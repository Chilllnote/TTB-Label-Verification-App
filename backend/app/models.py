"""Pydantic models for TTB Label Verification."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApplicationData(BaseModel):
    """Expected values from the application/submission."""
    
    model_config = ConfigDict(populate_by_name=True)

    brand: str
    product_class: str = Field(..., alias="class")
    producer: str
    country: str
    abv: str
    net_contents: str
    government_warning: str

    @field_validator(
        "brand",
        "product_class",
        "producer",
        "country",
        "abv",
        "net_contents",
        "government_warning",
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
