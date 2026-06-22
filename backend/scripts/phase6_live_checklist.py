#!/usr/bin/env python
"""Run the Phase 6 checklist against a deployed TTB Label Verification URL.

Usage:
    python scripts/phase6_live_checklist.py https://your-app.example.com
    python scripts/phase6_live_checklist.py --mock-vision https://your-app.example.com
"""

import io
import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFilter


@dataclass
class CheckResult:
    name: str
    passed: bool
    elapsed_ms: float
    status_code: int | None
    detail: str


def matching_data(**overrides) -> dict[str, str]:
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


def make_label_image(
    *,
    warning=True,
    blur=False,
    dark=False,
    rotate=False,
    marker: str | None = None,
) -> bytes:
    lines = [
        "PREMIUM VODKA",
        "Brand: Vodka Premium",
        "Class: Vodka",
        "Producer: Premium Distillery Inc.",
        "Country: Russia",
        "ABV: 40%",
        "Net Contents: 750 ml",
    ]
    if warning:
        lines.append("WARNING: CONTAINS ALCOHOL")

    bg = (244, 244, 238) if not dark else (34, 34, 34)
    fill = (10, 10, 10) if not dark else (68, 68, 68)
    img = Image.new("RGB", (900, 1200), color=bg)
    draw = ImageDraw.Draw(img)

    y = 80
    for line in lines:
        draw.text((60, y), line, fill=fill)
        y += 90

    if blur:
        img = img.filter(ImageFilter.GaussianBlur(radius=9))
    if rotate:
        img = img.rotate(8, expand=True, fillcolor=bg)

    draw = ImageDraw.Draw(img)
    if marker == "missing_warning":
        draw.rectangle((0, 0, 120, 120), fill=(235, 25, 25))
    elif marker == "all_null":
        draw.rectangle((0, 0, 120, 120), fill=(25, 65, 235))

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def multipart_body(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----phase6-{uuid.uuid4().hex}"
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


def post_multipart(
    base_url: str,
    path: str,
    fields: dict[str, str],
    files: list[tuple[str, str, bytes, str]],
) -> tuple[int, dict[str, Any] | None, float]:
    body, content_type = multipart_body(fields, files)
    request = Request(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urlopen(request, timeout=15) as response:
            elapsed_ms = (time.perf_counter() - start) * 1000
            raw = response.read().decode()
            return response.status, json.loads(raw), elapsed_ms
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


def single(base_url: str, image: bytes, data: dict[str, str], filename="label.jpg", content_type="image/jpeg"):
    return post_multipart(
        base_url,
        "/verify",
        {"application_data": json.dumps(data)},
        [("image", filename, image, content_type)],
    )


def expect(name: str, status: int, data: dict[str, Any] | None, elapsed_ms: float, predicate, detail: str):
    try:
        passed = bool(predicate(status, data or {}, elapsed_ms))
    except Exception as exc:
        passed = False
        detail = f"{detail}; check raised {exc.__class__.__name__}: {exc}"
    return CheckResult(name, passed, elapsed_ms, status, detail)


def failed_fields(data: dict[str, Any]) -> set[str]:
    return set(data.get("failed_fields") or [])


def run_checks(base_url: str, *, mock_vision: bool = False) -> list[CheckResult]:
    valid_image = make_label_image()
    no_warning_image = make_label_image(
        warning=False,
        marker="missing_warning" if mock_vision else None,
    )
    imperfect_image = make_label_image(
        blur=True,
        dark=True,
        rotate=True,
        marker="all_null" if mock_vision else None,
    )
    results: list[CheckResult] = []

    status, data, elapsed = single(base_url, valid_image, matching_data())
    results.append(
        expect(
            "valid label",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200 and d.get("overall_status") == "PASS",
            f"status={status} overall={data.get('overall_status') if data else None}",
        )
    )

    results.append(
        expect(
            "single-label speed",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200 and e < 5000 and float(d.get("latency_ms", 999999)) < 5000,
            f"client_ms={elapsed:.1f} server_ms={data.get('latency_ms') if data else None}",
        )
    )

    mismatch_data = matching_data(
        brand="Different Brand",
        **{"class": "Gin"},
        producer="Different Producer",
        country="United States",
        abv="41%",
        net_contents="700 ml",
    )
    status, data, elapsed = single(base_url, valid_image, mismatch_data)
    results.append(
        expect(
            "mismatches",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200
            and d.get("overall_status") == "NEEDS_REVIEW"
            and {"brand", "product_class", "producer", "country", "abv", "net_contents"}.issubset(
                failed_fields(d)
            ),
            f"status={status} failed={sorted(failed_fields(data or {}))}",
        )
    )

    status, data, elapsed = single(
        base_url,
        valid_image,
        matching_data(brand="vodka premium", **{"class": "vodka"}, producer="premium distillery inc."),
    )
    results.append(
        expect(
            "case-only non-warning",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200 and d.get("overall_status") == "PASS",
            f"status={status} overall={data.get('overall_status') if data else None}",
        )
    )

    status, data, elapsed = single(base_url, valid_image, matching_data(abv="40% ABV", net_contents="0.75 L"))
    results.append(
        expect(
            "ABV and units normalization",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200 and d.get("overall_status") == "PASS",
            f"status={status} overall={data.get('overall_status') if data else None}",
        )
    )

    status, data, elapsed = single(base_url, valid_image, matching_data(government_warning="WARNING: CONTAINS ALCOHOL"))
    results.append(
        expect(
            "correct warning",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200 and "government_warning" not in failed_fields(d),
            f"status={status} failed={sorted(failed_fields(data or {}))}",
        )
    )

    status, data, elapsed = single(base_url, valid_image, matching_data(government_warning="Warning: Contains Alcohol"))
    results.append(
        expect(
            "wrong-caps warning",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200
            and d.get("overall_status") == "NEEDS_REVIEW"
            and "government_warning" in failed_fields(d),
            f"status={status} failed={sorted(failed_fields(data or {}))}",
        )
    )

    status, data, elapsed = single(base_url, no_warning_image, matching_data())
    results.append(
        expect(
            "missing warning",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200
            and d.get("overall_status") == "NEEDS_REVIEW"
            and "government_warning" in failed_fields(d),
            f"status={status} failed={sorted(failed_fields(data or {}))}",
        )
    )

    status, data, elapsed = single(base_url, imperfect_image, matching_data(), filename="imperfect.jpg")
    results.append(
        expect(
            "imperfect image",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200 and d.get("overall_status") == "NEEDS_REVIEW" and e < 5000,
            f"status={status} overall={data.get('overall_status') if data else None} client_ms={elapsed:.1f}",
        )
    )

    status, data, elapsed = single(
        base_url,
        b"not an image",
        matching_data(),
        filename="not-a-label.txt",
        content_type="text/plain",
    )
    results.append(
        expect(
            "wrong file type",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 400,
            f"status={status} detail={data.get('detail') if data else None}",
        )
    )

    status, data, elapsed = post_multipart(base_url, "/verify", {}, [])
    results.append(
        expect(
            "empty submit",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 400,
            f"status={status} detail={data.get('detail') if data else None}",
        )
    )

    fields = {
        "application_data": json.dumps(
            [
                matching_data(),
                matching_data(government_warning="Warning: Contains Alcohol"),
                matching_data(),
            ]
        )
    }
    files = [
        ("images", "good.jpg", valid_image, "image/jpeg"),
        ("images", "wrong-warning.jpg", valid_image, "image/jpeg"),
        ("images", "bad.txt", b"not an image", "text/plain"),
    ]
    status, data, elapsed = post_multipart(base_url, "/verify/batch", fields, files)
    summary = data.get("summary", {}) if data else {}
    results.append(
        expect(
            "batch summary",
            status,
            data,
            elapsed,
            lambda s, d, e: s == 200
            and d.get("summary") == {"total": 3, "passed": 1, "needs_review": 1, "errors": 1},
            f"status={status} summary={summary}",
        )
    )

    return results


def main() -> int:
    args = sys.argv[1:]
    mock_vision = False
    if "--mock-vision" in args:
        mock_vision = True
        args.remove("--mock-vision")

    if len(args) != 1:
        print("Usage: python scripts/phase6_live_checklist.py [--mock-vision] https://your-app.example.com")
        return 2

    base_url = args[0].rstrip("/")
    results = run_checks(base_url, mock_vision=mock_vision)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} | {result.name} | {result.elapsed_ms:.1f}ms | HTTP {result.status_code} | {result.detail}")

    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
