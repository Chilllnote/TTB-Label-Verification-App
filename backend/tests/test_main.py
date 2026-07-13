import asyncio
import io
import json
import time

import pytest
from fastapi import HTTPException
from PIL import Image

from app import config
from app.main import verify_batch_endpoint, verify_endpoint
from app.models import ExtractedLabel
from app.vision_service import MockVisionService, VisionServiceUnavailableError


class FakeUpload:
    def __init__(self, filename: str, content: bytes, content_type: str = "image/jpeg"):
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


class SequenceVisionService:
    def __init__(self, responses):
        self.responses = list(responses)
        self.index = 0

    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        response = self.responses[self.index]
        self.index += 1
        return response


class SlowVisionService:
    def __init__(self, delay: float = 0.2):
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return matching_extraction()
        finally:
            self.active -= 1


class FailingVisionService:
    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        raise VisionServiceUnavailableError("provider timeout")


class CapturingVisionService:
    def __init__(self):
        self.image_bytes = None

    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        self.image_bytes = image_bytes
        return matching_extraction()


def make_image_bytes(format_name="JPEG", color=(255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    img = Image.new("RGB", (200, 200), color=color)
    img.save(buffer, format=format_name)
    return buffer.getvalue()


def make_jpeg_bytes(color=(255, 0, 0)) -> bytes:
    return make_image_bytes("JPEG", color)


def matching_application_data() -> dict:
    return {
        "brand": "Vodka Premium",
        "class": "Vodka",
        "producer": "Premium Distillery Inc.",
        "country": "Russia",
        "abv": "40%",
        "net_contents": "750 ml",
        "government_warning": "WARNING: CONTAINS ALCOHOL",
    }


def matching_extraction() -> ExtractedLabel:
    return ExtractedLabel(
        brand="Vodka Premium",
        product_class="Vodka",
        producer="Premium Distillery Inc.",
        country="Russia",
        abv="40%",
        net_contents="750 ml",
        government_warning="WARNING: CONTAINS ALCOHOL",
    )


def run(coro):
    return asyncio.run(coro)


def test_verify_endpoint_passes_with_matching_mocked_extraction():
    result = run(
        verify_endpoint(
            image=FakeUpload("label.jpg", make_jpeg_bytes()),
            application_data=json.dumps(matching_application_data()),
            vision_service=MockVisionService(),
        )
    )

    assert result.overall_verdict == "APPROVED"
    assert result.latency_ms >= 0
    assert len(result.field_results) == 7
    assert result.failed_fields is None


def test_verify_endpoint_needs_review_for_warning_case_mismatch():
    wrong_warning = matching_extraction()
    wrong_warning.government_warning = "Warning: Contains Alcohol"

    result = run(
        verify_endpoint(
            image=FakeUpload("label.jpg", make_jpeg_bytes()),
            application_data=json.dumps(matching_application_data()),
            vision_service=MockVisionService(response=wrong_warning),
        )
    )

    assert result.overall_verdict == "NEEDS_REVIEW"
    warning_result = next(fr for fr in result.field_results if fr.field == "government_warning")
    assert warning_result.status == "FAIL"
    assert warning_result.expected == "WARNING: CONTAINS ALCOHOL"
    assert warning_result.found == "Warning: Contains Alcohol"


def test_verify_endpoint_rejects_invalid_image_type():
    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_endpoint(
                image=FakeUpload("label.txt", b"not an image", "text/plain"),
                application_data=json.dumps(matching_application_data()),
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "Invalid image format" in exc_info.value.detail


@pytest.mark.parametrize(
    ("filename", "content_type", "format_name"),
    [
        ("label.webp", "image/webp", "WEBP"),
        ("label.tiff", "image/tiff", "TIFF"),
    ],
)
def test_verify_endpoint_accepts_supported_non_picker_formats(filename, content_type, format_name):
    service = CapturingVisionService()

    result = run(
        verify_endpoint(
            image=FakeUpload(filename, make_image_bytes(format_name), content_type),
            application_data=json.dumps(matching_application_data()),
            vision_service=service,
        )
    )

    assert result.overall_verdict == "APPROVED"
    assert service.image_bytes.startswith(b"\xff\xd8")


def test_verify_endpoint_rejects_oversized_image():
    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_endpoint(
                image=FakeUpload("label.jpg", b"0" * (5 * 1024 * 1024 + 1)),
                application_data=json.dumps(matching_application_data()),
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "too large" in exc_info.value.detail.lower()


def test_verify_endpoint_rejects_malformed_application_data():
    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_endpoint(
                image=FakeUpload("label.jpg", make_jpeg_bytes()),
                application_data="{bad json}",
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "Invalid application_data" in exc_info.value.detail


def test_verify_endpoint_reports_wrong_type_field_errors():
    bad_data = matching_application_data()
    bad_data["abv"] = 40

    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_endpoint(
                image=FakeUpload("label.jpg", make_jpeg_bytes()),
                application_data=json.dumps(bad_data),
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["message"] == "Please fix: Alcohol %."
    assert exc_info.value.detail["field_errors"] == [
        {
            "field": "abv",
            "label": "Alcohol %",
            "message": "Value error, Field must be text",
        }
    ]


def test_verify_endpoint_handles_partial_extraction_as_needs_review():
    partial_extraction = matching_extraction()
    partial_extraction.product_class = None

    result = run(
        verify_endpoint(
            image=FakeUpload("label.jpg", make_jpeg_bytes()),
            application_data=json.dumps(matching_application_data()),
            vision_service=MockVisionService(response=partial_extraction),
        )
    )

    assert result.overall_verdict == "NEEDS_REVIEW"
    class_result = next(fr for fr in result.field_results if fr.field == "product_class")
    assert class_result.status == "FAIL"
    assert class_result.found == ""


def test_extracted_label_contract_includes_raw_text_and_confidence():
    extracted = ExtractedLabel(raw_text="full label OCR", extraction_confidence=0.82)
    dumped = extracted.model_dump()
    assert dumped["raw_text"] == "full label OCR"
    assert dumped["extraction_confidence"] == 0.82


def test_verify_endpoint_maps_vision_failure_to_unreadable_photo_error():
    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_endpoint(
                image=FakeUpload("label.jpg", make_jpeg_bytes()),
                application_data=json.dumps(matching_application_data()),
                vision_service=FailingVisionService(),
            )
        )

    assert exc_info.value.status_code == 503
    assert "could not read this photo" in exc_info.value.detail.lower()


def test_verify_batch_endpoint_all_pass_summary_and_order():
    result = run(
        verify_batch_endpoint(
            images=[
                FakeUpload("label-a.jpg", make_jpeg_bytes((255, 0, 0))),
                FakeUpload("label-b.jpg", make_jpeg_bytes((0, 255, 0))),
                FakeUpload("label-c.jpg", make_jpeg_bytes((0, 0, 255))),
            ],
            application_data=json.dumps(
                [matching_application_data(), matching_application_data(), matching_application_data()]
            ),
            vision_service=MockVisionService(),
        )
    )

    assert result.summary.model_dump() == {
        "total": 3,
        "passed": 3,
        "needs_review": 0,
        "errors": 0,
    }
    assert [item.index for item in result.results] == [0, 1, 2]
    assert [item.filename for item in result.results] == [
        "label-a.jpg",
        "label-b.jpg",
        "label-c.jpg",
    ]
    assert all(item.status == "APPROVED" for item in result.results)


def test_verify_batch_endpoint_counts_needs_review():
    wrong_warning = matching_extraction()
    wrong_warning.government_warning = "Warning: Contains Alcohol"

    result = run(
        verify_batch_endpoint(
            images=[
                FakeUpload("label-a.jpg", make_jpeg_bytes()),
                FakeUpload("label-b.jpg", make_jpeg_bytes()),
            ],
            application_data=json.dumps([matching_application_data(), matching_application_data()]),
            vision_service=SequenceVisionService([matching_extraction(), wrong_warning]),
        )
    )

    assert result.summary.model_dump() == {
        "total": 2,
        "passed": 1,
        "needs_review": 1,
        "errors": 0,
    }
    assert result.results[0].status == "APPROVED"
    assert result.results[1].status == "NEEDS_REVIEW"
    assert result.results[1].result.overall_verdict == "NEEDS_REVIEW"


def test_verify_batch_endpoint_isolates_bad_image_to_item_error():
    result = run(
        verify_batch_endpoint(
            images=[
                FakeUpload("label-a.jpg", make_jpeg_bytes()),
                FakeUpload("label-b.txt", b"not an image", "text/plain"),
            ],
            application_data=json.dumps([matching_application_data(), matching_application_data()]),
            vision_service=MockVisionService(),
        )
    )

    assert result.summary.model_dump() == {
        "total": 2,
        "passed": 1,
        "needs_review": 0,
        "errors": 1,
    }
    assert result.results[0].status == "APPROVED"
    assert result.results[1].status == "ERROR"
    assert result.results[1].result is None
    assert "image file" in result.results[1].error


def test_verify_batch_endpoint_isolates_vision_failure_to_item_error():
    result = run(
        verify_batch_endpoint(
            images=[FakeUpload("label-a.jpg", make_jpeg_bytes())],
            application_data=json.dumps([matching_application_data()]),
            vision_service=FailingVisionService(),
        )
    )

    assert result.summary.model_dump() == {
        "total": 1,
        "passed": 0,
        "needs_review": 0,
        "errors": 1,
    }
    assert result.results[0].status == "ERROR"
    assert result.results[0].result is None
    assert "clear image file" in result.results[0].error


def test_verify_batch_endpoint_rejects_image_data_count_mismatch():
    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_batch_endpoint(
                images=[
                    FakeUpload("label-a.jpg", make_jpeg_bytes()),
                    FakeUpload("label-b.jpg", make_jpeg_bytes()),
                ],
                application_data=json.dumps([matching_application_data()]),
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "Each label needs one image" in exc_info.value.detail


def test_verify_batch_endpoint_rejects_more_than_five_labels():
    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_batch_endpoint(
                images=[
                    FakeUpload(f"label-{index}.jpg", make_jpeg_bytes())
                    for index in range(6)
                ],
                application_data=json.dumps([matching_application_data() for _ in range(6)]),
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "Maximum allowed size is 5" in exc_info.value.detail


def test_verify_batch_endpoint_rejects_missing_required_fields():
    bad_data = matching_application_data()
    del bad_data["brand"]

    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_batch_endpoint(
                images=[FakeUpload("label-a.jpg", make_jpeg_bytes())],
                application_data=json.dumps([bad_data]),
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["message"] == "Please fix: Label 1 Brand."
    assert exc_info.value.detail["field_errors"] == [
        {
            "field": "brand",
            "label": "Brand",
            "message": "Field required",
            "index": 0,
        }
    ]


def test_verify_batch_endpoint_processes_concurrently_with_bound(monkeypatch, tmp_path):
    service = SlowVisionService(delay=0.2)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "OPENAI_VISION_MODEL=gpt-4o-mini",
                "OPENAI_TIMEOUT_SECONDS=3.8",
                "OPENAI_IMAGE_DETAIL=high",
                "PREPROCESS_MAX_DIMENSION=768",
                "PREPROCESS_JPEG_QUALITY=75",
                "BATCH_CONCURRENCY=3",
            ]
        )
        + "\n"
    )
    monkeypatch.setattr(config, "DOTENV_PATH", dotenv_path)

    start = time.perf_counter()
    result = run(
        verify_batch_endpoint(
            images=[
                FakeUpload(f"label-{index}.jpg", make_jpeg_bytes())
                for index in range(3)
            ],
            application_data=json.dumps([matching_application_data() for _ in range(3)]),
            vision_service=service,
        )
    )
    elapsed = time.perf_counter() - start

    assert result.summary.model_dump() == {
        "total": 3,
        "passed": 3,
        "needs_review": 0,
        "errors": 0,
    }
    assert service.max_active == 3
    assert elapsed < 0.45
