"""Pydantic models for TTB Label Verification."""

import re
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


APPLICATION_ID_PATTERN = re.compile(r"^APP-[0-9A-F]{8}$")


class ApplicationData(BaseModel):
    """Expected values from the application/submission."""

    model_config = ConfigDict(populate_by_name=True)

    brand_name: str
    class_type_designation: str
    alcohol_content: str
    net_contents: str
    bottler_producer_name_address: str
    country_of_origin: str
    government_health_warning_statement: str

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_keys(cls, value):
        """Accept the old UI/API keys while exposing the application-package shape."""
        if not isinstance(value, dict):
            return value

        aliases = {
            "brand": "brand_name",
            "class": "class_type_designation",
            "product_class": "class_type_designation",
            "abv": "alcohol_content",
            "producer": "bottler_producer_name_address",
            "country": "country_of_origin",
            "government_warning": "government_health_warning_statement",
        }
        normalized = dict(value)
        for old_key, new_key in aliases.items():
            if new_key not in normalized and old_key in normalized:
                normalized[new_key] = normalized[old_key]
        return normalized

    @field_validator(
        "brand_name",
        "class_type_designation",
        "alcohol_content",
        "net_contents",
        "bottler_producer_name_address",
        "country_of_origin",
        "government_health_warning_statement",
        mode="before",
    )
    @classmethod
    def require_non_blank_string(cls, value):
        if value is None:
            raise ValueError("Field is required")

        if not isinstance(value, str):
            raise ValueError("Field must be text")

        value = value.strip()
        if not value:
            raise ValueError("Field cannot be blank")

        return value

    @property
    def brand(self) -> str:
        return self.brand_name

    @property
    def product_class(self) -> str:
        return self.class_type_designation

    @property
    def producer(self) -> str:
        return self.bottler_producer_name_address

    @property
    def country(self) -> str:
        return self.country_of_origin

    @property
    def abv(self) -> str:
        return self.alcohol_content

    @property
    def government_warning(self) -> str:
        return self.government_health_warning_statement


class ExtractedLabel(BaseModel):
    """Values extracted from the label (OCR/vision pipeline).
    
    All fields are Optional to handle cases where vision service
    cannot reliably extract a field (blurry, non-label image, etc.).
    """
    
    model_config = ConfigDict(populate_by_name=True)

    brand: Optional[str] = None
    product_class: Optional[str] = Field(None, alias="class")
    producer: Optional[str] = None
    country: Optional[str] = None
    abv: Optional[str] = None
    net_contents: Optional[str] = None
    government_warning: Optional[str] = None
    raw_text: Optional[str] = None
    extraction_confidence: Optional[float] = None


class FieldResult(BaseModel):
    """Result of comparing a single field."""

    field: str
    expected: str
    found: str
    status: Literal["PASS", "FAIL"]
    score: Optional[float] = None
    message: str


class LatencyMetrics(BaseModel):
    """Internal timing and sizing measurements for one label verification."""

    upload_read_ms: float = 0.0
    image_validate_ms: float = 0.0
    preprocess_ms: float = 0.0
    vision_ms: float = 0.0
    compare_ms: float = 0.0
    total_latency_ms: float = 0.0
    original_bytes: int = 0
    preprocessed_bytes: int = 0
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    image_format: Optional[str] = None
    preprocessed_width: Optional[int] = None
    preprocessed_height: Optional[int] = None
    preprocessed_format: Optional[str] = None
    vision_service: str
    vision_model: Optional[str] = None


class VerificationResult(BaseModel):
    """Overall verification result."""

    field_results: list[FieldResult]
    overall_verdict: Literal["APPROVED", "NEEDS_REVIEW"]
    summary: str
    failed_fields: Optional[list[str]] = None
    latency_ms: float
    metrics: Optional[LatencyMetrics] = None


class BatchSummary(BaseModel):
    """Summary counts for a batch verification run."""

    total: int
    passed: int
    needs_review: int
    errors: int


class BatchItemResult(BaseModel):
    """Result for one item in a batch verification run."""

    index: int
    filename: str
    status: Literal["APPROVED", "NEEDS_REVIEW", "ERROR"]
    result: Optional[VerificationResult] = None
    error: Optional[str] = None


class BatchVerificationResult(BaseModel):
    """Overall response for batch verification."""

    summary: BatchSummary
    results: list[BatchItemResult]
    latency_ms: float


class ApplicationPackage(BaseModel):
    """One submitted application package for matching."""

    application_id: str
    image_filename: str
    application_data: ApplicationData

    @model_validator(mode="before")
    @classmethod
    def create_application_id(cls, value):
        if isinstance(value, dict) and not str(value.get("application_id") or "").strip():
            value = dict(value)
            value["application_id"] = f"APP-{uuid4().hex[:8].upper()}"
        return value

    @field_validator("application_id", mode="before")
    @classmethod
    def require_application_id_shape(cls, value):
        if value is None:
            raise ValueError("Field is required")
        if not isinstance(value, str):
            raise ValueError("Field must be text")
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be blank")
        if not APPLICATION_ID_PATTERN.fullmatch(value):
            raise ValueError("Application ID must look like APP-1B81036D")
        return value

    @field_validator("image_filename", mode="before")
    @classmethod
    def require_non_blank_image_filename(cls, value):
        if value is None:
            raise ValueError("Field is required")
        if not isinstance(value, str):
            raise ValueError("Field must be text")
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be blank")
        return value


class ApplicationRecord(BaseModel):
    """In-memory application record shown in the table/detail views."""

    application_id: str
    image_filename: str
    status: Literal["PENDING", "ACCEPTED", "NEEDS_CHECK", "REJECTED", "ERROR"]
    application_data: ApplicationData
    extracted_data: Optional[ExtractedLabel] = None
    verification_result: Optional[VerificationResult] = None
    match_percentage: Optional[int] = None
    error: Optional[str] = None
    checked_at: Optional[str] = None
