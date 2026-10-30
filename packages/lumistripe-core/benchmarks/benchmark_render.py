from __future__ import annotations

import argparse
import json
import platform
import time
from math import ceil

import numpy as np
from lumistripe.render import (
    NATIVE_AVAILABLE,
    kernels,
)
from lumistripe.render.kernels import (
    _apply_alpha_scale_numpy,
    _correct_rgba_numpy,
)


def _percentiles(samples_us: list[float]) -> dict[str, float]:
    ordered = sorted(samples_us)

    def percentile(quantile: float) -> float:
        index = min(max(0, ceil(len(ordered) * quantile) - 1), len(ordered) - 1)
        return ordered[index]

    return {key: percentile(q) for key, q in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))}


def _measure(callback, iterations: int) -> dict[str, float]:
    timings: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        callback()
        timings.append((time.perf_counter_ns() - started) / 1_000.0)
    return _percentiles(timings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare reusable NumPy and native RGBA kernels")
    parser.add_argument("--lengths", default="150,300,600")
    parser.add_argument("--iterations", type=int, default=10_000)
    args = parser.parse_args()
    try:
        lengths = tuple(int(part.strip()) for part in args.lengths.split(","))
    except ValueError:
        parser.error("--lengths must be comma-separated integers")
    if not lengths or any(length <= 0 for length in lengths) or args.iterations <= 0:
        parser.error("lengths and iterations must be positive")
    if not NATIVE_AVAILABLE:
        parser.error("render extension is unavailable; build with LUMISTRIPE_BUILD_EXTENSIONS=render")

    results = []
    for length in lengths:
        source = np.random.default_rng(length).integers(
            0, 256, size=(length, 4), dtype=np.uint8
        )
        correction = np.array((193, 237, 151), dtype=np.uint16)
        corrected_numpy = np.empty_like(source)
        corrected_native = np.empty_like(source)
        scaled_numpy = np.empty_like(source)
        scaled_native = np.empty_like(source)
        rgb_scratch = np.empty((length, 3), dtype=np.uint16)
        alpha_scratch = np.empty(length, dtype=np.float64)
        scale = 0.63

        def run_numpy(
            source=source,
            corrected_numpy=corrected_numpy,
            correction=correction,
            rgb_scratch=rgb_scratch,
            scaled_numpy=scaled_numpy,
            scale=scale,
            alpha_scratch=alpha_scratch,
        ) -> None:
            _correct_rgba_numpy(source, corrected_numpy, correction, rgb_scratch)
            _apply_alpha_scale_numpy(
                source, corrected_numpy, scaled_numpy, scale, alpha_scratch
            )

        def run_native(
            source=source,
            corrected_native=corrected_native,
            correction=correction,
            scaled_native=scaled_native,
            scale=scale,
        ) -> None:
            assert kernels._native_module is not None
            kernels._native_module.correct_rgba_into(
                source, corrected_native, correction
            )
            kernels._native_module.apply_alpha_scale_into(
                source, corrected_native, scaled_native, scale
            )

        run_numpy()
        run_native()
        np.testing.assert_array_equal(corrected_native, corrected_numpy)
        np.testing.assert_array_equal(scaled_native[:, :3], scaled_numpy[:, :3])
        alpha_delta = np.abs(
            scaled_native[:, 3].astype(np.int16)
            - scaled_numpy[:, 3].astype(np.int16)
        )
        if np.max(alpha_delta, initial=0) > 1:
            raise AssertionError("native alpha scaling differs by more than one LSB")
        results.append(
            {
                "led_count": length,
                "iterations": args.iterations,
                "numpy_us": _measure(run_numpy, args.iterations),
                "native_us": _measure(run_native, args.iterations),
            }
        )

    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "native_available": NATIVE_AVAILABLE,
                "results": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
