import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app, get_vision_service
from app.models import ApplicationData, ExtractedLabel
from app.vision_service import MockVisionService

client = TestClient(app)


def make_jpeg_bytes(color=(255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    img = Image.new("RGB", (200, 200), color=color)
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_verify_endpoint_passes_with_matching_mocked_extraction():
    app.dependency_overrides[get_vision_service] = lambda: MockVisionService()

    payload = {
        "application_data": json.dumps(
            {
                "brand": "Vodka Premium",
                "class": "Vodka",
                "producer": "Premium Distillery Inc.",
                "country": "Russia",
                "abv": "40%",
                "net_contents": "750 ml",
                "government_warning": "WARNING: CONTAINS ALCOHOL",
            }
        )
    }
    response = client.post(
        "/verify",
        data=payload,
        files=[("image", ("label.jpg", make_jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "PASS"
    assert body["latency_ms"] >= 0
    assert len(body["field_results"]) == 7
    assert body["failed_fields"] is None


def test_verify_endpoint_needs_review_for_warning_case_mismatch():
    wrong_warning = ExtractedLabel(
        brand="Vodka Premium",
        product_class="Vodka",
        producer="Premium Distillery Inc.",
        country="Russia",
        abv="40%",
        net_contents="750 ml",
        government_warning="Warning: Contains Alcohol",
    )
    app.dependency_overrides[get_vision_service] = lambda: MockVisionService(response=wrong_warning)

    payload = {
        "application_data": json.dumps(
            {
                "brand": "Vodka Premium",
                "class": "Vodka",
                "producer": "Premium Distillery Inc.",
                "country": "Russia",
                "abv": "40%",
                "net_contents": "750 ml",
                "government_warning": "WARNING: CONTAINS ALCOHOL",
            }
        )
    }
    response = client.post(
        "/verify",
        data=payload,
        files=[("image", ("label.jpg", make_jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "NEEDS_REVIEW"
    assert body["latency_ms"] >= 0

    warning_result = next(
        fr for fr in body["field_results"] if fr["field_name"] == "government_warning"
    )
    assert warning_result["status"] == "FAIL"
    assert warning_result["expected"] == "WARNING: CONTAINS ALCOHOL"
    assert warning_result["extracted"] == "Warning: Contains Alcohol"


def test_verify_endpoint_rejects_invalid_image_type():
    app.dependency_overrides[get_vision_service] = lambda: MockVisionService()

    payload = {
        "application_data": json.dumps(
            {
                "brand": "Vodka Premium",
                "class": "Vodka",
                "producer": "Premium Distillery Inc.",
                "country": "Russia",
                "abv": "40%",
                "net_contents": "750 ml",
                "government_warning": "WARNING: CONTAINS ALCOHOL",
            }
        )
    }
    response = client.post(
        "/verify",
        data=payload,
        files=[("image", ("label.txt", b"not an image", "text/plain"))],
    )

    assert response.status_code == 400
    assert "Invalid image format" in response.json()["detail"]


def test_verify_endpoint_rejects_oversized_image():
    app.dependency_overrides[get_vision_service] = lambda: MockVisionService()

    payload = {
        "application_data": json.dumps(
            {
                "brand": "Vodka Premium",
                "class": "Vodka",
                "producer": "Premium Distillery Inc.",
                "country": "Russia",
                "abv": "40%",
                "net_contents": "750 ml",
                "government_warning": "WARNING: CONTAINS ALCOHOL",
            }
        )
    }
    oversized_bytes = b"0" * (5 * 1024 * 1024 + 1)
    response = client.post(
        "/verify",
        data=payload,
        files=[("image", ("label.jpg", oversized_bytes, "image/jpeg"))],
    )

    assert response.status_code == 400
    assert "too large" in response.json()["detail"].lower()


def test_verify_endpoint_rejects_malformed_application_data():
    app.dependency_overrides[get_vision_service] = lambda: MockVisionService()

    payload = {"application_data": "{bad json}"}
    response = client.post(
        "/verify",
        data=payload,
        files=[("image", ("label.jpg", make_jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 400
    assert "Invalid application_data" in response.json()["detail"]


def test_verify_endpoint_handles_partial_extraction_as_needs_review():
    partial_extraction = ExtractedLabel(
        brand="Vodka Premium",
        product_class=None,
        producer="Premium Distillery Inc.",
        country="Russia",
        abv="40%",
        net_contents="750 ml",
        government_warning="WARNING: CONTAINS ALCOHOL",
    )
    app.dependency_overrides[get_vision_service] = lambda: MockVisionService(response=partial_extraction)

    payload = {
        "application_data": json.dumps(
            {
                "brand": "Vodka Premium",
                "class": "Vodka",
                "producer": "Premium Distillery Inc.",
                "country": "Russia",
                "abv": "40%",
                "net_contents": "750 ml",
                "government_warning": "WARNING: CONTAINS ALCOHOL",
            }
        )
    }
    response = client.post(
        "/verify",
        data=payload,
        files=[("image", ("label.jpg", make_jpeg_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "NEEDS_REVIEW"
    assert body["latency_ms"] >= 0

    class_result = next(
        fr for fr in body["field_results"] if fr["field_name"] == "product_class"
    )
    assert class_result["status"] == "FAIL"
    assert class_result["extracted"] == ""
