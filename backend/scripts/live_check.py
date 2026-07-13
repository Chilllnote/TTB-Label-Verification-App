#!/usr/bin/env python
"""Smoke check a deployed app is using real vision, not MockVisionService.

Usage:
    python scripts/live_check.py https://your-app.example.com

The check uploads a generated JPEG label whose values intentionally differ from
MockVisionService defaults. It fails if the response appears to contain the
mock's fixed extracted values.
"""

import io
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont


MOCK_DEFAULTS = {
    "brand": "Vodka Premium",
    "product_class": "Vodka",
    "producer": "Premium Distillery Inc.",
    "country": "Russia",
    "abv": "40%",
    "net_contents": "750 ml",
    "government_warning": "WARNING: CONTAINS ALCOHOL",
}

SMOKE_DATA = {
    "brand": "Cedar Ridge Smoke Test",
    "class": "Red Wine",
    "producer": "Northstar Test Winery",
    "country": "United States of America",
    "abv": "13.5%",
    "net_contents": "750 ml",
    "government_warning": (
        "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
        "drink alcoholic beverages during pregnancy because of the risk of birth "
        "defects. (2) Consumption of alcoholic beverages impairs your ability to "
        "drive a car or operate machinery, and may cause health problems."
    ),
}


def _font(size: int):
    for font_path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size)
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def make_smoke_label() -> bytes:
    """Create a bright, high-contrast JPEG label for the live smoke test."""
    image = Image.new("RGB", (1400, 1800), color=(250, 250, 245))
    draw = ImageDraw.Draw(image)
    title_font = _font(76)
    field_font = _font(48)
    warning_font = _font(32)

    draw.rectangle((60, 60, 1340, 1740), outline=(20, 20, 20), width=6)
    draw.text((120, 120), "CEDAR RIDGE SMOKE TEST", fill=(10, 10, 10), font=title_font)
    draw.line((120, 230, 1280, 230), fill=(10, 10, 10), width=4)

    y = 300
    rows = [
        ("Brand", SMOKE_DATA["brand"]),
        ("Class", SMOKE_DATA["class"]),
        ("Producer", SMOKE_DATA["producer"]),
        ("Country", SMOKE_DATA["country"]),
        ("ABV", SMOKE_DATA["abv"]),
        ("Net Contents", SMOKE_DATA["net_contents"]),
    ]
    for label, value in rows:
        draw.text((120, y), f"{label}: {value}", fill=(10, 10, 10), font=field_font)
        y += 100

    y += 50
    draw.rectangle((100, y - 30, 1300, 1660), outline=(10, 10, 10), width=4)
    y += 20
    for line in _wrap_text(draw, SMOKE_DATA["government_warning"], warning_font, 1080):
        draw.text((140, y), line, fill=(10, 10, 10), font=warning_font)
        y += 48

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def multipart_body(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----live-check-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode())
        chunks.append(b"\r\n")

    for field_name, filename, content, content_type in files:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        chunks.append(content)
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def post_verify(base_url: str, image_bytes: bytes, timeout_seconds: float) -> tuple[int, dict[str, Any], float]:
    body, content_type = multipart_body(
        {"application_data": json.dumps(SMOKE_DATA)},
        [("image", "real-vision-smoke-label.jpg", image_bytes, "image/jpeg")],
    )
    request = Request(
        urljoin(base_url.rstrip("/") + "/", "verify"),
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )

    start = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return response.status, json.loads(response.read().decode()), elapsed_ms
    except HTTPError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        raw = exc.read().decode()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"detail": raw}
        return exc.code, data, elapsed_ms
    except URLError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return 0, {"detail": str(exc)}, elapsed_ms


def field_results_by_name(data: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in data.get("field_results") or []:
        field = item.get("field") or item.get("field_name")
        found = item.get("found")
        if found is None:
            found = item.get("extracted")
        if field:
            fields[str(field)] = "" if found is None else str(found)
    return fields


def matching_mock_fields(found_fields: dict[str, str]) -> list[str]:
    matches: list[str] = []
    for field, mock_value in MOCK_DEFAULTS.items():
        found = found_fields.get(field, "")
        if found.strip().lower() == mock_value.lower():
            matches.append(field)
    return matches


def fail(message: str, data: dict[str, Any] | None = None) -> int:
    print(f"FAIL: {message}")
    if data is not None:
        print(json.dumps(data, indent=2, sort_keys=True))
    return 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"-h", "--help"}:
        print("Usage: python scripts/live_check.py https://your-app.example.com [timeout_seconds]")
        return 0
    if len(sys.argv) not in {2, 3}:
        print("Usage: python scripts/live_check.py https://your-app.example.com [timeout_seconds]")
        return 2

    base_url = sys.argv[1]
    timeout_seconds = float(sys.argv[2]) if len(sys.argv) == 3 else 20.0
    image_bytes = make_smoke_label()
    status, data, elapsed_ms = post_verify(base_url, image_bytes, timeout_seconds)

    print("Image used: generated high-contrast JPEG synthetic label")
    print("Image filename: real-vision-smoke-label.jpg")
    print(f"Image size: {len(image_bytes)} bytes")
    print(f"HTTP status: {status}")
    print(f"Elapsed: {elapsed_ms:.1f} ms")

    if status != 200:
        return fail("Expected HTTP 200 from /verify.", data)

    if not isinstance(data, dict):
        return fail("Expected a JSON object response.")

    verdict = data.get("overall_verdict") or data.get("overall_status")
    if verdict not in {"APPROVED", "NEEDS_REVIEW", "PASS"}:
        return fail("Response is missing a recognized overall verdict.", data)

    found_fields = field_results_by_name(data)
    if not found_fields:
        return fail("Response is missing field_results with found/extracted values.", data)

    print("Found values:")
    for field in sorted(MOCK_DEFAULTS):
        print(f"  {field}: {found_fields.get(field, '')}")

    mock_matches = matching_mock_fields(found_fields)
    if len(mock_matches) >= 5:
        return fail(
            "Deployment appears to be returning MockVisionService fixed defaults "
            f"for fields: {', '.join(mock_matches)}.",
            data,
        )

    distinctive_hits = [
        value
        for value in ("Cedar Ridge", "Northstar", "13.5", "United States")
        if value.lower() in json.dumps(found_fields).lower()
    ]
    if not distinctive_hits:
        return fail(
            "Response did not include any distinctive smoke-label values. "
            "This may be a vision failure or an unexpected fixed response.",
            data,
        )

    print("PASS: live deployment did not return mock fixed defaults.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
