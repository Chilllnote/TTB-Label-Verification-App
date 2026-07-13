"""Unit tests for VisionService and image preprocessing."""

import asyncio
import pytest

from app.models import ExtractedLabel
from app.preprocessing import preprocess_image
from app.vision_service import MockVisionService


class TestImagePreprocessing:
    """Test image preprocessing: downscaling, JPEG encoding."""
    
    def test_preprocess_handles_invalid_image(self):
        """Preprocessing should return original bytes on error (never throw)."""
        corrupt_bytes = b"not an image at all"
        result = preprocess_image(corrupt_bytes)
        # Should return original bytes, not raise exception
        assert result == corrupt_bytes
    
    def test_preprocess_returns_bytes(self):
        """Preprocessing should always return bytes."""
        invalid_bytes = b"xyz"
        result = preprocess_image(invalid_bytes)
        assert isinstance(result, bytes)
    
    def test_preprocess_reduces_size(self):
        """Preprocessing JPEG re-encoding should reduce image file size."""
        # Create a simple RGB image using PIL
        from PIL import Image
        import io
        
        img = Image.new("RGB", (1920, 1080), color=(255, 0, 0))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        png_bytes = buffer.getvalue()
        
        # Preprocess should downscale and re-encode as JPEG
        result = preprocess_image(png_bytes)
        
        # JPEG should be smaller than original PNG
        assert len(result) < len(png_bytes)
        # Result should be JPEG (starts with JPEG magic bytes)
        assert result.startswith(b'\xff\xd8')  # JPEG magic


class TestMockVisionService:
    """Test MockVisionService returns fixed responses."""
    
    def test_mock_returns_default_extraction(self):
        """Mock should return default realistic label by default."""
        service = MockVisionService()
        result = asyncio.run(service.extract(b"fake_image_bytes"))
        
        assert isinstance(result, ExtractedLabel)
        assert result.brand == "Vodka Premium"
        assert result.product_class == "Vodka"
        assert result.government_warning == "WARNING: CONTAINS ALCOHOL"
    
    def test_mock_returns_custom_extraction(self):
        """Mock should return custom response when provided."""
        custom = ExtractedLabel(
            brand="Custom Brand",
            product_class="Whiskey",
            producer="Custom Producer",
            country="USA",
            abv="50%",
            net_contents="1 L",
            government_warning="WARNING: HEALTH HAZARD"
        )
        service = MockVisionService(response=custom)
        result = asyncio.run(service.extract(b"fake_image_bytes"))
        
        assert result.brand == "Custom Brand"
        assert result.product_class == "Whiskey"
    
    def test_mock_returns_partial_extraction(self):
        """Mock should support partial extraction (null fields)."""
        partial = ExtractedLabel(
            brand="Partial Brand",
            product_class=None,  # Unreadable
            producer="Producer",
            country=None,  # Unreadable
            abv="40%",
            net_contents="750 ml",
            government_warning="WARNING: CONTAINS ALCOHOL"
        )
        service = MockVisionService(response=partial)
        result = asyncio.run(service.extract(b"fake_image_bytes"))
        
        assert result.brand == "Partial Brand"
        assert result.product_class is None
        assert result.country is None
        assert result.abv == "40%"
    
    def test_mock_returns_all_nulls_for_non_label(self):
        """Mock should support all-null response (non-label image)."""
        all_null = ExtractedLabel(
            brand=None,
            product_class=None,
            producer=None,
            country=None,
            abv=None,
            net_contents=None,
            government_warning=None
        )
        service = MockVisionService(response=all_null)
        result = asyncio.run(service.extract(b"fake_image_bytes"))
        
        assert result.brand is None
        assert result.product_class is None
        assert result.producer is None
        assert result.country is None
        assert result.abv is None
        assert result.net_contents is None
        assert result.government_warning is None


class TestVisionServiceIntegration:
    """Integration tests with comparison engine (mock vision)."""
    
    def test_mock_extraction_through_comparison(self):
        """Full pipeline: mock extraction -> comparison."""
        from app.comparison import verify_label
        from app.models import ApplicationData
        
        # Application expects exact warning match
        app_data = ApplicationData(
            brand="Vodka Premium",
            product_class="Vodka",
            producer="Premium Distillery Inc.",
            country="Russia",
            abv="40%",
            net_contents="750 ml",
            government_warning="WARNING: CONTAINS ALCOHOL"
        )
        
        # Mock returns exact match
        mock_extraction = ExtractedLabel(
            brand="Vodka Premium",
            product_class="Vodka",
            producer="Premium Distillery Inc.",
            country="Russia",
            abv="40%",
            net_contents="750 ml",
            government_warning="WARNING: CONTAINS ALCOHOL"
        )
        
        # Run comparison
        result = verify_label(app_data, mock_extraction)
        
        assert result.overall_verdict == "APPROVED"
        assert all(fr.status == "PASS" for fr in result.field_results)
    
    def test_mock_extraction_warning_case_sensitive_fail(self):
        """Case mismatch in warning should fail despite other matches."""
        from app.comparison import verify_label
        from app.models import ApplicationData
        
        app_data = ApplicationData(
            brand="Vodka Premium",
            product_class="Vodka",
            producer="Premium Distillery Inc.",
            country="Russia",
            abv="40%",
            net_contents="750 ml",
            government_warning="WARNING: CONTAINS ALCOHOL"
        )
        
        # Mock returns different case for warning
        mock_extraction = ExtractedLabel(
            brand="Vodka Premium",
            product_class="Vodka",
            producer="Premium Distillery Inc.",
            country="Russia",
            abv="40%",
            net_contents="750 ml",
            government_warning="Warning: Contains Alcohol"  # ← WRONG CASE
        )
        
        result = verify_label(app_data, mock_extraction)
        
        # Should have NEEDS_REVIEW due to warning mismatch
        assert result.overall_verdict == "NEEDS_REVIEW"
        
        # Find warning field result
        warning_result = [fr for fr in result.field_results if fr.field == "government_warning"][0]
        assert warning_result.status == "FAIL"
    
    def test_mock_extraction_partial_null_fields(self):
        """Null extracted fields should fail comparison gracefully."""
        from app.comparison import verify_label
        from app.models import ApplicationData
        
        app_data = ApplicationData(
            brand="Vodka Premium",
            product_class="Vodka",
            producer="Premium Distillery Inc.",
            country="Russia",
            abv="40%",
            net_contents="750 ml",
            government_warning="WARNING: CONTAINS ALCOHOL"
        )
        
        # Mock returns partial extraction (some nulls)
        mock_extraction = ExtractedLabel(
            brand="Vodka Premium",
            product_class=None,  # ← UNREADABLE
            producer="Premium Distillery Inc.",
            country="Russia",
            abv="40%",
            net_contents="750 ml",
            government_warning="WARNING: CONTAINS ALCOHOL"
        )
        
        result = verify_label(app_data, mock_extraction)
        
        # Should have NEEDS_REVIEW due to missing class
        assert result.overall_verdict == "NEEDS_REVIEW"
        
        # Class field should FAIL
        class_result = [fr for fr in result.field_results if fr.field == "product_class"][0]
        assert class_result.status == "FAIL"
        assert class_result.found == ""  # Null becomes empty string in comparison


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
