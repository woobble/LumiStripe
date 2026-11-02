"""Measure the Python/native-C DSP path for comparison with Rust audio."""

from __future__ import annotations

import argparse
import json
import platform
import resource
import statistics
import time
import tracemalloc

import numpy as np
from lumistripe.audio import AudioConfig, AudioState
from lumistripe.audio.config import DEFAULT_SAMPLE_RATE, FFT_HOP_SIZE


def measure(iterations: int) -> list[dict[str, float | int]]:
    output: list[dict[str, float | int]] = []
    for hops_per_call in (1, 4, 16):
        batch_samples = FFT_HOP_SIZE * hops_per_call
        sample_count = iterations * FFT_HOP_SIZE
        calls = max(1, sample_count // batch_samples)
        phase = 2.0 * np.pi * np.arange(batch_samples, dtype=np.float32)
        samples = (
            0.32 * np.sin(80.0 * phase / DEFAULT_SAMPLE_RATE)
            + 0.08 * np.sin(1_600.0 * phase / DEFAULT_SAMPLE_RATE)
        ).astype(np.float32)
        state = AudioState(AudioConfig())
        for _ in range(32):
            state.feed_samples(samples)
            state.frame()
            state.music_features()
            state.take_beat_events()

        elapsed_ms: list[float] = []
        cpu_started = time.process_time()
        started = time.perf_counter()
        for _ in range(calls):
            call_started = time.perf_counter_ns()
            state.feed_samples(samples)
            state.frame()
            state.music_features()
            state.take_beat_events()
            elapsed_ms.append((time.perf_counter_ns() - call_started) / 1_000_000.0)
        wall_seconds = time.perf_counter() - started
        cpu_seconds = time.process_time() - cpu_started

        tracemalloc.start()
        tracemalloc.reset_peak()
        heap_baseline, _ = tracemalloc.get_traced_memory()
        for _ in range(calls):
            state.feed_samples(samples)
            state.frame()
            state.music_features()
            state.take_beat_events()
        python_heap_live_bytes, python_heap_peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        audio_seconds = calls * batch_samples / DEFAULT_SAMPLE_RATE
        ordered = sorted(elapsed_ms)
        output.append(
            {
                "hops_per_call": hops_per_call,
                "calls": calls,
                "simulated_audio_seconds": audio_seconds,
                "wall_seconds": wall_seconds,
                "realtime_headroom_x": audio_seconds / max(wall_seconds, 1e-9),
                "cpu_utilization_percent": cpu_seconds / max(wall_seconds, 1e-9) * 100.0,
                "feed_p50_ms": statistics.median(ordered),
                "feed_p95_ms": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
                "feed_p99_ms": ordered[min(len(ordered) - 1, int(0.99 * len(ordered)))],
                "python_heap_live_bytes_delta": max(
                    0, python_heap_live_bytes - heap_baseline
                ),
                "python_heap_peak_bytes_delta": max(
                    0, python_heap_peak_bytes - heap_baseline
                ),
                "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                / (1024.0 if platform.system() == "Linux" else 1024.0**2),
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=4096)
    args = parser.parse_args()
    if args.iterations < 64:
        parser.error("--iterations must be at least 64")
    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "iterations_512_sample_hops": args.iterations,
                "allocation_note": (
                    "tracemalloc reports Python-managed bytes only; allocations inside "
                    "the native C extension are not visible"
                ),
                "results": measure(args.iterations),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
