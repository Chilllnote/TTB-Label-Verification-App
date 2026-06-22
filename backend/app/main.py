"""FastAPI application for TTB Label Verification."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.comparison import verify_label
from app.models import (
    ApplicationData,
    BatchItemResult,
    BatchSummary,
    BatchVerificationResult,
    VerificationResult,
)
from app.preprocessing import preprocess_image
from app.vision_service import MockVisionService, OpenAIVisionService, VisionService

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BATCH_SIZE = 5
DEFAULT_BATCH_CONCURRENCY = 3
ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png")

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
            logger.warning(f"OpenAI initialization failed: {e}. Falling back to Mock.")
            _vision_service = MockVisionService()


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
    except (ValidationError, json.JSONDecodeError):
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

    try:
        return [ApplicationData.model_validate(item) for item in raw_items]
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid application_data JSON or missing required fields.",
        )


async def _verify_uploaded_label(
    image: UploadFile,
    application_data_model: ApplicationData,
    vision_service: VisionService,
) -> VerificationResult:
    start_time = time.perf_counter()

    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image format: {image.content_type}. Expected JPEG or PNG.",
        )

    image_bytes = await image.read()
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

    preprocessed_bytes = preprocess_image(image_bytes)
    extracted_label = await vision_service.extract(preprocessed_bytes)
    verification_result = verify_label(application_data_model, extracted_label)

    latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
    verification_result.latency_ms = latency_ms

    logger.info(
        "Verification completed in %.1fms status=%s",
        latency_ms,
        verification_result.overall_status,
    )
    if latency_ms > 5000:
        logger.warning("Verification latency exceeded 5 second budget: %.1fms", latency_ms)

    return verification_result


def _batch_concurrency_limit() -> int:
    raw_value = os.getenv("BATCH_CONCURRENCY", str(DEFAULT_BATCH_CONCURRENCY))
    try:
        configured = int(raw_value)
    except ValueError:
        configured = DEFAULT_BATCH_CONCURRENCY
    return min(MAX_BATCH_SIZE, max(1, configured))


def _plain_item_error(exc: HTTPException) -> str:
    detail = str(exc.detail)
    lower_detail = detail.lower()
    if "invalid image format" in lower_detail:
        return "Please choose a JPG or PNG image."
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
            result = await _verify_uploaded_label(image, application_data_model, vision_service)
            return BatchItemResult(
                index=index,
                filename=filename,
                status=result.overall_status,
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

    passed = sum(1 for item in item_results if item.status == "PASS")
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
        logger.warning("Batch verification latency exceeded 5 second budget: %.1fms", latency_ms)

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
