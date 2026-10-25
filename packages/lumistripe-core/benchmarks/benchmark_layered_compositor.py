"""Benchmark NumPy and native layered compositing on synthetic RGBA frames.

Build the ``render`` extension profile first, then run from the repository root:

    UV_CACHE_DIR=/tmp/lumistripe-uv uv run python \
      packages/lumistripe-core/benchmarks/benchmark_layered_compositor.py

The complete LayeredRenderer case uses simulated animations and an in-memory
Stripe. It does not include audio capture or physical SPI transmission.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import time
import tracemalloc
from collections.abc import Callable
from math import ceil
from typing import Any

import numpy as np
from lumistripe import (
    Animation,
    AnimationPlayer,
    AudioFrame,
    AudioSnapshot,
    Effect,
    MusicFeatures,
    Rgb,
    Rgba,
    Stripe,
)
from lumistripe.controller import Controller
from lumistripe.effects.layers import (
    ActiveEffect,
    EffectCategory,
    EffectDefinition,
    EffectScheduler,
    EffectSchedulerConfig,
    LayeredRenderer,
)
from lumistripe.render import composite_screen_rgba_into, kernels

_Backend = str
_NativeModule = Any


class _BenchmarkBase(Animation):
    @property
    def name(self) -> str:
        return "benchmark_base"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(100, 130, 170))


class _BenchmarkOverlay(Effect):
    def __init__(self, color: Rgba) -> None:
        self._color = color

    @property
    def name(self) -> str:
        return "benchmark_overlay"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(self._color)


def _percentile(samples_us: list[float], quantile: float) -> float:
    ordered = sorted(samples_us)
    index = min(max(0, ceil(len(ordered) * quantile) - 1), len(ordered) - 1)
    return ordered[index]


def _allocation_peak(callback: Callable[[], None]) -> int:
    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    callback()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return max(0, peak - baseline)


def _measure(
    callback: Callable[[], None], *, iterations: int, warmup: int, rounds: int
) -> dict[str, float | int]:
    for _ in range(warmup):
        callback()
    samples_us: list[float] = []
    wall_started = time.perf_counter_ns()
    cpu_started = time.process_time_ns()
    for _ in range(rounds):
        for _ in range(iterations):
            started = time.perf_counter_ns()
            callback()
            samples_us.append((time.perf_counter_ns() - started) / 1_000.0)
    wall_elapsed_ns = time.perf_counter_ns() - wall_started
    cpu_elapsed_ns = time.process_time_ns() - cpu_started
    median_us = _percentile(samples_us, 0.50)
    return {
        "samples": len(samples_us),
        "p50_us": round(median_us, 3),
        "p95_us": round(_percentile(samples_us, 0.95), 3),
        "p99_us": round(_percentile(samples_us, 0.99), 3),
        "effective_fps": round(1_000_000.0 / median_us, 1),
        "process_cpu_percent": round(cpu_elapsed_ns * 100.0 / wall_elapsed_ns, 1),
    }


def _select_backend(backend: _Backend, native: _NativeModule) -> None:
    kernels._native_module = native if backend == "native_c" else None


def _make_layered_renderer(
    led_count: int, overlay_count: int, snapshot: AudioSnapshot
) -> tuple[Callable[[], None], Stripe]:
    player = AnimationPlayer(transition_ms=0)
    player.add(_BenchmarkBase(), 50, 10)
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=17))
    renderer = LayeredRenderer(scheduler)
    stripe = Stripe(led_count)
    colors = (Rgba(255, 0, 0, 0.7), Rgba(0, 0, 255, 0.55))
    categories = (EffectCategory.RHYTHMIC, EffectCategory.ACCENT)
    strengths = (0.35, 0.4)
    for index in range(overlay_count):
        scheduler.active.append(
            ActiveEffect(
                EffectDefinition(
                    f"benchmark_{index}", categories[index], 3_600.0, strengths[index]
                ),
                _BenchmarkOverlay(colors[index]),
                0.0,
                snapshot.frame,
            )
        )
    tick = 0

    def render() -> None:
        nonlocal tick
        renderer.render(
            player,
            stripe,
            snapshot,
            now_s=1.0 + tick * 0.00001,
            flush=False,
        )
        tick += 1

    render()
    return render, stripe


def _run_compositor_case(
    led_count: int,
    overlay_count: int,
    *,
    native: _NativeModule,
    iterations: int,
    warmup: int,
    rounds: int,
) -> dict[str, object]:
    rng = np.random.default_rng(4100 + led_count)
    base = rng.integers(0, 256, (led_count, 4), dtype=np.uint8)
    overlays = [
        rng.integers(0, 256, (led_count, 4), dtype=np.uint8)
        for _ in range(overlay_count)
    ]
    destination = np.empty_like(base)
    strengths = (0.35, 0.4)
    arguments = (
        base,
        destination,
        0.82,
        overlays[0] if overlay_count > 0 else None,
        strengths[0] if overlay_count > 0 else 0.0,
        overlays[1] if overlay_count > 1 else None,
        strengths[1] if overlay_count > 1 else 0.0,
    )
    expected: np.ndarray | None = None
    measurements: dict[str, object] = {}
    for backend in ("numpy_fallback", "native_c"):
        if backend == "native_c" and native is None:
            continue
        _select_backend(backend, native)
        composite_screen_rgba_into(*arguments)
        if expected is None:
            expected = destination.copy()
        else:
            np.testing.assert_array_equal(destination, expected)
        run = lambda: composite_screen_rgba_into(*arguments)
        measurement = _measure(
            run, iterations=iterations, warmup=warmup, rounds=rounds
        )
        measurement["traced_peak_bytes_per_call"] = _allocation_peak(run)
        measurements[backend] = measurement
    return measurements


def _run_renderer_case(
    led_count: int,
    overlay_count: int,
    *,
    native: _NativeModule,
    snapshot: AudioSnapshot,
    iterations: int,
    warmup: int,
    rounds: int,
) -> dict[str, object]:
    measurements: dict[str, object] = {}
    expected: np.ndarray | None = None
    for backend in ("numpy_fallback", "native_c"):
        if backend == "native_c" and native is None:
            continue
        _select_backend(backend, native)
        render, stripe = _make_layered_renderer(led_count, overlay_count, snapshot)
        measurement = _measure(
            render, iterations=iterations, warmup=warmup, rounds=rounds
        )
        if expected is None:
            expected = stripe.pixels().copy()
        else:
            np.testing.assert_array_equal(stripe.pixels(), expected)
        measurement["traced_peak_bytes_per_call"] = _allocation_peak(render)
        measurements[backend] = measurement
    return measurements


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare NumPy and C layered compositor performance"
    )
    parser.add_argument("--lengths", default="150,300,600")
    parser.add_argument("--iterations", type=int, default=3_000)
    parser.add_argument("--warmup", type=int, default=500)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    try:
        lengths = tuple(int(part.strip()) for part in args.lengths.split(","))
    except ValueError:
        parser.error("--lengths must be comma-separated integers")
    if not lengths or any(length <= 0 for length in lengths):
        parser.error("lengths must be positive")
    if min(args.iterations, args.rounds) <= 0 or args.warmup < 0:
        parser.error("iterations and rounds must be positive; warmup cannot be negative")

    native = kernels._native_module
    if native is None:
        parser.error(
            "render extension is unavailable; build with "
            "LUMISTRIPE_BUILD_EXTENSIONS=render"
        )
    snapshot = AudioSnapshot.from_parts(
        AudioFrame(fresh=True), MusicFeatures(musical_impact=0.8)
    )
    try:
        results = []
        for led_count in lengths:
            for overlay_count in (0, 1, 2):
                results.append(
                    {
                        "led_count": led_count,
                        "overlay_count": overlay_count,
                        "compositor": _run_compositor_case(
                            led_count,
                            overlay_count,
                            native=native,
                            iterations=args.iterations,
                            warmup=args.warmup,
                            rounds=args.rounds,
                        ),
                        "layered_renderer": _run_renderer_case(
                            led_count,
                            overlay_count,
                            native=native,
                            snapshot=snapshot,
                            iterations=args.iterations,
                            warmup=args.warmup,
                            rounds=args.rounds,
                        ),
                    }
                )
    finally:
        kernels._native_module = native

    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "numpy": np.__version__,
                "native_extension": native.__file__,
                "workload": "synthetic in-memory render; no audio capture or SPI",
                "results": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
