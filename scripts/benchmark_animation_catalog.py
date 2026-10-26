"""Measure catalog renderer frame times against their playback budgets.

Run with ``uv run python scripts/benchmark_animation_catalog.py`` on the target
device. The benchmark uses virtual pixel buffers and does not access LED
hardware.
"""

from __future__ import annotations

import argparse
from math import ceil
from time import perf_counter_ns

from lumistripe.animation.catalog import AnimationDefinition, party_catalog
from lumistripe.audio import AudioFrame
from lumistripe.stripe import Stripe


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(max(0, ceil(len(ordered) * percentile) - 1), len(ordered) - 1)
    return ordered[index]


def _measure(
    definition: AnimationDefinition,
    length: int,
    mode: str,
    warmup: int,
    samples: int,
    renderer: str,
) -> tuple[float, float, float, int]:
    animation = definition.create()
    rng = getattr(animation, "_rng", None)
    if rng is not None and hasattr(rng, "seed"):
        rng.seed(0x4C554D49)
    stripe = Stripe(length)
    quiet = AudioFrame(
        rms=0.02,
        bands=(0.01, 0.015, 0.02, 0.018, 0.016, 0.014, 0.012, 0.01),
    )
    loud = AudioFrame(
        rms=0.78,
        bands=(0.9, 0.82, 0.68, 0.58, 0.48, 0.62, 0.75, 0.86),
        beat=True,
        beat_strength=0.95,
    )
    timings: list[float] = []
    native_hits = 0
    for frame in range(warmup + samples):
        start = perf_counter_ns()
        audio = None if mode == "static" else (loud if frame % 12 == 0 else quiet)
        configured_audio = (
            None if audio is None else animation.effective_audio(audio)
        )
        rendered = (
            animation._render_native(
                frame, stripe.pixels(), configured_audio, 1.0
            )
            if renderer == "native" and animation.native_render_enabled
            else False
        )
        if frame >= warmup and rendered:
            native_hits += 1
        if not rendered:
            controller = animation.output_controller(stripe, 1.0)
            if configured_audio is None:
                animation.tick(frame, controller)
            else:
                animation.tick_audio(frame, controller, configured_audio)
        elapsed_ms = (perf_counter_ns() - start) / 1_000_000.0
        if frame >= warmup:
            timings.append(elapsed_ms)
    return (
        _percentile(timings, 0.50),
        _percentile(timings, 0.95),
        _percentile(timings, 0.99),
        native_hits,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lengths", default="60,150,300,600", help="comma-separated LED counts"
    )
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument(
        "--renderer",
        choices=("python", "native", "both"),
        default="python",
        help="measure the Python path, force native kernels, or measure both",
    )
    parser.add_argument(
        "--fail-on-over-budget",
        action="store_true",
        help="exit nonzero if any p95 exceeds that animation's frame_ms",
    )
    args = parser.parse_args()
    lengths = tuple(int(value.strip()) for value in args.lengths.split(","))
    if not lengths or any(length < 1 for length in lengths):
        parser.error("lengths must be positive LED counts")
    if args.warmup < 0 or args.frames < 1:
        parser.error("warmup must be nonnegative and frames must be positive")

    print(
        "animation,length,mode,renderer,p50_ms,p95_ms,p99_ms,native_hits,"
        "effective_fps,budget_ms,within_budget"
    )
    over_budget = False
    for definition in party_catalog():
        for length in lengths:
            for mode in ("static", "audio"):
                renderers = ("python", "native") if args.renderer == "both" else (args.renderer,)
                for renderer in renderers:
                    p50_ms, p95_ms, p99_ms, native_hits = _measure(
                        definition, length, mode, args.warmup, args.frames, renderer
                    )
                    within_budget = p95_ms <= definition.frame_ms
                    over_budget |= not within_budget
                    print(
                        f"{definition.name},{length},{mode},{renderer},{p50_ms:.6f},"
                        f"{p95_ms:.6f},{p99_ms:.6f},{native_hits},"
                        f"{1000.0 / p50_ms:.1f},"
                        f"{definition.frame_ms},{within_budget}"
                    )
    return int(args.fail_on_over_budget and over_budget)


if __name__ == "__main__":
    raise SystemExit(main())
