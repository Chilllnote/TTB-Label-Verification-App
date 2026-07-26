"""FastAPI application for TTB Label Verification."""

import asyncio
import json
import logging
import mimetypes
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

# Load environment variables from a .env file at the project root if present.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

from app.config import runtime_bool, runtime_choice, runtime_int
from app.comparison import verify_label
from app.models import (
    ApplicationData,
    ApplicationPackage,
    ApplicationRecord,
    BatchItemResult,
    BatchSummary,
    BatchVerificationResult,
    FieldResult,
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
APPLICATION_FIELD_LABELS = {
    "brand_name": "Brand",
    "class_type_designation": "Class/Type Designation",
    "alcohol_content": "Alcohol %",
    "bottler_producer_name_address": "Name and Address of Bottler/Producer",
    "country_of_origin": "Country of Origin",
    "government_health_warning_statement": "Government Health Warning Statement",
    "brand": "Brand",
    "class": "Class or Type",
    "product_class": "Class or Type",
    "producer": "Producer",
    "country": "Country",
    "abv": "Alcohol %",
    "net_contents": "Bottle Size",
    "government_warning": "Government Warning",
}
LEGACY_ERROR_FIELD_NAMES = {
    "brand_name": "brand",
    "class_type_designation": "product_class",
    "alcohol_content": "abv",
    "bottler_producer_name_address": "producer",
    "country_of_origin": "country",
    "government_health_warning_statement": "government_warning",
}
APPLICATION_IMAGE_DIR = Path(__file__).resolve().parents[1] / "scripts"
MOCK_APPLICATION_DIR = Path(__file__).resolve().parent / "mock_applications"
MOCK_APPLICATION_IMAGE_DIR = MOCK_APPLICATION_DIR / "images"
MOCK_APPLICATION_FILE = MOCK_APPLICATION_DIR / "applications.json"

_application_records: dict[str, ApplicationRecord] = {}
_uploaded_image_bytes: dict[str, tuple[bytes, str]] = {}

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


class BytesUpload:
    """Small UploadFile-compatible wrapper for in-memory/database images."""

    def __init__(self, filename: str, content: bytes, content_type: str):
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


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
    _ensure_mock_application_records()


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
                "field": LEGACY_ERROR_FIELD_NAMES.get(field, field),
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


def _preprocess_grayscale() -> bool:
    return runtime_bool("PREPROCESS_GRAYSCALE")


def _preprocess_threshold() -> str:
    return runtime_choice("PREPROCESS_THRESHOLD", {"off", "binary", "adaptive"})


def _preprocess_contrast() -> bool:
    return runtime_bool("PREPROCESS_CONTRAST")


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
        grayscale=_preprocess_grayscale(),
        threshold_mode=_preprocess_threshold(),
        enhance_contrast=_preprocess_contrast(),
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_status(result: VerificationResult) -> str:
    if result.overall_verdict == "APPROVED":
        return "ACCEPTED"
    failed = sum(1 for field in result.field_results if field.status == "FAIL")
    return "NEEDS_CHECK" if failed == 1 else "REJECTED"


def _match_percentage(result: VerificationResult) -> int:
    total = len(result.field_results)
    if not total:
        return 0
    passed = sum(1 for field in result.field_results if field.status == "PASS")
    return round((passed / total) * 100)


def _content_type_for_filename(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "image/jpeg"


def _store_record(record: ApplicationRecord) -> ApplicationRecord:
    _application_records[record.application_id] = record
    return record


def _store_uploaded_image(filename: str, content: bytes, content_type: str) -> None:
    _uploaded_image_bytes[filename] = (content, content_type)


def _record_from_verification_result(
    package: ApplicationPackage,
    result: VerificationResult,
) -> ApplicationRecord:
    return ApplicationRecord(
        application_id=package.application_id,
        image_filename=package.image_filename,
        status=_record_status(result),
        application_data=package.application_data,
        verification_result=result,
        match_percentage=_match_percentage(result),
        checked_at=_now_iso(),
    )


def _failed_field_details(result: VerificationResult) -> list[dict[str, str]]:
    return [
        {
            "field": field_result.field,
            "label": APPLICATION_FIELD_LABELS.get(
                field_result.field,
                field_result.field.replace("_", " ").title(),
            ),
            "expected": field_result.expected,
            "found": field_result.found,
            "message": field_result.message,
        }
        for field_result in result.field_results
        if field_result.status == "FAIL"
    ]


def _upload_failure_detail(
    package: ApplicationPackage,
    *,
    result: VerificationResult | None = None,
    error: str | None = None,
) -> dict[str, object]:
    failed_fields = _failed_field_details(result) if result else []
    status_label = _record_status(result) if result else "ERROR"
    reason = error
    if not reason and failed_fields:
        names = ", ".join(str(field["label"]) for field in failed_fields)
        reason = f"Fields did not match: {names}."
    if not reason:
        reason = "The application could not be processed."

    return {
        "application_id": package.application_id,
        "image_filename": package.image_filename,
        "status": status_label,
        "match_percentage": _match_percentage(result) if result else None,
        "failed_fields": failed_fields,
        "error": reason,
    }


def _upload_failed_response(failures: list[dict[str, object]]) -> HTTPException:
    count = len(failures)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "message": (
                "Upload failed. No applications were added to the table."
                if count != 1
                else f"Upload failed for {failures[0]['application_id']}. The application was not added to the table."
            ),
            "upload_failures": failures,
        },
    )


async def _build_verified_application_record(
    package: ApplicationPackage,
    image: UploadFile,
    vision_service: VisionService,
) -> ApplicationRecord:
    try:
        result = await _verify_uploaded_label(
            image,
            package.application_data,
            vision_service,
        )
    except HTTPException as exc:
        raise _upload_failed_response(
            [_upload_failure_detail(package, error=_plain_item_error(exc))]
        ) from exc

    return _record_from_verification_result(package, result)


def _lookup_application_image(filename: str) -> BytesUpload:
    if filename in _uploaded_image_bytes:
        content, content_type = _uploaded_image_bytes[filename]
        return BytesUpload(filename, content, content_type)

    safe_filename = Path(filename).name
    candidate = next(
        (
            path
            for path in [
                MOCK_APPLICATION_IMAGE_DIR / safe_filename,
                APPLICATION_IMAGE_DIR / safe_filename,
            ]
            if path.is_file()
        ),
        None,
    )
    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image '{filename}' was not found in the application image store.",
        )

    return BytesUpload(
        safe_filename,
        candidate.read_bytes(),
        _content_type_for_filename(safe_filename),
    )


def _load_mock_application_packages() -> list[ApplicationPackage]:
    if not MOCK_APPLICATION_FILE.is_file():
        return []

    try:
        raw_items = json.loads(MOCK_APPLICATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not load mock application packages")
        return []

    packages = []
    for item in raw_items:
        try:
            packages.append(ApplicationPackage.model_validate(item))
        except ValidationError:
            logger.exception("Invalid mock application package")
    return packages


def _mock_field_result(field: str, expected: str, found: str, passed: bool) -> FieldResult:
    return FieldResult(
        field=field,
        expected=expected,
        found=found,
        status="PASS" if passed else "FAIL",
        score=100.0 if passed else 0.0,
        message="Mock application field matched." if passed else "Mock application field needs review.",
    )


def _mock_verification_result(
    package: ApplicationPackage,
    found_values: dict[str, str],
) -> VerificationResult:
    app_data = package.application_data
    expected_values = {
        "brand": app_data.brand,
        "product_class": app_data.product_class,
        "abv": app_data.abv,
        "net_contents": app_data.net_contents,
        "producer": app_data.producer,
        "country": app_data.country,
        "government_warning": app_data.government_warning,
    }
    field_results = [
        _mock_field_result(field, expected, found_values.get(field, ""), expected == found_values.get(field, ""))
        for field, expected in expected_values.items()
    ]
    failed = [result.field for result in field_results if result.status == "FAIL"]
    return VerificationResult(
        field_results=field_results,
        overall_verdict="NEEDS_REVIEW" if failed else "APPROVED",
        summary="Mock application display record.",
        failed_fields=failed or None,
        latency_ms=0.0,
    )


def _mock_record_for_package(package: ApplicationPackage) -> ApplicationRecord:
    app_data = package.application_data
    found_values = {
        "brand": app_data.brand,
        "product_class": app_data.product_class,
        "abv": app_data.abv,
        "net_contents": app_data.net_contents,
        "producer": app_data.producer,
        "country": app_data.country,
        "government_warning": app_data.government_warning,
    }

    if package.application_id == "APP-A0000002":
        found_values["net_contents"] = "12 fl oz"
    elif package.application_id == "APP-A0000003":
        found_values.update(
            {
                "brand": "Midnight Cactus",
                "product_class": "Tequila",
                "abv": "38%",
                "net_contents": "375 ml",
                "producer": "Desert Sun Spirits, Jalisco, Mexico",
                "country": "Mexico",
                "government_warning": "Warning: Contains Alcohol",
            }
        )

    result = _mock_verification_result(package, found_values)
    return ApplicationRecord(
        application_id=package.application_id,
        image_filename=package.image_filename,
        status=_record_status(result),
        application_data=package.application_data,
        verification_result=result,
        match_percentage=_match_percentage(result),
        checked_at=_now_iso(),
    )


def _ensure_mock_application_records() -> None:
    for package in _load_mock_application_packages():
        if package.application_id in _application_records:
            continue
        _store_record(_mock_record_for_package(package))


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


@app.get("/applications", response_model=list[ApplicationRecord])
async def list_applications() -> list[ApplicationRecord]:
    """Return the in-memory application table."""
    _ensure_mock_application_records()
    return list(_application_records.values())


@app.get("/applications/{application_id}", response_model=ApplicationRecord)
async def get_application(application_id: str) -> ApplicationRecord:
    """Return one detailed in-memory application record."""
    _ensure_mock_application_records()
    record = _application_records.get(application_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application was not found.",
        )
    return record


@app.get("/applications/images/{filename}")
async def get_application_image(filename: str):
    """Serve a stored application label image for detail view previews."""
    safe_filename = Path(filename).name
    if safe_filename in _uploaded_image_bytes:
        image_bytes, content_type = _uploaded_image_bytes[safe_filename]
        return Response(content=image_bytes, media_type=content_type)

    for directory in [MOCK_APPLICATION_IMAGE_DIR, APPLICATION_IMAGE_DIR]:
        candidate = directory / safe_filename
        if candidate.is_file():
            return FileResponse(
                candidate,
                media_type=_content_type_for_filename(safe_filename),
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Application image was not found.",
    )


@app.patch("/applications/{application_id}/status", response_model=ApplicationRecord)
async def update_application_status_endpoint(
    application_id: str,
    status_update: dict[str, str],
) -> ApplicationRecord:
    """Manually set an application status from the detail view."""
    _ensure_mock_application_records()
    record = _application_records.get(application_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application was not found.",
        )

    new_status = str(status_update.get("status", "")).strip().upper()
    if new_status not in {"ACCEPTED", "REJECTED"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status can only be changed to ACCEPTED or REJECTED.",
        )

    updated = record.model_copy(update={"status": new_status})
    return _store_record(updated)


@app.post("/applications/upload", response_model=ApplicationRecord)
async def upload_application_endpoint(
    image: UploadFile = File(...),
    application: str = Form(...),
    vision_service: VisionService = Depends(get_vision_service),
) -> ApplicationRecord:
    """Create and verify one application package with an uploaded image."""
    image_bytes = await image.read()
    image_filename = image.filename or f"uploaded-{int(time.time())}.jpg"
    image_content_type = image.content_type or _content_type_for_filename(image_filename)
    try:
        raw_package = json.loads(application)
        if "application_data" not in raw_package:
            raw_package = {
                **(
                    {"application_id": raw_package["application_id"]}
                    if raw_package.get("application_id")
                    else {}
                ),
                "image_filename": raw_package.get("image_filename", image_filename),
                "application_data": raw_package,
            }
        raw_package["image_filename"] = raw_package.get("image_filename") or image_filename
        package = ApplicationPackage.model_validate(raw_package)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid application JSON: {exc}",
        )

    upload = BytesUpload(package.image_filename, image_bytes, image_content_type)
    record = await _build_verified_application_record(package, upload, vision_service)
    _store_uploaded_image(package.image_filename, image_bytes, image_content_type)
    return _store_record(record)


@app.post("/applications/upload-json", response_model=ApplicationRecord)
async def upload_application_json_endpoint(
    application_file: UploadFile = File(...),
    vision_service: VisionService = Depends(get_vision_service),
) -> ApplicationRecord:
    """Create and verify one application package from JSON only."""
    raw_bytes = await application_file.read()
    try:
        raw_package = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application upload must be one JSON application package.",
        )

    if not isinstance(raw_package, dict) or isinstance(raw_package, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application upload must be one JSON application package.",
        )

    try:
        package = ApplicationPackage.model_validate(raw_package)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid application package: {exc}",
        )

    try:
        image = _lookup_application_image(package.image_filename)
        record = await _build_verified_application_record(package, image, vision_service)
        return _store_record(record)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("upload_failures"):
            raise exc
        raise _upload_failed_response(
            [_upload_failure_detail(package, error=_plain_item_error(exc))]
        ) from exc


@app.post("/applications/batch", response_model=list[ApplicationRecord])
async def upload_application_batch_endpoint(
    application_file: UploadFile = File(...),
    vision_service: VisionService = Depends(get_vision_service),
) -> list[ApplicationRecord]:
    """Verify application packages whose images already exist by filename."""
    raw_bytes = await application_file.read()
    try:
        raw_items = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch upload must be a JSON array of application packages.",
        )

    if not isinstance(raw_items, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch upload must be a JSON array of application packages.",
        )

    if len(raw_items) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch size too large. Maximum allowed size is {MAX_BATCH_SIZE} applications.",
        )

    packages = []
    for item in raw_items:
        try:
            packages.append(ApplicationPackage.model_validate(item))
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid application package: {exc}",
            )

    verified_records = []
    failures = []
    for package in packages:
        try:
            image = _lookup_application_image(package.image_filename)
            verified_records.append(
                await _build_verified_application_record(package, image, vision_service)
            )
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("upload_failures"):
                failures.extend(exc.detail["upload_failures"])
            else:
                failures.append(
                    _upload_failure_detail(package, error=_plain_item_error(exc))
                )

    if failures:
        raise _upload_failed_response(failures)

    return [_store_record(record) for record in verified_records]
