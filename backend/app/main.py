"""FastAPI application for TTB Label Verification."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

# Load environment variables from a .env file at the project root if present.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

from app.config import runtime_int
from app.comparison import verify_label
from app.models import (
    ApplicationData,
    BatchItemResult,
    BatchSummary,
    BatchVerificationResult,
    LatencyMetrics,
    VerificationResult,
)
from app.preprocessing import inspect_image, preprocess_image
from app.vision_service import (
    MockVisionService,
    OpenAIVisionService,
    UnavailableVisionService,
    VisionExtractionError,
    VisionService,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BATCH_SIZE = 5
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/heic",
    "image/heif",
}
SINGLE_LABEL_BUDGET_MS = 5000.0
APPLICATION_FIELD_LABELS = {
    "brand": "Brand",
    "class": "Class or Type",
    "product_class": "Class or Type",
    "producer": "Producer",
    "country": "Country",
    "abv": "Alcohol %",
    "net_contents": "Bottle Size",
    "government_warning": "Government Warning",
}

# Initialize FastAPI app
app = FastAPI(title="TTB Label Verification App")

# Dependency injection: VisionService instance
_vision_service: VisionService = None


def get_vision_service() -> VisionService:
    """Dependency that returns the active VisionService (Mock or OpenAI)."""
    return _vision_service


# Mount static files and frontend
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="frontend")


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request, exc):
    """Return plain user-safe messages for missing multipart/form fields."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": "Please choose a label photo and fill in all required fields."
        },
    )


@app.on_event("startup")
async def startup():
    """Initialize VisionService on app startup."""
    global _vision_service

    # Use mock if USE_MOCK_VISION env var is set to "true"
    if os.getenv("USE_MOCK_VISION", "").lower() == "true":
        logger.info("Using MockVisionService (no API calls)")
        _vision_service = MockVisionService()
    else:
        try:
            logger.info("Using OpenAIVisionService (real API)")
            _vision_service = OpenAIVisionService()
        except ValueError as e:
            logger.error("OpenAI initialization failed: %s", e)
            _vision_service = UnavailableVisionService(str(e))


@app.get("/")
async def root():
    """Serve frontend HTML."""
    return FileResponse(frontend_dir / "index.html")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return JSONResponse(status_code=200, content={"status": "ok"})


def _parse_application_data(raw_json: str) -> ApplicationData:
    if not raw_json or not raw_json.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="application_data must be a non-empty JSON string containing all required fields.",
        )

    try:
        return ApplicationData.model_validate_json(raw_json)
    except ValidationError as exc:
        field_errors = _validation_field_errors(exc)
        detail = (
            {
                "message": _validation_error_message(field_errors),
                "field_errors": field_errors,
            }
            if field_errors
            else "Invalid application_data JSON or missing required fields."
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid application_data JSON or missing required fields.",
        )


def _parse_batch_application_data(raw_json: str) -> list[ApplicationData]:
    if not raw_json or not raw_json.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="application_data must be a non-empty JSON array containing one data object per image.",
        )

    try:
        raw_items = json.loads(raw_json)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid application_data JSON or missing required fields.",
        )

    if not isinstance(raw_items, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="application_data must be a JSON array containing one data object per image.",
        )

    application_data_models = []
    field_errors = []
    for index, item in enumerate(raw_items):
        try:
            application_data_models.append(ApplicationData.model_validate(item))
        except ValidationError as exc:
            field_errors.extend(_validation_field_errors(exc, index=index))

    if field_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": _validation_error_message(field_errors),
                "field_errors": field_errors,
            },
        )

    return application_data_models


def _validation_field_errors(
    exc: ValidationError, index: int | None = None
) -> list[dict[str, object]]:
    field_errors = []
    for error in exc.errors():
        loc = [part for part in error.get("loc", ()) if isinstance(part, str)]
        if not loc:
            continue

        field = loc[-1]
        field_label = APPLICATION_FIELD_LABELS.get(
            field, field.replace("_", " ").title()
        )
        field_errors.append(
            {
                "field": field,
                "label": field_label,
                "message": str(error.get("msg", "Invalid value")),
                **({"index": index} if index is not None else {}),
            }
        )
    return field_errors


def _validation_error_message(field_errors: list[dict[str, object]]) -> str:
    if not field_errors:
        return "Please fix the highlighted fields."

    labels = []
    for error in field_errors:
        label = str(error.get("label") or error.get("field") or "Field")
        if error.get("index") is not None:
            label = f"Label {int(error['index']) + 1} {label}"
        if label not in labels:
            labels.append(label)

    return f"Please fix: {', '.join(labels)}."


def _preprocess_max_dimension() -> int:
    return runtime_int("PREPROCESS_MAX_DIMENSION", 320, 1600)


def _preprocess_jpeg_quality() -> int:
    return runtime_int("PREPROCESS_JPEG_QUALITY", 50, 90)


async def _verify_uploaded_label(
    image: UploadFile,
    application_data_model: ApplicationData,
    vision_service: VisionService,
) -> VerificationResult:
    start_time = time.perf_counter()
    metrics = LatencyMetrics(
        vision_service=vision_service.__class__.__name__,
        vision_model=getattr(vision_service, "model_name", None),
    )

    if image.content_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image format: {image.content_type}. Expected an image upload.",
        )

    read_start = time.perf_counter()
    image_bytes = await image.read()
    metrics.upload_read_ms = round((time.perf_counter() - read_start) * 1000, 1)
    metrics.original_bytes = len(image_bytes)

    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image upload is empty.",
        )

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image file too large. Maximum allowed size is 5 MB.",
        )

    validate_start = time.perf_counter()
    try:
        image_info = inspect_image(image_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    metrics.image_validate_ms = round((time.perf_counter() - validate_start) * 1000, 1)
    metrics.image_width = image_info.width
    metrics.image_height = image_info.height
    metrics.image_format = image_info.format

    preprocess_start = time.perf_counter()
    preprocessed_bytes = preprocess_image(
        image_bytes,
        max_dimension=_preprocess_max_dimension(),
        jpeg_quality=_preprocess_jpeg_quality(),
    )
    metrics.preprocess_ms = round((time.perf_counter() - preprocess_start) * 1000, 1)
    metrics.preprocessed_bytes = len(preprocessed_bytes)
    try:
        preprocessed_info = inspect_image(preprocessed_bytes)
        metrics.preprocessed_width = preprocessed_info.width
        metrics.preprocessed_height = preprocessed_info.height
        metrics.preprocessed_format = preprocessed_info.format
    except ValueError:
        logger.warning(
            "Preprocessed image could not be inspected; continuing with original validation result"
        )

    vision_start = time.perf_counter()
    try:
        extracted_label = await vision_service.extract(preprocessed_bytes)
    except VisionExtractionError as exc:
        metrics.vision_ms = round((time.perf_counter() - vision_start) * 1000, 1)
        logger.warning("Vision extraction failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We could not read this photo.",
        ) from exc
    metrics.vision_ms = round((time.perf_counter() - vision_start) * 1000, 1)

    compare_start = time.perf_counter()
    verification_result = verify_label(application_data_model, extracted_label)
    metrics.compare_ms = round((time.perf_counter() - compare_start) * 1000, 1)

    latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
    metrics.total_latency_ms = latency_ms
    verification_result.latency_ms = latency_ms
    verification_result.metrics = metrics

    logger.info(
        "Verification completed in %.1fms status=%s",
        latency_ms,
        verification_result.overall_verdict,
    )
    if latency_ms > SINGLE_LABEL_BUDGET_MS:
        logger.warning(
            "Verification latency exceeded 5 second budget: %.1fms", latency_ms
        )

    return verification_result


def _batch_concurrency_limit() -> int:
    return runtime_int("BATCH_CONCURRENCY", 1, MAX_BATCH_SIZE)


def _plain_item_error(exc: HTTPException) -> str:
    detail = str(exc.detail)
    lower_detail = detail.lower()
    if "invalid image format" in lower_detail:
        return "Please choose an image file."
    if "could not be read" in lower_detail or "could not read" in lower_detail:
        return "Please choose a clear image file."
    if "dimensions are too large" in lower_detail:
        return "Please choose a smaller photo."
    if "too large" in lower_detail:
        return "Please choose an image under 5 MB."
    if "empty" in lower_detail:
        return "The photo is empty. Please choose another label photo."
    return detail


async def _verify_batch_item(
    index: int,
    image: UploadFile,
    application_data_model: ApplicationData,
    vision_service: VisionService,
    semaphore: asyncio.Semaphore,
) -> BatchItemResult:
    async with semaphore:
        filename = image.filename or f"Label {index + 1}"
        try:
            result = await _verify_uploaded_label(
                image, application_data_model, vision_service
            )
            return BatchItemResult(
                index=index,
                filename=filename,
                status=result.overall_verdict,
                result=result,
                error=None,
            )
        except HTTPException as exc:
            return BatchItemResult(
                index=index,
                filename=filename,
                status="ERROR",
                result=None,
                error=_plain_item_error(exc),
            )
        except Exception:
            logger.exception("Batch item %s failed unexpectedly", index)
            return BatchItemResult(
                index=index,
                filename=filename,
                status="ERROR",
                result=None,
                error="Something went wrong while checking this label. Please try again.",
            )


@app.post("/verify", response_model=VerificationResult)
async def verify_endpoint(
    image: UploadFile = File(...),
    application_data: str = Form(...),
    vision_service: VisionService = Depends(get_vision_service),
) -> VerificationResult:
    """Verify a label image against expected application data."""
    application_data_model = _parse_application_data(application_data)
    return await _verify_uploaded_label(image, application_data_model, vision_service)


@app.post("/verify/batch", response_model=BatchVerificationResult)
async def verify_batch_endpoint(
    images: list[UploadFile] = File(...),
    application_data: str = Form(...),
    vision_service: VisionService = Depends(get_vision_service),
) -> BatchVerificationResult:
    """Verify multiple label images against expected application data."""
    start_time = time.perf_counter()

    if not images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one label image is required.",
        )

    if len(images) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch size too large. Maximum allowed size is {MAX_BATCH_SIZE} labels.",
        )

    application_data_models = _parse_batch_application_data(application_data)

    if len(images) != len(application_data_models):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Each label needs one image and one set of expected values.",
        )

    semaphore = asyncio.Semaphore(_batch_concurrency_limit())
    tasks = [
        _verify_batch_item(index, image, data, vision_service, semaphore)
        for index, (image, data) in enumerate(zip(images, application_data_models))
    ]
    item_results = await asyncio.gather(*tasks)

    passed = sum(1 for item in item_results if item.status == "APPROVED")
    needs_review = sum(1 for item in item_results if item.status == "NEEDS_REVIEW")
    errors = sum(1 for item in item_results if item.status == "ERROR")
    latency_ms = round((time.perf_counter() - start_time) * 1000, 1)

    logger.info(
        "Batch verification completed in %.1fms total=%s passed=%s needs_review=%s errors=%s",
        latency_ms,
        len(item_results),
        passed,
        needs_review,
        errors,
    )
    if latency_ms > 5000:
        logger.warning(
            "Batch verification latency exceeded 5 second budget: %.1fms", latency_ms
        )

    return BatchVerificationResult(
        summary=BatchSummary(
            total=len(item_results),
            passed=passed,
            needs_review=needs_review,
            errors=errors,
        ),
        results=item_results,
        latency_ms=latency_ms,
    )
