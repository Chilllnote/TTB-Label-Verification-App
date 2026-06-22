"""Test suite for TTB Label Verification comparison functions."""

import pytest

from app.comparison import (
    compare_abv,
    compare_brand,
    compare_country,
    compare_government_warning,
    compare_net_contents,
    compare_producer,
    compare_product_class,
)
from app.models import FieldResult


class TestBrandFuzzyComparison:
    """Brand fuzzy matching tests."""

    def test_exact_identical_pass(self):
        """Exact identical values should pass."""
        result = compare_brand("Brand Name", "Brand Name")
        assert result.status == "PASS"
        assert result.score is not None
        assert result.score >= 85

    def test_case_only_diff_pass(self):
        """Case-only differences should pass."""
        result = compare_brand("Brand", "brand")
        assert result.status == "PASS"
        assert result.message

    def test_case_only_diff_mixed_case_pass(self):
        """Mixed case should still pass."""
        result = compare_brand("BRAND NAME", "brand name")
        assert result.status == "PASS"

    def test_minor_typo_pass(self):
        """Minor typos within threshold should pass."""
        result = compare_brand("Absolut Vodka", "Absolut Vokda")
        assert result.status == "PASS"

    def test_transposed_words_pass(self):
        """Same words in different order should pass."""
        result = compare_brand("Jack Daniels Tennessee", "Tennessee Jack Daniels")
        assert result.status == "PASS"

    def test_punctuation_variance_pass(self):
        """Punctuation differences should pass."""
        result = compare_brand("Jack Daniels", "Jack Daniels's")
        assert result.status == "PASS"

    def test_clearly_different_fail(self):
        """Clearly different brands should fail."""
        result = compare_brand("Jack Daniels", "Jameson Irish Whiskey")
        assert result.status == "FAIL"

    def test_very_short_low_score_fail(self):
        """Very short text with low fuzzy score should fail."""
        result = compare_brand("A", "B")
        assert result.status == "FAIL"


class TestProductClassFuzzyComparison:
    """Product class fuzzy matching tests."""

    def test_exact_identical_pass(self):
        """Exact identical class should pass."""
        result = compare_product_class("Whiskey", "Whiskey")
        assert result.status == "PASS"

    def test_case_insensitive_pass(self):
        """Case-insensitive matching should pass."""
        result = compare_product_class("Whiskey", "whiskey")
        assert result.status == "PASS"

    def test_clearly_different_fail(self):
        """Different classes should fail."""
        result = compare_product_class("Whiskey", "Vodka")
        assert result.status == "FAIL"


class TestProducerFuzzyComparison:
    """Producer fuzzy matching tests."""

    def test_exact_match_pass(self):
        """Exact producer match should pass."""
        result = compare_producer("Brown Forman", "Brown Forman")
        assert result.status == "PASS"

    def test_case_variant_pass(self):
        """Case variant should pass."""
        result = compare_producer("Brown Forman", "brown forman")
        assert result.status == "PASS"

    def test_clearly_different_fail(self):
        """Different producers should fail."""
        result = compare_producer("Brown Forman", "Diageo")
        assert result.status == "FAIL"


class TestCountrySynonymComparison:
    """Country synonym and normalization tests."""

    def test_usa_synonym_pass(self):
        """USA and United States should match."""
        result = compare_country("USA", "United States")
        assert result.status == "PASS"

    def test_united_states_to_usa_pass(self):
        """United States to USA should match."""
        result = compare_country("United States", "USA")
        assert result.status == "PASS"

    def test_uk_synonym_pass(self):
        """UK and United Kingdom should match."""
        result = compare_country("UK", "United Kingdom")
        assert result.status == "PASS"

    def test_russia_federation_synonym_pass(self):
        """Russia and Russian Federation should match."""
        result = compare_country("Russia", "Russian Federation")
        assert result.status == "PASS"

    def test_czechia_czech_republic_synonym_pass(self):
        """Czechia and Czech Republic should match."""
        result = compare_country("Czechia", "Czech Republic")
        assert result.status == "PASS"

    def test_case_variance_after_normalization_pass(self):
        """Case variance should be normalized."""
        result = compare_country("usa", "UNITED STATES")
        assert result.status == "PASS"

    def test_different_countries_fail(self):
        """Different countries should fail."""
        result = compare_country("USA", "Canada")
        assert result.status == "FAIL"

    def test_unrecognized_country_fail(self):
        """Unrecognized country string should fail."""
        result = compare_country("Atlantis", "Lemuria")
        assert result.status == "FAIL"


class TestABVNumericNormalization:
    """ABV numeric extraction and tolerance tests."""

    def test_exact_percentage_pass(self):
        """Exact percentage match should pass."""
        result = compare_abv("45%", "45%")
        assert result.status == "PASS"
        assert result.score is not None

    def test_decimal_percentage_pass(self):
        """45.0% and 45% should be equivalent."""
        result = compare_abv("45.0%", "45%")
        assert result.status == "PASS"

    def test_percentage_vol_label_pass(self):
        """Percentage with vol label should pass."""
        result = compare_abv("45% vol", "45.0% vol")
        assert result.status == "PASS"

    def test_complex_abv_with_proof_pass(self):
        """Complex ABV with proof notation should pass."""
        result = compare_abv("45%", "45% Alc./Vol. (90 Proof)")
        assert result.status == "PASS"
        assert result.score is not None

    def test_within_tolerance_pass(self):
        """45% vs 44.8% within ±0.2% tolerance should pass."""
        result = compare_abv("45%", "44.8%")
        assert result.status == "PASS"

    def test_outside_tolerance_fail(self):
        """45% vs 44% outside tolerance should fail."""
        result = compare_abv("45%", "44%")
        assert result.status == "FAIL"

    def test_malformed_abv_fail(self):
        """Malformed ABV text should fail."""
        result = compare_abv("45%", "abc%")
        assert result.status == "FAIL"

    def test_missing_abv_fail(self):
        """Missing ABV value should fail."""
        result = compare_abv("45%", "")
        assert result.status == "FAIL"


class TestNetContentsUnitNormalization:
    """Net contents unit normalization tests."""

    def test_ml_case_insensitive_pass(self):
        """750 mL vs 750 ml should match."""
        result = compare_net_contents("750 ml", "750 mL")
        assert result.status == "PASS"

    def test_ml_no_space_pass(self):
        """750mL vs 750 ml should match."""
        result = compare_net_contents("750 mL", "750ml")
        assert result.status == "PASS"

    def test_ml_to_liters_pass(self):
        """750 ml to 0.75 L should match."""
        result = compare_net_contents("750 ml", "0.75 L")
        assert result.status == "PASS"

    def test_liter_to_ml_pass(self):
        """1.5 L to 1500 ml should match."""
        result = compare_net_contents("1.5 L", "1500 ml")
        assert result.status == "PASS"

    def test_numeric_only_default_ml_pass(self):
        """750 vs 750 ml with numeric-only defaulting to ml."""
        result = compare_net_contents("750 ml", "750")
        assert result.status == "PASS"

    def test_divergent_volumes_fail(self):
        """750 ml vs 1 L (divergent volumes) should fail."""
        result = compare_net_contents("750 ml", "1 L")
        assert result.status == "FAIL"

    def test_invalid_units_fail(self):
        """Invalid or missing units should fail."""
        result = compare_net_contents("750 ml", "750 xyz")
        assert result.status == "FAIL"

    def test_empty_value_fail(self):
        """Empty value should fail."""
        result = compare_net_contents("750 ml", "")
        assert result.status == "FAIL"

    def test_fl_oz_to_ml_pass(self):
        """1 L should match approximately 33.8 fl oz."""
        result = compare_net_contents("1 L", "33.8 fl oz")
        # 33.8 fl oz ≈ 1000 ml, within tolerance
        assert result.status == "PASS"


class TestGovernmentWarningExactCaseSensitive:
    """Government warning exact case-sensitive comparison tests."""

    def test_exact_match_pass(self):
        """Exact identical warning should pass."""
        warning = "WARNING: CONTAINS ALCOHOL"
        result = compare_government_warning(warning, warning)
        assert result.status == "PASS"
        assert result.message
        assert "exact" in result.message.lower() or "match" in result.message.lower()

    def test_all_caps_correct_pass(self):
        """Correct all-caps warning should pass."""
        warning = "WARNING: CONTAINS ALCOHOL"
        result = compare_government_warning(warning, warning)
        assert result.status == "PASS"

    def test_title_case_fail(self):
        """Title case (case difference) should FAIL."""
        result = compare_government_warning(
            "WARNING: CONTAINS ALCOHOL", "Warning: Contains Alcohol"
        )
        assert result.status == "FAIL"
        assert "case" in result.message.lower()

    def test_missing_colon_fail(self):
        """Missing colon (punctuation difference) should FAIL."""
        result = compare_government_warning(
            "WARNING: CONTAINS ALCOHOL", "WARNING CONTAINS ALCOHOL"
        )
        assert result.status == "FAIL"
        assert "punctuation" in result.message.lower() or "exact" in result.message.lower()

    def test_trailing_space_fail(self):
        """Trailing space should FAIL."""
        result = compare_government_warning(
            "WARNING: CONTAINS ALCOHOL", "WARNING: CONTAINS ALCOHOL "
        )
        assert result.status == "FAIL"

    def test_empty_warning_fail(self):
        """Empty warning should fail."""
        result = compare_government_warning("WARNING: CONTAINS ALCOHOL", "")
        assert result.status == "FAIL"

    def test_missing_warning_fail(self):
        """Missing warning from extracted should fail."""
        result = compare_government_warning("WARNING: CONTAINS ALCOHOL", None)
        assert result.status == "FAIL"

    def test_extra_space_fail(self):
        """Extra space in warning should fail."""
        result = compare_government_warning(
            "WARNING: CONTAINS ALCOHOL", "WARNING:  CONTAINS ALCOHOL"
        )
        assert result.status == "FAIL"


class TestFieldResultBehavior:
    """FieldResult data availability tests."""

    def test_field_result_always_has_expected_extracted(self):
        """FieldResult should always contain both expected and extracted."""
        result = compare_brand("Brand1", "Brand2")
        assert result.expected == "Brand1"
        assert result.extracted == "Brand2"
        assert result.field_name == "brand"

    def test_misread_warning_returns_extracted_text(self):
        """Misread warning should have extracted text in result."""
        result = compare_government_warning(
            "WARNING: CONTAINS ALCOHOL", "CAUTION: ALCOHOL PRESENT"
        )
        assert result.status == "FAIL"
        assert result.expected == "WARNING: CONTAINS ALCOHOL"
        assert result.extracted == "CAUTION: ALCOHOL PRESENT"
        assert result.message

    def test_score_populated_for_fuzzy_field(self):
        """Score should be populated for fuzzy fields."""
        result = compare_brand("Brand Name", "Brand Name")
        assert result.score is not None
        assert 0 <= result.score <= 100

    def test_score_none_for_exact_warning(self):
        """Score should be None for exact match warning."""
        result = compare_government_warning(
            "WARNING: CONTAINS ALCOHOL", "WARNING: CONTAINS ALCOHOL"
        )
        assert result.score is None


class TestAggregationRule:
    """Test overall verification aggregation logic."""

    def test_all_pass_results_in_pass(self):
        """All passing fields should result in overall PASS."""
        from app.comparison import aggregate_verification

        field_results = [
            compare_brand("Brand", "brand"),
            compare_product_class("Whiskey", "whiskey"),
            compare_country("USA", "United States"),
        ]
        verdict = aggregate_verification(field_results)
        assert verdict.overall_status == "PASS"
        assert verdict.summary

    def test_one_fail_results_in_needs_review(self):
        """Single FAIL should result in NEEDS_REVIEW."""
        from app.comparison import aggregate_verification

        field_results = [
            compare_brand("Brand1", "Brand1"),  # PASS
            compare_government_warning(
                "WARNING: CONTAINS ALCOHOL", "Warning: Contains Alcohol"
            ),  # FAIL
        ]
        verdict = aggregate_verification(field_results)
        assert verdict.overall_status == "NEEDS_REVIEW"
        assert verdict.failed_fields is not None
        assert "government_warning" in verdict.failed_fields

    def test_multiple_fails_results_in_needs_review(self):
        """Multiple FAILs should result in NEEDS_REVIEW."""
        from app.comparison import aggregate_verification

        field_results = [
            compare_brand("Jack Daniels", "Jameson Irish Whiskey"),  # FAIL
            compare_abv("45%", "30%"),  # FAIL
        ]
        verdict = aggregate_verification(field_results)
        assert verdict.overall_status == "NEEDS_REVIEW"
        assert len(verdict.failed_fields) >= 2

    def test_parse_error_in_field_results_in_fail(self):
        """Parse error in a field should mark it FAIL."""
        result = compare_abv("45%", "invalid")
        assert result.status == "FAIL"
