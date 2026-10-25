"""Compare the Python and native BeatTunnel paths.

Build the render extension first, then run from the repository root:

    UV_CACHE_DIR=/tmp/lumistripe-uv uv run python \
      packages/lumistripe-core/benchmarks/benchmark_beat_tunnel.py

The layered cases use simulated animations, audio frames, and an in-memory
Stripe. They do not include audio capture or physical SPI transmission.
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
from types import ModuleType
from typing import Any

import numpy as np
from lumistripe import (
    Animation,
    AnimationPlayer,
    AudioFrame,
    AudioSnapshot,
    BeatTunnel,
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
from lumistripe.render import kernels

_NativeModule = Any
_AudioFrame = AudioFrame(
    rms=0.62,
    bands=(0.85, 0.7, 0.52, 0.61, 0.44, 0.25, 0.18, 0.3),
    beat=False,
    fresh=True,
)


class _BenchmarkBase(Animation):
    @property
    def name(self) -> str:
        return "benchmark_base"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(100, 130, 170))


class _BenchmarkOverlay(Effect):
    @property
    def name(self) -> str:
        return "benchmark_overlay"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgba(0, 80, 255, 0.42))


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
        "missed_60hz_deadline_percent": round(
            sum(sample_us > 16_666.667 for sample_us in samples_us)
            * 100.0
            / len(samples_us),
            3,
        ),
    }


def _select_backend(native_module: _NativeModule) -> None:
    kernels._native_module = native_module


def _configured_tunnel(mode: str) -> BeatTunnel:
    animation = BeatTunnel()
    animation.configure(
        {
            "speed": 65,
            "width": 30,
            "intensity": 88,
            "palette": "rainbow",
            "music_reactivity": 0 if mode == "static" else 100,
        }
    )
    return animation


def _make_standalone(
    led_count: int, mode: str
) -> tuple[Callable[[], None], Callable[[], np.ndarray]]:
    animation = _configured_tunnel(mode)
    stripe = Stripe(led_count)
    frame = 0

    def render() -> None:
        nonlocal frame
        audio = None if mode == "static" else animation.effective_audio(_AudioFrame)
        if not animation._render_native(frame, stripe.pixels(), audio, 0.82):
            controller = animation.output_controller(stripe, 0.82)
            if audio is None:
                animation.tick(frame, controller)
            else:
                animation.tick_audio(frame, controller, audio)
        frame += 1

    return render, lambda: stripe.pixels().copy()


def _make_layered_renderer(
    led_count: int, overlay_count: int, mode: str
) -> tuple[Callable[[], None], Callable[[], np.ndarray]]:
    player = AnimationPlayer(transition_ms=0)
    player.add(_BenchmarkBase(), 50, 10)
    snapshot = AudioSnapshot.from_parts(_AudioFrame, MusicFeatures(musical_impact=0.8))
    scheduler = EffectScheduler(
        EffectSchedulerConfig(
            beat_confidence_threshold=1.0,
            onset_threshold=1.0,
            seed=19,
        )
    )
    renderer = LayeredRenderer(scheduler)
    stripe = Stripe(led_count)
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

    # Initialize reusable buffers before seeding active effects; the first
    # renderer frame intentionally clears active state when it sees a new size.
    render()
    categories = (EffectCategory.RHYTHMIC, EffectCategory.ACCENT)
    strengths = (0.35, 0.3)
    for index in range(overlay_count):
        animation: Effect = (
            _configured_tunnel(mode) if index == 0 else _BenchmarkOverlay()
        )
        scheduler.active.append(
            ActiveEffect(
                EffectDefinition(
                    f"benchmark_{index}", categories[index], 3_600.0, strengths[index]
                ),
                animation,
                1.0,
                snapshot.frame,
            )
        )
    return render, lambda: stripe.pixels().copy()


def _run_case(
    factory: Callable[[], tuple[Callable[[], None], Callable[[], np.ndarray]]],
    *,
    native: _NativeModule,
    python_effect_module: ModuleType,
    iterations: int,
    warmup: int,
    rounds: int,
) -> dict[str, object]:
    measurements: dict[str, object] = {}
    expected: np.ndarray | None = None
    for backend, native_module in (
        ("python_effect", python_effect_module),
        ("native_effect", native),
    ):
        _select_backend(native_module)
        render, output = factory()
        measurement = _measure(
            render, iterations=iterations, warmup=warmup, rounds=rounds
        )
        pixels = output()
        if expected is None:
            expected = pixels
        else:
            np.testing.assert_array_equal(pixels, expected)
        measurement["traced_peak_bytes_per_call"] = _allocation_peak(render)
        measurements[backend] = measurement
    return measurements


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Python and native BeatTunnel rendering"
    )
    parser.add_argument("--lengths", default="150,300,600")
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    try:
        lengths = tuple(int(part.strip()) for part in args.lengths.split(","))
    except ValueError:
        parser.error("--lengths must be comma-separated integers")
    if not lengths or any(length <= 0 for length in lengths):
        parser.error("lengths must be positive")
    if min(args.iterations, args.rounds) <= 0 or args.warmup < 0:
        parser.error(
            "iterations and rounds must be positive; warmup cannot be negative"
        )

    native = kernels._native_module
    if native is None or not kernels.native_tunnel_rings_available():
        parser.error(
            "render extension with the BeatTunnel kernel is unavailable; build with "
            "LUMISTRIPE_BUILD_EXTENSIONS=render"
        )
    python_effect_module = ModuleType("lumistripe.render._native_without_tunnel")
    for method_name in (
        "correct_rgba_into",
        "apply_alpha_scale_into",
        "composite_screen_rgba_into",
    ):
        setattr(python_effect_module, method_name, getattr(native, method_name))

    results: dict[str, object] = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or "unknown",
            "hardware": "simulated; no Raspberry Pi or physical SPI",
            "iterations": args.iterations,
            "warmup": args.warmup,
            "rounds": args.rounds,
        },
        "cases": {},
    }
    cases = results["cases"]
    assert isinstance(cases, dict)
    for length in lengths:
        for mode in ("static", "audio"):
            cases[f"standalone/{mode}/{length}"] = _run_case(
                lambda length=length, mode=mode: _make_standalone(length, mode),
                native=native,
                python_effect_module=python_effect_module,
                iterations=args.iterations,
                warmup=args.warmup,
                rounds=args.rounds,
            )
            for overlay_count in (0, 1, 2):
                cases[f"layered/{mode}/{length}/effects-{overlay_count}"] = _run_case(
                    lambda length=length, overlay_count=overlay_count, mode=mode: (
                        _make_layered_renderer(length, overlay_count, mode)
                    ),
                    native=native,
                    python_effect_module=python_effect_module,
                    iterations=args.iterations,
                    warmup=args.warmup,
                    rounds=args.rounds,
                )
    _select_backend(native)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
