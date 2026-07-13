"""Pure comparison functions for TTB Label Verification."""

import re
from typing import Optional

from rapidfuzz import fuzz

from app.models import FieldResult, VerificationResult


def compare_brand(expected: str, extracted: str) -> FieldResult:
    """Compare brand names using fuzzy matching.
    
    Args:
        expected: expected brand name
        extracted: extracted brand name from label
        
    Returns:
        FieldResult with PASS/FAIL status and fuzzy match score
    """
    if not expected or not extracted:
        return FieldResult(
            field="brand",
            expected=expected or "",
            found=extracted or "",
            status="FAIL",
            score=0.0,
            message="Missing brand name",
        )

    # Use token_set_ratio for case-insensitive, word-order-tolerant matching
    # Convert to lowercase first for consistent case-insensitive comparison
    score = fuzz.token_set_ratio(expected.lower(), extracted.lower())
    threshold = 90

    if score >= threshold:
        return FieldResult(
            field="brand",
            expected=expected,
            found=extracted,
            status="PASS",
            score=float(score),
            message=f"Brand match: fuzzy score {score:.1f}%",
        )
    else:
        return FieldResult(
            field="brand",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=float(score),
            message=f"Brand mismatch: fuzzy score {score:.1f}% below threshold {threshold}%",
        )


def compare_product_class(expected: str, extracted: str) -> FieldResult:
    """Compare product class using fuzzy matching.
    
    Args:
        expected: expected product class
        extracted: extracted product class from label
        
    Returns:
        FieldResult with PASS/FAIL status
    """
    if not expected or not extracted:
        return FieldResult(
            field="product_class",
            expected=expected or "",
            found=extracted or "",
            status="FAIL",
            score=0.0,
            message="Missing product class",
        )

    score = fuzz.token_set_ratio(expected.lower(), extracted.lower())
    threshold = 90

    if score >= threshold:
        return FieldResult(
            field="product_class",
            expected=expected,
            found=extracted,
            status="PASS",
            score=float(score),
            message=f"Product class match: fuzzy score {score:.1f}%",
        )
    else:
        return FieldResult(
            field="product_class",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=float(score),
            message=f"Product class mismatch: fuzzy score {score:.1f}% below threshold {threshold}%",
        )


def compare_producer(expected: str, extracted: str) -> FieldResult:
    """Compare producer using fuzzy matching.
    
    Args:
        expected: expected producer name
        extracted: extracted producer from label
        
    Returns:
        FieldResult with PASS/FAIL status
    """
    if not expected or not extracted:
        return FieldResult(
            field="producer",
            expected=expected or "",
            found=extracted or "",
            status="FAIL",
            score=0.0,
            message="Missing producer",
        )

    score = fuzz.token_set_ratio(expected.lower(), extracted.lower())
    threshold = 90

    if score >= threshold:
        return FieldResult(
            field="producer",
            expected=expected,
            found=extracted,
            status="PASS",
            score=float(score),
            message=f"Producer match: fuzzy score {score:.1f}%",
        )
    else:
        return FieldResult(
            field="producer",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=float(score),
            message=f"Producer mismatch: fuzzy score {score:.1f}% below threshold {threshold}%",
        )


def compare_country(expected: str, extracted: str) -> FieldResult:
    """Compare country with synonym normalization.
    
    Args:
        expected: expected country
        extracted: extracted country from label
        
    Returns:
        FieldResult with PASS/FAIL status
    """
    if not expected or not extracted:
        return FieldResult(
            field="country",
            expected=expected or "",
            found=extracted or "",
            status="FAIL",
            score=None,
            message="Missing country",
        )

    # Country synonyms dictionary
    country_aliases = {
        "usa": "united states",
        "united states": "united states",
        "united states of america": "united states",
        "us": "united states",
        "u.s.": "united states",
        "u.s.a.": "united states",
        "america": "united states",
        "uk": "united kingdom",
        "united kingdom": "united kingdom",
        "england": "united kingdom",
        "great britain": "united kingdom",
        "russia": "russian federation",
        "russian federation": "russian federation",
        "ussr": "russian federation",
        "soviet union": "russian federation",
        "czechia": "czech republic",
        "czech republic": "czech republic",
        "france": "france",
        "french republic": "france",
        "italy": "italy",
        "italia": "italy",
        "italian republic": "italy",
        "spain": "spain",
        "espana": "spain",
        "kingdom of spain": "spain",
        "germany": "germany",
        "deutschland": "germany",
        "federal republic of germany": "germany",
        "portugal": "portugal",
        "portuguese republic": "portugal",
        "australia": "australia",
        "commonwealth of australia": "australia",
    }

    # Normalize to canonical form
    expected_normalized = country_aliases.get(expected.lower().strip(), expected.lower().strip())
    extracted_normalized = country_aliases.get(extracted.lower().strip(), extracted.lower().strip())

    if expected_normalized == extracted_normalized:
        return FieldResult(
            field="country",
            expected=expected,
            found=extracted,
            status="PASS",
            score=None,
            message=f"Country match: {expected_normalized}",
        )
    else:
        return FieldResult(
            field="country",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=None,
            message=f"Country mismatch: {expected_normalized} vs {extracted_normalized}",
        )


def compare_abv(expected: str, extracted: str) -> FieldResult:
    """Compare ABV with numeric normalization and tolerance.
    
    Args:
        expected: expected ABV (e.g., "45%" or "45% vol")
        extracted: extracted ABV from label
        
    Returns:
        FieldResult with PASS/FAIL status and numeric score
    """
    if not expected or not extracted:
        return FieldResult(
            field="abv",
            expected=expected or "",
            found=extracted or "",
            status="FAIL",
            score=None,
            message="Missing ABV value",
        )

    # Extract numeric value from strings like "45%", "45% vol", "45% Alc./Vol. (90 Proof)"
    def extract_abv_value(value: str) -> Optional[float]:
        """Extract numeric ABV percentage from complex string."""
        # Match patterns like "45", "45.0", "45.5" at start or after certain delimiters
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
        if match:
            return float(match.group(1))
        # Also try matching just a number if % is present but separated
        match = re.search(r"(\d+(?:\.\d+)?)", value)
        if match and "%" in value:
            return float(match.group(1))
        match = re.search(r"(\d+(?:\.\d+)?)\s*proof\b", value, flags=re.IGNORECASE)
        if match:
            return float(match.group(1)) / 2
        return None

    expected_val = extract_abv_value(expected)
    extracted_val = extract_abv_value(extracted)

    if expected_val is None or extracted_val is None:
        return FieldResult(
            field="abv",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=None,
            message=f"Could not extract numeric ABV values",
        )

    # Compare with tolerance of ±0.1% (with small epsilon for floating point precision)
    tolerance = 0.1
    epsilon = 1e-6
    difference = abs(expected_val - extracted_val)

    if difference <= (tolerance + epsilon):
        score = max(0, 100 - (difference / tolerance * 15))  # Scale to 0-100
        return FieldResult(
            field="abv",
            expected=expected,
            found=extracted,
            status="PASS",
            score=float(score),
            message=f"ABV match: {expected_val}% vs {extracted_val}% (difference: {difference:.2f}%)",
        )
    else:
        score = max(0, 100 - (difference / tolerance * 100))
        return FieldResult(
            field="abv",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=float(score),
            message=f"ABV mismatch: {expected_val}% vs {extracted_val}% (difference: {difference:.2f}% exceeds ±{tolerance}%)",
        )


def compare_net_contents(expected: str, extracted: str) -> FieldResult:
    """Compare net contents with unit normalization to milliliters.
    
    Args:
        expected: expected net contents (e.g., "750 ml", "0.75 L")
        extracted: extracted net contents from label
        
    Returns:
        FieldResult with PASS/FAIL status
    """
    if not expected or not extracted:
        return FieldResult(
            field="net_contents",
            expected=expected or "",
            found=extracted or "",
            status="FAIL",
            score=None,
            message="Missing net contents value",
        )

    def normalize_to_ml(value: str) -> Optional[float]:
        """Convert volume to milliliters."""
        # Remove extra whitespace
        value = value.strip().lower()
        
        # Try to extract numeric value and unit
        # Match patterns like "750 ml", "0.75L", "25fl oz", etc.
        # Use a more flexible pattern that handles multi-word units
        match = re.match(r"(\d+(?:\.\d+)?)\s*(.*)$", value)
        if not match:
            return None

        numeric_val = float(match.group(1))
        unit = match.group(2).strip() if match.group(2) else ""

        # Normalize units to ml
        if unit in ["ml", "milliliter", "milliliters", ""]:
            # Empty unit or ml defaults to ml
            return numeric_val
        elif unit in ["l", "liter", "liters", "litre", "litres"]:
            return numeric_val * 1000
        elif unit in ["cl", "centiliter", "centiliters", "centilitre", "centilitres"]:
            return numeric_val * 10
        elif unit in ["fl oz", "floz", "fl. oz.", "fl.oz.", "fluid ounce", "fluid ounces"]:
            # 1 fl oz ≈ 29.5735 ml
            return numeric_val * 29.5735
        else:
            # Unrecognized unit
            return None

    expected_ml = normalize_to_ml(expected)
    extracted_ml = normalize_to_ml(extracted)

    if expected_ml is None or extracted_ml is None:
        return FieldResult(
            field="net_contents",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=None,
            message="Could not parse net contents values",
        )

    # Compare with tolerance: ±5 ml or ±2%, whichever is stricter
    difference = abs(expected_ml - extracted_ml)
    tolerance_ml = 5
    tolerance_percent = expected_ml * 0.02

    effective_tolerance = min(tolerance_ml, tolerance_percent)

    if difference <= effective_tolerance:
        score = max(0, 100 - (difference / effective_tolerance * 20))
        return FieldResult(
            field="net_contents",
            expected=expected,
            found=extracted,
            status="PASS",
            score=float(score),
            message=f"Net contents match: {expected_ml:.1f} ml vs {extracted_ml:.1f} ml (difference: {difference:.1f} ml)",
        )
    else:
        score = max(0, 100 - (difference / effective_tolerance * 100))
        return FieldResult(
            field="net_contents",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=float(score),
            message=f"Net contents mismatch: {expected_ml:.1f} ml vs {extracted_ml:.1f} ml (difference: {difference:.1f} ml exceeds tolerance)",
        )


def compare_government_warning(expected: str, extracted: Optional[str]) -> FieldResult:
    """Compare government warning with strict case-sensitive exact matching.
    
    Args:
        expected: expected government warning text
        extracted: extracted government warning from label
        
    Returns:
        FieldResult with PASS/FAIL status (exact case-sensitive match required)
    """
    if not expected:
        return FieldResult(
            field="government_warning",
            expected=expected or "",
            found=extracted or "",
            status="FAIL",
            score=None,
            message="Missing expected government warning",
        )

    if extracted is None or extracted == "":
        return FieldResult(
            field="government_warning",
            expected=expected,
            found=extracted or "",
            status="FAIL",
            score=None,
            message="Government warning is missing from extracted label",
        )

    # Exact case-sensitive comparison
    if expected == extracted:
        return FieldResult(
            field="government_warning",
            expected=expected,
            found=extracted,
            status="PASS",
            score=None,
            message="Government warning matches exactly (case-sensitive)",
        )
    else:
        return FieldResult(
            field="government_warning",
            expected=expected,
            found=extracted,
            status="FAIL",
            score=None,
            message=(
                "Government warning does not match exactly. "
                f"Found: {extracted}. Case, punctuation, and whitespace must be exact."
            ),
        )


def aggregate_verification(field_results: list[FieldResult]) -> VerificationResult:
    """Aggregate individual field results into overall verification verdict.
    
    Rule: any FAIL => NEEDS_REVIEW, otherwise APPROVED
    
    Args:
        field_results: list of FieldResult from all field comparisons
        
    Returns:
        VerificationResult with overall status and summary
    """
    failed_fields = [fr.field for fr in field_results if fr.status == "FAIL"]

    if failed_fields:
        overall_verdict = "NEEDS_REVIEW"
        summary = f"Verification requires review: {len(failed_fields)} field(s) failed ({', '.join(failed_fields)})"
    else:
        overall_verdict = "APPROVED"
        summary = "All fields verified successfully"

    return VerificationResult(
        field_results=field_results,
        overall_verdict=overall_verdict,
        summary=summary,
        failed_fields=failed_fields if failed_fields else None,
        latency_ms=0.0,
    )


def verify_label(
    application_data: "ApplicationData", extracted_label: "ExtractedLabel"
) -> VerificationResult:
    """Orchestrate full label verification: compare all fields and aggregate.
    
    Args:
        application_data: expected values from application
        extracted_label: values extracted from label image
        
    Returns:
        VerificationResult with field-by-field comparison and overall verdict
    """
    # Import here to avoid circular imports
    from app.models import ApplicationData, ExtractedLabel
    
    # Compare all 7 fields
    field_results = [
        compare_brand(application_data.brand, extracted_label.brand or ""),
        compare_product_class(application_data.product_class, extracted_label.product_class or ""),
        compare_producer(application_data.producer, extracted_label.producer or ""),
        compare_country(application_data.country, extracted_label.country or ""),
        compare_abv(application_data.abv, extracted_label.abv or ""),
        compare_net_contents(application_data.net_contents, extracted_label.net_contents or ""),
        compare_government_warning(application_data.government_warning, extracted_label.government_warning),
    ]
    
    # Aggregate results
    return aggregate_verification(field_results)
