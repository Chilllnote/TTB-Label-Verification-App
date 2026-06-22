"""Pydantic models for TTB Label Verification."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


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


class FieldResult(BaseModel):
    """Result of comparing a single field."""

    field_name: str
    expected: str
    extracted: str
    status: Literal["PASS", "FAIL"]
    score: Optional[float] = None
    message: str


class VerificationResult(BaseModel):
    """Overall verification result."""

    field_results: list[FieldResult]
    overall_status: Literal["PASS", "NEEDS_REVIEW"]
    summary: str
    failed_fields: Optional[list[str]] = None
    latency_ms: float


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
    status: Literal["PASS", "NEEDS_REVIEW", "ERROR"]
    result: Optional[VerificationResult] = None
    error: Optional[str] = None


class BatchVerificationResult(BaseModel):
    """Overall response for batch verification."""

    summary: BatchSummary
    results: list[BatchItemResult]
    latency_ms: float
