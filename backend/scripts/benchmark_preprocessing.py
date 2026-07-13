#!/usr/bin/env python
"""Compare preprocessing variants without calling the vision API.

Usage:
    python scripts/benchmark_preprocessing.py scripts/sample_image_2.jpg
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.preprocessing import inspect_image, preprocess_image


VARIANTS = [
    {"name": "baseline", "max_dimension": 768, "jpeg_quality": 75, "grayscale": False, "threshold_mode": "off", "enhance_contrast": False},
    {"name": "quality_70", "max_dimension": 1024, "jpeg_quality": 70, "grayscale": False, "threshold_mode": "off", "enhance_contrast": False},
    {"name": "grayscale_70", "max_dimension": 1024, "jpeg_quality": 70, "grayscale": True, "threshold_mode": "off", "enhance_contrast": True},
    {"name": "binary_70", "max_dimension": 1024, "jpeg_quality": 70, "grayscale": True, "threshold_mode": "binary", "enhance_contrast": True},
    {"name": "adaptive_70", "max_dimension": 1024, "jpeg_quality": 70, "grayscale": True, "threshold_mode": "adaptive", "enhance_contrast": True},
    {"name": "grayscale_60", "max_dimension": 1024, "jpeg_quality": 60, "grayscale": True, "threshold_mode": "off", "enhance_contrast": True},
]


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/benchmark_preprocessing.py IMAGE_PATH")
        return 2

    image_path = Path(sys.argv[1])
    image_bytes = image_path.read_bytes()
    original_info = inspect_image(image_bytes)
    results = []

    for variant in VARIANTS:
        kwargs = {key: value for key, value in variant.items() if key != "name"}
        start = time.perf_counter()
        output = preprocess_image(image_bytes, **kwargs)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        output_info = inspect_image(output)
        results.append(
            {
                "variant": variant["name"],
                "elapsed_ms": elapsed_ms,
                "bytes": len(output),
                "size": [output_info.width, output_info.height],
                "byte_reduction_percent": round((1 - (len(output) / len(image_bytes))) * 100, 1),
            }
        )

    print(json.dumps({
        "image": str(image_path),
        "original_bytes": len(image_bytes),
        "original_size": [original_info.width, original_info.height],
        "results": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
