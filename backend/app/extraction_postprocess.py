"""Conservative cleanup for vision extraction results."""

import re
from typing import Optional

from app.models import ExtractedLabel


US_STATE_ABBREVIATIONS = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}


COUNTRY_ALIASES = {
    "canada": "Canada",
    "united states": "United States",
    "united states of america": "United States",
    "usa": "United States",
    "u.s.a.": "United States",
    "us": "United States",
    "u.s.": "United States",
}


COUNTRY_PHRASES = (
    re.compile(
        r"\b(?:produced|product|made|crafted|bottled|distilled|brewed|vinted)\s+"
        r"(?:and\s+\w+\s+)?(?:in|of)\s+"
        r"(canada|united states(?: of america)?|u\.s\.a\.|u\.s\.|usa|us)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:country\s+of\s+origin|origin)\s*[:\-]?\s*"
        r"(canada|united states(?: of america)?|u\.s\.a\.|u\.s\.|usa|us)\b",
        re.IGNORECASE,
    ),
)


PRODUCER_PHRASE = re.compile(
    r"\b(?:produced\s+and\s+bottled|produced|bottled|distilled|brewed|vinted)\s+"
    r"by\s+(.+?)(?=\s{2,}|\s+\d{3,5}\b|\s+GOVERNMENT\b|\s+CONTAINS\b|$)",
    re.IGNORECASE,
)


CITY_STATE_PHRASE = re.compile(
    r"\b[A-Z][a-zA-Z .'-]+,\s*([A-Z]{2})\b"
)


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,.;:-")


def _canonical_country(value: str) -> Optional[str]:
    return COUNTRY_ALIASES.get(_normalize_spaces(value).lower())


def infer_country_from_text(raw_text: Optional[str]) -> Optional[str]:
    """Infer a country only from explicit origin or clear U.S. state text."""
    if not raw_text:
        return None

    normalized_text = _normalize_spaces(raw_text)
    for pattern in COUNTRY_PHRASES:
        match = pattern.search(normalized_text)
        if match:
            return _canonical_country(match.group(1))

    for match in CITY_STATE_PHRASE.finditer(raw_text):
        if match.group(1).upper() in US_STATE_ABBREVIATIONS:
            return "United States"

    return None


def infer_producer_from_text(raw_text: Optional[str]) -> Optional[str]:
    """Infer the responsible producer/bottler from an explicit byline."""
    if not raw_text:
        return None

    match = PRODUCER_PHRASE.search(_normalize_spaces(raw_text))
    if not match:
        return None

    producer = _normalize_spaces(match.group(1))
    producer = re.sub(
        r"\s+[A-Z][a-zA-Z.'-]+,\s*[A-Z]{2}\b.*$",
        "",
        producer,
    ).strip(" ,.;:-")
    if not producer:
        return None

    return producer


def postprocess_extraction(extracted: ExtractedLabel) -> ExtractedLabel:
    """Fill missing structured fields from obvious raw text cues.

    The government warning is intentionally untouched because it must remain an
    exact, case-sensitive extraction.
    """
    updates = {}
    if not extracted.country:
        country = infer_country_from_text(extracted.raw_text)
        if country:
            updates["country"] = country

    if not extracted.producer:
        producer = infer_producer_from_text(extracted.raw_text)
        if producer:
            updates["producer"] = producer

    if not updates:
        return extracted

    return extracted.model_copy(update=updates)
