"""FastAPI application for TTB Label Verification."""

import json
import logging
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.comparison import verify_label, aggregate_verification
from app.models import ApplicationData, VerificationResult
from app.preprocessing import preprocess_image
from app.vision_service import MockVisionService, OpenAIVisionService, VisionService

logger = logging.getLogger(__name__)

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


@app.post("/verify", response_model=VerificationResult)
async def verify_endpoint(
    image: UploadFile = File(...),
    application_data: str = Form(...),
    vision_service: VisionService = Depends(get_vision_service),
) -> VerificationResult:
    """Verify a label image against expected application data.

    Args:
        image: Label image file (JPEG/PNG, <5MB)
        application_data: Expected label data as JSON string in multipart form
        vision_service: Injected VisionService (Mock or OpenAI)

    Returns:
        VerificationResult with field-by-field comparison, latency, and overall verdict
    """
    start_time = time.perf_counter()

    if image.content_type not in ("image/jpeg", "image/png"):
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

    if len(image_bytes) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image file too large. Maximum allowed size is 5 MB.",
        )

    application_data_model = _parse_application_data(application_data)

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
