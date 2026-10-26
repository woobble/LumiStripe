"""Compare Python and native SpectrumFlash rendering without physical I/O.

Build the render profile, then run from the repository root:

    LUMISTRIPE_BUILD_EXTENSIONS=render uv build --package lumistripe-core \
      --wheel --out-dir /tmp/lumistripe-render-wheel
    PYTHONPATH=packages/lumistripe-core/build/lib.* \
      uv run python packages/lumistripe-core/benchmarks/benchmark_spectrum_flash.py

The measurements use in-memory stripes and do not include audio capture or SPI.
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
    SpectrumFlash,
    Stripe,
)
from lumistripe.controller import Controller
from lumistripe.effects import spectrum_flash as spectrum_flash_module
from lumistripe.effects.layers import (
    ActiveEffect,
    EffectCategory,
    EffectDefinition,
    EffectScheduler,
    EffectSchedulerConfig,
    LayeredRenderer,
)
from lumistripe.render import kernels

_AUDIO = AudioFrame(
    rms=0.62,
    bands=(0.85, 0.7, 0.52, 0.61, 0.44, 0.25, 0.18, 0.3),
    beat=False,
    fresh=True,
)


class _Base(Animation):
    @property
    def name(self) -> str:
        return "benchmark_base"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(100, 130, 170))


class _Overlay(Effect):
    @property
    def name(self) -> str:
        return "benchmark_overlay"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgba(0, 80, 255, 0.42))


def _percentile(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    return ordered[min(max(ceil(len(ordered) * quantile) - 1, 0), len(ordered) - 1)]


def _measure(callback: Callable[[], None], iterations: int, warmup: int) -> dict[str, float | int]:
    for _ in range(warmup):
        callback()
    samples: list[float] = []
    wall_start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    for _ in range(iterations):
        start = time.perf_counter_ns()
        callback()
        samples.append((time.perf_counter_ns() - start) / 1_000.0)
    elapsed_wall = time.perf_counter_ns() - wall_start
    elapsed_cpu = time.process_time_ns() - cpu_start
    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    callback()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    median = _percentile(samples, 0.5)
    return {
        "samples": len(samples),
        "p50_us": round(median, 3),
        "p95_us": round(_percentile(samples, 0.95), 3),
        "p99_us": round(_percentile(samples, 0.99), 3),
        "effective_fps": round(1_000_000 / median, 1),
        "process_cpu_percent": round(elapsed_cpu * 100 / elapsed_wall, 1),
        "traced_peak_bytes": max(0, peak - before),
        "missed_60hz_deadline_percent": round(
            sum(sample > 16_666.667 for sample in samples) * 100 / len(samples), 3
        ),
    }


def _configured(mode: str) -> SpectrumFlash:
    animation = SpectrumFlash()
    animation.configure(
        {
            "width": 50,
            "decay": 50,
            "intensity": 90,
            "palette": "rainbow",
            "music_reactivity": 0 if mode == "static" else 100,
        }
    )
    return animation


def _standalone(length: int, mode: str) -> tuple[Callable[[], None], Callable[[], np.ndarray]]:
    animation = _configured(mode)
    stripe = Stripe(length)
    frame = 0

    def render() -> None:
        nonlocal frame
        audio = None if mode == "static" else animation.effective_audio(_AUDIO)
        if not animation._render_native(frame, stripe.pixels(), audio, 0.82):
            controller = animation.output_controller(stripe, 0.82)
            if audio is None:
                animation.tick(frame, controller)
            else:
                animation.tick_audio(frame, controller, audio)
        frame += 1

    return render, lambda: stripe.pixels().copy()


def _layered(length: int, effect_count: int) -> tuple[Callable[[], None], Callable[[], np.ndarray]]:
    player = AnimationPlayer(transition_ms=0)
    player.add(_Base(), 50, 10)
    snapshot = AudioSnapshot.from_parts(_AUDIO, MusicFeatures(musical_impact=0.8))
    scheduler = EffectScheduler(EffectSchedulerConfig(beat_confidence_threshold=1.0))
    renderer = LayeredRenderer(scheduler)
    stripe = Stripe(length)
    frame = 0

    def render() -> None:
        nonlocal frame
        renderer.render(player, stripe, snapshot, now_s=1.0 + frame * 0.00001, flush=False)
        frame += 1

    render()
    for index in range(effect_count):
        animation: Effect = _configured("audio") if index == 0 else _Overlay()
        scheduler.active.append(
            ActiveEffect(
                EffectDefinition(
                    f"benchmark_{index}",
                    EffectCategory.RHYTHMIC if index == 0 else EffectCategory.ACCENT,
                    3600.0,
                    0.3,
                ),
                animation,
                1.0,
                snapshot.frame,
            )
        )
    return render, lambda: stripe.pixels().copy()


def _compare(
    factory: Callable[[], tuple[Callable[[], None], Callable[[], np.ndarray]]],
    iterations: int,
    warmup: int,
) -> dict[str, object]:
    result: dict[str, object] = {}
    expected: np.ndarray | None = None
    for backend, enabled in (("python", False), ("native", True)):
        spectrum_flash_module.native_spectrum_flash_available = (
            kernels.native_spectrum_flash_available if enabled else lambda: False
        )
        render, output = factory()
        result[backend] = _measure(render, iterations, warmup)
        pixels = output()
        if expected is None:
            expected = pixels
        else:
            np.testing.assert_array_equal(pixels, expected)
    spectrum_flash_module.native_spectrum_flash_available = kernels.native_spectrum_flash_available
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lengths", default="150,300,600")
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=50)
    args = parser.parse_args()
    if not kernels.native_spectrum_flash_available():
        parser.error("render extension with SpectrumFlash kernel is unavailable")
    lengths = tuple(int(part) for part in args.lengths.split(","))
    if not lengths or min(lengths) <= 0 or args.iterations <= 0 or args.warmup < 0:
        parser.error("lengths and iterations must be positive; warmup cannot be negative")
    results: dict[str, object] = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or "unknown",
            "hardware": "simulated; no physical LEDs, audio capture, or SPI",
            "iterations": args.iterations,
            "warmup": args.warmup,
        },
        "cases": {},
    }
    cases = results["cases"]
    assert isinstance(cases, dict)
    for length in lengths:
        for mode in ("static", "audio"):
            cases[f"standalone/{mode}/{length}"] = _compare(
                lambda length=length, mode=mode: _standalone(length, mode),
                args.iterations,
                args.warmup,
            )
        for count in (0, 1, 2):
            cases[f"layered/audio/{length}/effects-{count}"] = _compare(
                lambda length=length, count=count: _layered(length, count),
                args.iterations,
                args.warmup,
            )
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
