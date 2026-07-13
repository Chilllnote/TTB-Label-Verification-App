#!/usr/bin/env python
"""Measure live /verify latency against a deployed app.

Usage:
    python scripts/benchmark_live.py https://your-app.example.com [runs] [timeout_seconds]
"""

import statistics
import sys
import time

from live_check import make_smoke_label, post_verify


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0

    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * (percent / 100)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def main() -> int:
    if len(sys.argv) not in {2, 3, 4}:
        print("Usage: python scripts/benchmark_live.py https://your-app.example.com [runs] [timeout_seconds]")
        return 2

    base_url = sys.argv[1]
    runs = int(sys.argv[2]) if len(sys.argv) >= 3 else 10
    timeout_seconds = float(sys.argv[3]) if len(sys.argv) == 4 else 30.0
    image_bytes = make_smoke_label()

    all_latencies: list[float] = []
    success_latencies: list[float] = []
    statuses: list[int] = []

    print("Image used: generated high-contrast JPEG synthetic label")
    print("Image filename: real-vision-smoke-label.jpg")
    print(f"Image size: {len(image_bytes)} bytes")
    print(f"Runs: {runs}")

    for run_number in range(1, runs + 1):
        status, _data, elapsed_ms = post_verify(base_url, image_bytes, timeout_seconds)
        statuses.append(status)
        all_latencies.append(elapsed_ms)
        if status == 200:
            success_latencies.append(elapsed_ms)
        print(f"{run_number:02d}: status={status} elapsed_ms={elapsed_ms:.1f}")
        if run_number != runs:
            time.sleep(0.5)

    print("\nAll attempts:")
    print(f"  p50_ms={statistics.median(all_latencies):.1f}")
    print(f"  p95_ms={percentile(all_latencies, 95):.1f}")
    print(f"  statuses={statuses}")

    if not success_latencies:
        print("\nSuccessful /verify attempts: 0")
        print("No successful p50/p95 available. Fix deployment credentials/provider errors and rerun.")
        return 1

    print("\nSuccessful /verify attempts:")
    print(f"  count={len(success_latencies)}")
    print(f"  p50_ms={statistics.median(success_latencies):.1f}")
    print(f"  p95_ms={percentile(success_latencies, 95):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
