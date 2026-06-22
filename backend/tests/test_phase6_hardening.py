import asyncio
import io
import json
import time

import pytest
from fastapi import HTTPException
from PIL import Image, ImageFilter

from app.comparison import verify_label
from app.main import verify_batch_endpoint, verify_endpoint
from app.models import ApplicationData, ExtractedLabel
from app.preprocessing import preprocess_image
from app.vision_service import MockVisionService


class FakeUpload:
    def __init__(self, filename: str, content: bytes, content_type: str = "image/jpeg"):
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


class FixedVisionService:
    def __init__(self, response: ExtractedLabel, delay: float = 0.0):
        self.response = response
        self.delay = delay
        self.calls = 0

    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.response


class SequenceVisionService:
    def __init__(self, responses: list[ExtractedLabel]):
        self.responses = responses
        self.index = 0

    async def extract(self, image_bytes: bytes) -> ExtractedLabel:
        response = self.responses[self.index]
        self.index += 1
        return response


def run(coro):
    return asyncio.run(coro)


def make_label_image(
    text_lines: list[str] | None = None,
    *,
    blur=False,
    dark=False,
    rotate=False,
    marker: str | None = None,
) -> bytes:
    text_lines = text_lines or [
        "PREMIUM VODKA",
        "Brand: Vodka Premium",
        "Class: Vodka",
        "Producer: Premium Distillery Inc.",
        "Country: Russia",
        "ABV: 40%",
        "Net Contents: 750 ml",
        "WARNING: CONTAINS ALCOHOL",
    ]
    img = Image.new("RGB", (700, 900), color=(244, 244, 238))
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            if dark:
                pixels[x, y] = (42, 42, 40)

    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    y = 60
    fill = (10, 10, 10) if not dark else (70, 70, 70)
    for line in text_lines:
        draw.text((40, y), line, fill=fill)
        y += 70
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(radius=8))
    if rotate:
        img = img.rotate(8, expand=True, fillcolor=(244, 244, 238))

    draw = ImageDraw.Draw(img)
    if marker == "missing_warning":
        draw.rectangle((0, 0, 120, 120), fill=(235, 25, 25))
    elif marker == "all_null":
        draw.rectangle((0, 0, 120, 120), fill=(25, 65, 235))

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def matching_data(**overrides) -> dict:
    data = {
        "brand": "Vodka Premium",
        "class": "Vodka",
        "producer": "Premium Distillery Inc.",
        "country": "Russia",
        "abv": "40%",
        "net_contents": "750 ml",
        "government_warning": "WARNING: CONTAINS ALCOHOL",
    }
    data.update(overrides)
    return data


def matching_extraction(**overrides) -> ExtractedLabel:
    data = {
        "brand": "Vodka Premium",
        "product_class": "Vodka",
        "producer": "Premium Distillery Inc.",
        "country": "Russia",
        "abv": "40%",
        "net_contents": "750 ml",
        "government_warning": "WARNING: CONTAINS ALCOHOL",
    }
    data.update(overrides)
    return ExtractedLabel(**data)


def all_null_extraction() -> ExtractedLabel:
    return ExtractedLabel(
        brand=None,
        product_class=None,
        producer=None,
        country=None,
        abv=None,
        net_contents=None,
        government_warning=None,
    )


def test_valid_label_passes_and_records_latency_metrics():
    service = FixedVisionService(matching_extraction(), delay=0.01)

    result = run(
        verify_endpoint(
            image=FakeUpload("valid-label.jpg", make_label_image()),
            application_data=json.dumps(matching_data()),
            vision_service=service,
        )
    )

    assert result.overall_status == "PASS"
    assert result.failed_fields is None
    assert result.latency_ms < 5000
    assert result.metrics is not None
    assert result.metrics.preprocessed_bytes > 0
    assert result.metrics.vision_ms >= 10
    assert result.metrics.total_latency_ms == result.latency_ms


def test_mismatched_fields_need_review():
    extraction = matching_extraction(
        brand="Other Vodka",
        product_class="Gin",
        producer="Other Producer",
        country="United States",
        abv="41%",
        net_contents="700 ml",
    )

    result = verify_label(ApplicationData.model_validate(matching_data()), extraction)

    assert result.overall_status == "NEEDS_REVIEW"
    assert set(result.failed_fields or []) == {
        "brand",
        "product_class",
        "producer",
        "country",
        "abv",
        "net_contents",
    }


def test_case_only_non_warning_fields_pass_but_warning_case_fails():
    case_only = matching_extraction(
        brand="vodka premium",
        product_class="vodka",
        producer="premium distillery inc.",
        government_warning="Warning: Contains Alcohol",
    )

    result = verify_label(ApplicationData.model_validate(matching_data()), case_only)

    passing = {field.field_name for field in result.field_results if field.status == "PASS"}
    failing = {field.field_name for field in result.field_results if field.status == "FAIL"}
    assert {"brand", "product_class", "producer"}.issubset(passing)
    assert "government_warning" in failing


def test_abv_and_units_normalization_pass_equivalent_forms_and_fail_wrong_numbers():
    equivalent = matching_extraction(abv="40% Alc./Vol.", net_contents="0.75 L")
    equivalent_result = verify_label(ApplicationData.model_validate(matching_data()), equivalent)
    assert equivalent_result.overall_status == "PASS"

    wrong = matching_extraction(abv="41% ABV", net_contents="700 ml")
    wrong_result = verify_label(ApplicationData.model_validate(matching_data()), wrong)
    failing = {field.field_name for field in wrong_result.field_results if field.status == "FAIL"}
    assert {"abv", "net_contents"}.issubset(failing)


@pytest.mark.parametrize(
    ("warning", "expected_status"),
    [
        ("WARNING: CONTAINS ALCOHOL", "PASS"),
        (None, "FAIL"),
        ("Warning: Contains Alcohol", "FAIL"),
    ],
)
def test_warning_exactness_cases(warning, expected_status):
    result = verify_label(
        ApplicationData.model_validate(matching_data()),
        matching_extraction(government_warning=warning),
    )
    warning_result = next(field for field in result.field_results if field.field_name == "government_warning")
    assert warning_result.status == expected_status


def test_imperfect_valid_image_degrades_to_needs_review_without_crashing():
    service = FixedVisionService(all_null_extraction())

    result = run(
        verify_endpoint(
            image=FakeUpload("dark-blurry-label.jpg", make_label_image(blur=True, dark=True, rotate=True)),
            application_data=json.dumps(matching_data()),
            vision_service=service,
        )
    )

    assert result.overall_status == "NEEDS_REVIEW"
    assert set(result.failed_fields or []) == {
        "brand",
        "product_class",
        "producer",
        "country",
        "abv",
        "net_contents",
        "government_warning",
    }
    assert result.latency_ms < 5000


def test_wrong_file_type_rejects_before_vision_call():
    service = FixedVisionService(matching_extraction())

    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_endpoint(
                image=FakeUpload("not-a-label.txt", b"not an image", "text/plain"),
                application_data=json.dumps(matching_data()),
                vision_service=service,
            )
        )

    assert exc_info.value.status_code == 400
    assert "Invalid image format" in exc_info.value.detail
    assert service.calls == 0


def test_corrupt_jpeg_rejects_before_vision_call():
    service = FixedVisionService(matching_extraction())

    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_endpoint(
                image=FakeUpload("broken.jpg", b"not really jpeg bytes", "image/jpeg"),
                application_data=json.dumps(matching_data()),
                vision_service=service,
            )
        )

    assert exc_info.value.status_code == 400
    assert "could not be read" in exc_info.value.detail.lower()
    assert service.calls == 0


def test_empty_submit_rejects_plainly_before_vision_call():
    service = FixedVisionService(matching_extraction())

    with pytest.raises(HTTPException) as exc_info:
        run(
            verify_endpoint(
                image=FakeUpload("empty.jpg", b"", "image/jpeg"),
                application_data=json.dumps(matching_data()),
                vision_service=service,
            )
        )

    assert exc_info.value.status_code == 400
    assert "empty" in exc_info.value.detail.lower()
    assert service.calls == 0


def test_batch_summary_counts_pass_review_and_error():
    wrong_warning = matching_extraction(government_warning="Warning: Contains Alcohol")
    service = SequenceVisionService([matching_extraction(), wrong_warning])

    result = run(
        verify_batch_endpoint(
            images=[
                FakeUpload("good.jpg", make_label_image()),
                FakeUpload("wrong-warning.jpg", make_label_image()),
                FakeUpload("bad.txt", b"not an image", "text/plain"),
            ],
            application_data=json.dumps([matching_data(), matching_data(), matching_data()]),
            vision_service=service,
        )
    )

    assert result.summary.model_dump() == {
        "total": 3,
        "passed": 1,
        "needs_review": 1,
        "errors": 1,
    }
    assert [item.status for item in result.results] == ["PASS", "NEEDS_REVIEW", "ERROR"]


def test_single_label_mocked_speed_budget_is_under_five_seconds():
    service = FixedVisionService(matching_extraction(), delay=0.05)

    start = time.perf_counter()
    result = run(
        verify_endpoint(
            image=FakeUpload("fast.jpg", make_label_image()),
            application_data=json.dumps(matching_data()),
            vision_service=service,
        )
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result.overall_status == "PASS"
    assert result.latency_ms < 5000
    assert elapsed_ms < 5000


def test_default_mock_vision_supports_phase6_image_markers_after_preprocessing():
    service = MockVisionService()

    missing_warning = run(
        service.extract(preprocess_image(make_label_image(marker="missing_warning")))
    )
    all_null = run(
        service.extract(preprocess_image(make_label_image(blur=True, dark=True, rotate=True, marker="all_null")))
    )

    assert missing_warning.brand == "Vodka Premium"
    assert missing_warning.government_warning is None
    assert all_null.brand is None
    assert all_null.government_warning is None
