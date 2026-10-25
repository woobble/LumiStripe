"""Measure real PortAudio input callback timing and buffer health.

Run on the target with a working audio input. The script enables
``LUMISTRIPE_PROFILE_AUDIO_CALLBACKS=1`` in its process before opening the
stream. Callback duration tracing keeps a bounded 512-sample window, so the
normal capture path has no added timer or lock work.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import resource
import time
from math import ceil
from pathlib import Path

from lumistripe.audio import AudioInput


def _cpu_model() -> str | None:
    try:
        contents = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    except OSError:
        return platform.processor() or None
    match = re.search(r"^(?:model name|Model)\s*:\s*(.+)$", contents, re.MULTILINE)
    return match.group(1).strip() if match else platform.processor() or None


def _rss_mb() -> float | None:
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
    return int(match.group(1)) / 1024.0 if match else None


def _summarize(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def nearest_rank(quantile: float) -> float:
        if not ordered:
            return 0.0
        index = min(max(0, ceil(len(ordered) * quantile) - 1), len(ordered) - 1)
        return ordered[index]

    return {
        "p50": nearest_rank(0.50),
        "p95": nearest_rank(0.95),
        "p99": nearest_rank(0.99),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="optional substring selecting an input device")
    parser.add_argument("--warmup", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=20.0)
    args = parser.parse_args()
    if args.warmup < 0.0 or args.duration < 2.0:
        parser.error("--warmup must be nonnegative and --duration at least 2 seconds")

    os.environ["LUMISTRIPE_PROFILE_AUDIO_CALLBACKS"] = "1"
    try:
        audio = AudioInput.with_device(args.device) if args.device else AudioInput.new()
    except RuntimeError as exc:
        parser.exit(2, f"Could not open audio input: {exc}\n")
    try:
        time.sleep(args.warmup)
        audio._clear_callback_timing_samples()
        before = audio.health()
        cpu_before = time.process_time()
        started = time.monotonic()
        time.sleep(args.duration)
        after = audio.health()
        elapsed = time.monotonic() - started
        cpu_seconds = time.process_time() - cpu_before
        callback_samples = list(audio._callback_timing_samples_ms())
        print(
            json.dumps(
                {
                    "environment": {
                        "platform": platform.platform(),
                        "architecture": platform.machine(),
                        "cpu_model": _cpu_model(),
                        "python": platform.python_version(),
                        "audio_device": audio.device_name(),
                    },
                    "duration_seconds": elapsed,
                    "callback_count": after.callback_count - before.callback_count,
                    "callbacks_per_second": (
                        after.callback_count - before.callback_count
                    )
                    / max(elapsed, 1e-9),
                    "callback_timing_sample_count": len(callback_samples),
                    "callback_duration_ms": _summarize(callback_samples),
                    "sample_count": after.processor.samples_seen
                    - before.processor.samples_seen,
                    "dropped_batches": after.dropped_batches
                    - before.dropped_batches,
                    "status_count": after.status_count - before.status_count,
                    "worker_error": after.worker_error,
                    "last_callback_age_ms": (
                        None
                        if after.last_callback_age is None
                        else after.last_callback_age * 1000.0
                    ),
                    "cpu_utilization_percent": cpu_seconds
                    / max(elapsed, 1e-9)
                    * 100.0,
                    "rss_mb": _rss_mb(),
                    "max_rss_platform_units": resource.getrusage(
                        resource.RUSAGE_SELF
                    ).ru_maxrss,
                    "timing_window_note": "Percentiles use the last at most 512 callbacks.",
                },
                indent=2,
            )
        )
    finally:
        audio.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
