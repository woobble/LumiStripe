"""Benchmark scalar references against dispatched native reactive kernels."""

from __future__ import annotations

import statistics
import sys
import time

import numpy as np
from lumistripe.render import kernels


def _checksum(frame: np.ndarray) -> int:
    return sum(memoryview(frame).cast("B"))


def _time_call(function, args: tuple[object, ...], iterations: int) -> float:
    start = time.perf_counter()
    for _ in range(iterations):
        function(*args)
    return time.perf_counter() - start


def _runner(function, make_args, mode: int):
    def run(frame: np.ndarray):
        return function, make_args(frame, mode)

    return run


def _measure_case(name: str, scalar, dispatched, pixels: int,
                  iterations: int) -> bool:
    scalar_frame = np.empty((pixels, 4), dtype=np.uint8)
    dispatch_frame = np.empty_like(scalar_frame)
    scalar_args, dispatch_args = scalar(scalar_frame), dispatched(dispatch_frame)

    scalar_function, scalar_values = scalar_args
    dispatch_function, dispatch_values = dispatch_args
    scalar_function(*scalar_values)
    dispatch_function(*dispatch_values)
    delta = int(np.abs(scalar_frame.astype(np.int16) -
                       dispatch_frame.astype(np.int16)).max(initial=0))
    if delta:
        print(f"{name}: parity failed (max byte delta {delta})")
        return False

    scalar_times: list[float] = []
    dispatch_times: list[float] = []
    for round_index in range(3):
        if round_index % 2 == 0:
            scalar_times.append(_time_call(scalar_function, scalar_values, iterations))
            dispatch_times.append(_time_call(dispatch_function, dispatch_values, iterations))
        else:
            dispatch_times.append(_time_call(dispatch_function, dispatch_values, iterations))
            scalar_times.append(_time_call(scalar_function, scalar_values, iterations))
    scalar_seconds = statistics.median(scalar_times)
    dispatch_seconds = statistics.median(dispatch_times)
    print(f"{name}: {pixels} pixels x {iterations} iterations (max delta {delta})")
    print(f"  scalar   {scalar_seconds * 1000:9.3f} ms  checksum={_checksum(scalar_frame)}")
    print(f"  candidate {dispatch_seconds * 1000:8.3f} ms  checksum={_checksum(dispatch_frame)}")
    print(f"  candidate/scalar speed ratio: {scalar_seconds / dispatch_seconds:.3f}x")
    return True


def main() -> int:
    if len(sys.argv) > 2:
        print(f"usage: {sys.argv[0]} [iterations]", file=sys.stderr)
        return 2
    try:
        iterations = int(sys.argv[1]) if len(sys.argv) == 2 else 1000
    except ValueError:
        iterations = 0
    if iterations < 1:
        print("iterations must be a positive integer", file=sys.stderr)
        return 2
    native = kernels._native_module
    required = (
        "_benchmark_render_aurora_scalar", "_benchmark_render_aurora_candidate",
        "_benchmark_render_plasma_rave_scalar", "_benchmark_render_plasma_rave_candidate",
        "_benchmark_render_beat_ripple_scalar", "_benchmark_render_beat_ripple_candidate",
    )
    if native is None or any(not hasattr(native, name) for name in required):
        print("rebuild lumistripe-core with LUMISTRIPE_BUILD_EXTENSIONS=render", file=sys.stderr)
        return 1

    bands = (0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)
    palette, custom_rgb = 0, 0x2EA7D4

    def aurora(frame: np.ndarray, mode: int):
        values = (frame, 37, mode, bands, 71.0, 0.045, 0.12, 0.31,
                  0.57, 0.35, 0.9, 23, palette, custom_rgb)
        return values

    def plasma(frame: np.ndarray, mode: int):
        values = (frame, 73.0, 0.23, 1.19, 0.41, 120, 80.0, 0.12,
                  0.9, palette, mode, int(not mode), custom_rgb)
        return values

    def ripple(frame: np.ndarray, mode: int):
        values = (frame, mode, 299.5, 84.0, 46.0, 0.83, 0.91,
                  145, 58, palette, custom_rgb)
        return values

    factories = (
        ("Aurora", aurora, "_benchmark_render_aurora_scalar", "_benchmark_render_aurora_candidate"),
        ("PlasmaRave", plasma, "_benchmark_render_plasma_rave_scalar", "_benchmark_render_plasma_rave_candidate"),
        ("BeatRipple", ripple, "_benchmark_render_beat_ripple_scalar", "_benchmark_render_beat_ripple_candidate"),
    )
    sizes = (600, 4096, 16384)
    print(f"AArch64 reactive SIMD benchmark, {iterations} iterations per round")
    for pixels in sizes:
        for name, make_args, scalar_name, dispatch_name in factories:
            for mode, mode_name in ((0, "static"), (1, "audio")):
                scalar = _runner(getattr(native, scalar_name), make_args, mode)
                dispatched = _runner(getattr(native, dispatch_name), make_args, mode)
                if not _measure_case(f"{name} {mode_name}", scalar, dispatched,
                                     pixels, iterations):
                    return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
