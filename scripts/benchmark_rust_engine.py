"""Measure local Python-to-Rust simulated frame IPC latency and throughput."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from lumistripe import Rgb
from lumistripe_web.runtime.rust_engine import RustEngineController


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, help="Path to lumistripe-engine")
    parser.add_argument("--pixels", type=_positive_int, default=80)
    parser.add_argument("--frames", type=_positive_int, default=500)
    parser.add_argument("--warmup", type=int, default=30)
    args = parser.parse_args()
    if args.warmup < 0:
        parser.error("--warmup must be zero or greater")

    controller = RustEngineController(args.pixels, binary_path=args.binary)
    try:
        controller.set_pixels(
            [
                Rgb(index % 256, (index * 3) % 256, (index * 7) % 256)
                for index in range(args.pixels)
            ]
        )
        for _ in range(args.warmup):
            controller.flush()

        samples_ms: list[float] = []
        started_at = time.perf_counter()
        for _ in range(args.frames):
            frame_started_at = time.perf_counter_ns()
            controller.flush()
            samples_ms.append((time.perf_counter_ns() - frame_started_at) / 1_000_000)
        elapsed_seconds = time.perf_counter() - started_at
    finally:
        controller.close()

    ordered = sorted(samples_ms)
    p95_index = min(len(ordered) - 1, int(0.95 * len(ordered)))
    print(
        json.dumps(
            {
                "pixels": args.pixels,
                "frames": args.frames,
                "elapsed_seconds": round(elapsed_seconds, 6),
                "frames_per_second": round(args.frames / elapsed_seconds, 2),
                "median_latency_ms": round(statistics.median(ordered), 4),
                "p95_latency_ms": round(ordered[p95_index], 4),
            },
            indent=2,
        )
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


if __name__ == "__main__":
    main()
