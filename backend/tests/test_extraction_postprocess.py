"""Regression tests for extraction post-processing."""

from app.extraction_postprocess import (
    infer_country_from_text,
    infer_producer_from_text,
    postprocess_extraction,
)
from app.models import ExtractedLabel


def test_produced_in_canada_fills_missing_country():
    raw_text = (
        "ENJOY CHILLED. PRODUCED IN CANADA IMPORTED BY 12345 IMPORTS "
        "MIAMI, FL GOVERNMENT WARNING: text"
    )

    extracted = postprocess_extraction(
        ExtractedLabel(
            brand="12345 Imports",
            product_class="Rum with Coconut Liqueur",
            producer="12345 Imports",
            country=None,
            raw_text=raw_text,
        )
    )

    assert extracted.country == "Canada"


def test_kingston_new_york_fills_missing_country_and_producer():
    raw_text = (
        "PRODUCED AND BOTTLED BY LIGHTHOUSE VINTNERS KINGSTON, NY "
        "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL"
    )

    extracted = postprocess_extraction(
        ExtractedLabel(
            brand="Lighthouse",
            product_class="Chardonnay",
            producer=None,
            country=None,
            raw_text=raw_text,
        )
    )

    assert extracted.producer == "LIGHTHOUSE VINTNERS"
    assert extracted.country == "United States"


def test_postprocess_does_not_change_government_warning():
    warning = "Government Warning: Do Not Normalize This"

    extracted = postprocess_extraction(
        ExtractedLabel(
            country=None,
            government_warning=warning,
            raw_text="PRODUCED IN CANADA GOVERNMENT WARNING: DO NOT NORMALIZE THIS",
        )
    )

    assert extracted.country == "Canada"
    assert extracted.government_warning == warning


def test_country_inference_uses_explicit_origin_before_importer_address():
    raw_text = "PRODUCED IN CANADA IMPORTED BY 12345 IMPORTS MIAMI, FL"

    assert infer_country_from_text(raw_text) == "Canada"


def test_city_state_implies_united_states():
    raw_text = "Produced and Bottled By Lighthouse Vintners Kingston, NY"

    assert infer_country_from_text(raw_text) == "United States"


def test_producer_inference_stops_before_city_state():
    raw_text = "Produced and Bottled By Lighthouse Vintners Kingston, NY 750 ML"

    assert infer_producer_from_text(raw_text) == "Lighthouse Vintners"
