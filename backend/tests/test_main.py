import asyncio
import io
import json
import logging
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from app import config
from app.main import verify_batch_endpoint, verify_endpoint
from app.main import (
    _application_records,
    _uploaded_image_bytes,
    get_application,
    get_application_image,
    list_applications,
    update_application_status_endpoint,
    upload_application_batch_endpoint,
    upload_application_endpoint,
    upload_application_json_endpoint,
)
from app.models import ExtractedLabel
from app.vision_service import MockVisionService, VisionServiceUnavailableError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def matching_application_package(application_id="APP-00000001", image_filename="sample_label.jpg") -> dict:
    return {
        "application_id": application_id,
        "image_filename": image_filename,
        "application_data": {
            "brand_name": "Vodka Premium",
            "class_type_designation": "Vodka",
            "bottler_producer_name_address": "Premium Distillery Inc.",
            "country_of_origin": "Russia",
            "alcohol_content": "40%",
            "net_contents": "750 ml",
            "government_health_warning_statement": "WARNING: CONTAINS ALCOHOL",
        },
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


def warning_mismatch_extraction() -> ExtractedLabel:
    extracted = matching_extraction()
    extracted.government_warning = "Warning: Contains Alcohol"
    return extracted


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


def test_upload_application_endpoint_adds_only_accepted_record():
    _application_records.clear()
    _uploaded_image_bytes.clear()

    result = run(
        upload_application_endpoint(
            image=FakeUpload("new-label.jpg", make_jpeg_bytes()),
            application=json.dumps(matching_application_package("APP-00000002", "new-label.jpg")),
            vision_service=MockVisionService(),
        )
    )

    assert result.application_id == "APP-00000002"
    assert result.status == "ACCEPTED"
    assert result.verification_result is not None
    assert _application_records["APP-00000002"].status == "ACCEPTED"
    assert _uploaded_image_bytes["new-label.jpg"][1] == "image/jpeg"


def test_upload_application_verification_still_preprocesses_before_vision():
    _application_records.clear()
    _uploaded_image_bytes.clear()
    service = CapturingVisionService()

    completed = run(
        upload_application_endpoint(
            image=FakeUpload("new-label.png", make_image_bytes("PNG"), "image/png"),
            application=json.dumps(matching_application_package("APP-00000003", "new-label.png")),
            vision_service=service,
        )
    )

    assert completed.status == "ACCEPTED"
    assert service.image_bytes.startswith(b"\xff\xd8")
    assert completed.verification_result.metrics.preprocessed_format == "JPEG"


def test_upload_application_endpoint_uploads_mismatch_as_review_record():
    _application_records.clear()
    _uploaded_image_bytes.clear()

    result = run(
        upload_application_endpoint(
            image=FakeUpload("bad-label.jpg", make_jpeg_bytes()),
            application=json.dumps(matching_application_package("APP-00000004", "bad-label.jpg")),
            vision_service=SequenceVisionService([warning_mismatch_extraction()]),
        )
    )

    assert result.application_id == "APP-00000004"
    assert result.status == "NEEDS_CHECK"
    assert result.verification_result.failed_fields == ["government_warning"]
    assert _application_records["APP-00000004"].status == "NEEDS_CHECK"
    assert _uploaded_image_bytes["bad-label.jpg"][1] == "image/jpeg"


def test_upload_application_batch_endpoint_uses_database_image_filename():
    _application_records.clear()

    result = run(
        upload_application_batch_endpoint(
            application_file=FakeUpload(
                "applications.json",
                json.dumps([matching_application_package("APP-00000005", "sample_label.jpg")]).encode("utf-8"),
                "application/json",
            ),
            vision_service=MockVisionService(),
        )
    )

    assert len(result) == 1
    assert result[0].application_id == "APP-00000005"
    assert result[0].image_filename == "sample_label.jpg"
    assert result[0].status == "ACCEPTED"
    assert _application_records["APP-00000005"].status == "ACCEPTED"


def test_upload_application_json_endpoint_generates_application_id():
    _application_records.clear()
    package = matching_application_package("", "sample_label.jpg")
    del package["application_id"]

    result = run(
        upload_application_json_endpoint(
            application_file=FakeUpload(
                "application.json",
                json.dumps(package).encode("utf-8"),
                "application/json",
            ),
            vision_service=MockVisionService(),
        )
    )

    assert len(result.application_id) == len("APP-1B81036D")
    assert result.application_id.startswith("APP-")
    assert result.image_filename == "sample_label.jpg"
    assert result.status == "ACCEPTED"
    assert _application_records[result.application_id].status == "ACCEPTED"


def test_upload_application_json_endpoint_rejects_invalid_application_id_shape():
    _application_records.clear()
    package = matching_application_package("APP-001", "sample_label.jpg")

    with pytest.raises(HTTPException) as exc_info:
        run(
            upload_application_json_endpoint(
                application_file=FakeUpload(
                    "application.json",
                    json.dumps(package).encode("utf-8"),
                    "application/json",
                ),
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "application_id" in str(exc_info.value.detail)
    assert "APP-1B81036D" in str(exc_info.value.detail)
    assert _application_records == {}


def test_upload_application_batch_endpoint_uploads_verification_mismatches():
    _application_records.clear()

    result = run(
        upload_application_batch_endpoint(
            application_file=FakeUpload(
                "applications.json",
                json.dumps(
                    [
                        matching_application_package("APP-00000006", "sample_label.jpg"),
                        matching_application_package("APP-00000007", "sample_label.jpg"),
                    ]
                ).encode("utf-8"),
                "application/json",
            ),
            vision_service=SequenceVisionService(
                [matching_extraction(), warning_mismatch_extraction()]
            ),
        )
    )

    assert [record.application_id for record in result] == ["APP-00000006", "APP-00000007"]
    assert [record.status for record in result] == ["ACCEPTED", "NEEDS_CHECK"]
    assert _application_records["APP-00000006"].status == "ACCEPTED"
    assert _application_records["APP-00000007"].status == "NEEDS_CHECK"


def test_sample_batch_success_upload_uses_repo_images_with_mock_vision():
    _application_records.clear()
    sample_path = PROJECT_ROOT / "sample_JSON" / "batch_upload_success.json"

    result = run(
        upload_application_batch_endpoint(
            application_file=FakeUpload(
                "batch_upload_success.json",
                sample_path.read_bytes(),
                "application/json",
            ),
            vision_service=MockVisionService(),
        )
    )

    assert [record.application_id for record in result] == [
        "APP-B0000001",
        "APP-B0000002",
    ]
    assert all(record.status == "ACCEPTED" for record in result)
    assert _application_records["APP-B0000001"].status == "ACCEPTED"
    assert _application_records["APP-B0000002"].status == "ACCEPTED"


def test_sample_batch_failure_upload_reports_missing_image_only():
    _application_records.clear()
    sample_path = PROJECT_ROOT / "sample_JSON" / "batch_upload_failure.json"

    with pytest.raises(HTTPException) as exc_info:
        run(
            upload_application_batch_endpoint(
                application_file=FakeUpload(
                    "batch_upload_failure.json",
                    sample_path.read_bytes(),
                    "application/json",
                ),
                vision_service=MockVisionService(),
            )
        )

    assert exc_info.value.status_code == 400
    assert _application_records == {}
    failures = exc_info.value.detail["upload_failures"]
    assert [failure["application_id"] for failure in failures] == ["APP-C0000002"]
    assert failures[0]["status"] == "ERROR"
    assert "not found" in failures[0]["error"].lower()


def test_list_applications_loads_repo_mock_applications():
    _application_records.clear()

    result = run(list_applications())

    assert {record.application_id for record in result} >= {
        "APP-A0000001",
        "APP-A0000002",
        "APP-A0000003",
    }
    by_id = {record.application_id: record for record in result}
    assert by_id["APP-A0000001"].status == "ACCEPTED"
    assert by_id["APP-A0000001"].match_percentage == 100
    assert by_id["APP-A0000002"].status == "NEEDS_CHECK"
    assert by_id["APP-A0000002"].match_percentage == 86
    assert by_id["APP-A0000003"].status == "REJECTED"
    assert by_id["APP-A0000003"].match_percentage < 50


def test_update_application_status_endpoint_changes_accepted_or_rejected_status():
    _application_records.clear()
    run(list_applications())

    result = run(
        update_application_status_endpoint(
            "APP-A0000002",
            {"status": "ACCEPTED"},
        )
    )

    assert result.status == "ACCEPTED"
    assert run(get_application("APP-A0000002")).status == "ACCEPTED"


def test_get_application_image_serves_uploaded_image_bytes():
    _uploaded_image_bytes.clear()
    image_bytes = make_jpeg_bytes()
    _uploaded_image_bytes["uploaded-label.jpg"] = (image_bytes, "image/jpeg")

    response = run(get_application_image("uploaded-label.jpg"))

    assert response.media_type == "image/jpeg"
    assert response.body == image_bytes


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


def test_verify_batch_endpoint_allows_total_latency_over_five_seconds(monkeypatch, caplog):
    ticks = {"value": 0.0}

    def fake_perf_counter():
        ticks["value"] += 0.2
        return ticks["value"]

    monkeypatch.setattr("app.main.time.perf_counter", fake_perf_counter)
    caplog.set_level(logging.WARNING, logger="app.main")

    result = run(
        verify_batch_endpoint(
            images=[
                FakeUpload("label-a.jpg", make_jpeg_bytes()),
                FakeUpload("label-b.jpg", make_jpeg_bytes()),
                FakeUpload("label-c.jpg", make_jpeg_bytes()),
            ],
            application_data=json.dumps(
                [matching_application_data(), matching_application_data(), matching_application_data()]
            ),
            vision_service=MockVisionService(),
        )
    )

    assert result.latency_ms > 5000
    assert result.summary.passed == 3
    assert "Batch verification latency exceeded 5 second budget" not in caplog.text


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
                "OPENAI_TIMEOUT_SECONDS=20",
                "OPENAI_IMAGE_DETAIL=high",
                "PREPROCESS_MAX_DIMENSION=1024",
                "PREPROCESS_JPEG_QUALITY=70",
                "PREPROCESS_GRAYSCALE=true",
                "PREPROCESS_THRESHOLD=off",
                "PREPROCESS_CONTRAST=true",
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
