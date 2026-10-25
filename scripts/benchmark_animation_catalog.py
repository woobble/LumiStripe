"""Measure catalog renderer frame times against their playback budgets.

Run with ``uv run python scripts/benchmark_animation_catalog.py`` on the target
device. The benchmark uses virtual pixel buffers and does not access LED
hardware.
"""

from __future__ import annotations

import argparse
from statistics import median
from time import perf_counter_ns

from lumistripe.animation.catalog import AnimationDefinition, party_catalog
from lumistripe.audio import AudioFrame
from lumistripe.stripe import Stripe


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * percentile), len(ordered) - 1)
    return ordered[index]


def _measure(
    definition: AnimationDefinition,
    length: int,
    mode: str,
    warmup: int,
    samples: int,
) -> tuple[float, float]:
    animation = definition.create()
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
    for frame in range(warmup + samples):
        start = perf_counter_ns()
        if mode == "static":
            animation.tick(frame, stripe)
        else:
            audio = loud if frame % 12 == 0 else quiet
            animation.tick_audio(frame, stripe, audio)
        elapsed_ms = (perf_counter_ns() - start) / 1_000_000.0
        if frame >= warmup:
            timings.append(elapsed_ms)
    return median(timings), _percentile(timings, 0.95)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lengths", default="60,150,300,600", help="comma-separated LED counts"
    )
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--frames", type=int, default=40)
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

    print("animation,length,mode,median_ms,p95_ms,budget_ms,within_budget")
    over_budget = False
    for definition in party_catalog():
        for length in lengths:
            for mode in ("static", "audio"):
                median_ms, p95_ms = _measure(
                    definition, length, mode, args.warmup, args.frames
                )
                within_budget = p95_ms <= definition.frame_ms
                over_budget |= not within_budget
                print(
                    f"{definition.name},{length},{mode},{median_ms:.3f},"
                    f"{p95_ms:.3f},{definition.frame_ms},{within_budget}"
                )
    return int(args.fail_on_over_budget and over_budget)


if __name__ == "__main__":
    raise SystemExit(main())
