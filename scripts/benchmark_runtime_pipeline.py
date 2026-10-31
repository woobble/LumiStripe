"""Measure runtime render scheduling and optional physical LED output.

The benchmark starts the real runtime and records render scheduling, output
preparation, telemetry, publication, and independent LED output diagnostics.
Use ``--hardware`` on a device to include physical transfer measurements.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import re
import resource
import tempfile
import time
import tracemalloc
from collections import deque
from collections.abc import Callable, Iterable
from itertools import pairwise
from math import ceil
from pathlib import Path
from typing import Any

from lumistripe.playback import PlaybackMode
from lumistripe.render import kernels as render_kernels
from lumistripe_web.runtime import LumiStripeRuntime, RuntimeSettings
from lumistripe_web.settings import (
    CalibrationSettingsStore,
    StripeOutputSettings,
    StripeTopologySettings,
)

_LAYOUTS = ("mirrored", "continuous", "independent")
_STAGES = (
    "_step",
    "_flush_power_limited_outputs",
    "_update_audio_telemetry",
    "_capture_preview_frame",
    "_publish",
    "_publish_frame_updates",
)


def percentile(values: Iterable[float], quantile: float) -> float:
    """Return a nearest-rank percentile from non-empty samples."""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(max(0, ceil(len(ordered) * quantile) - 1), len(ordered) - 1)
    return ordered[index]


def summarize(values: Iterable[float]) -> dict[str, float]:
    return {
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def parse_csv_ints(value: str, *, argument: str) -> tuple[int, ...]:
    try:
        values = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{argument} must be comma-separated integers") from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError(f"{argument} values must be positive")
    return values


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
        try:
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        except (AttributeError, OSError):
            return None
        # Linux reports KiB; macOS reports bytes.
        return peak / (1024.0 if platform.system() == "Linux" else 1024.0**2)
    match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
    return int(match.group(1)) / 1024.0 if match else None


def _topology(
    led_count: int,
    layout: str,
    zones: int,
    power_limit: bool,
    *,
    hardware: bool,
    spi_devices: tuple[str, ...],
    spi_speed_hz: int,
    power_budget_watts: float,
) -> StripeTopologySettings:
    first, remainder = divmod(led_count, 2)
    pixels = (first + remainder, first)
    outputs = tuple(
        StripeOutputSettings(
            id=f"output-{index + 1}",
            name=f"Output {index + 1}",
            pixels=output_pixels,
            zone_count=zones,
            zone_pattern="alternate",
            spi_device=(spi_devices[index] if hardware else f"virtual:{index}"),
            spi_speed_hz=spi_speed_hz,
        )
        for index, output_pixels in enumerate(pixels)
    )
    return StripeTopologySettings(
        layout=layout,  # type: ignore[arg-type]
        outputs=outputs,
        power_budget_enabled=power_limit,
        power_budget_watts=power_budget_watts if power_limit else None,
    )


def _install_timers(
    runtime: LumiStripeRuntime,
    *,
    sample_limit: int | None = None,
) -> tuple[dict[str, deque[float]], dict[str, int], deque[int]]:
    timings = {
        stage: deque[float](maxlen=sample_limit) for stage in _STAGES
    }
    counts = dict.fromkeys(_STAGES, 0)
    frame_starts_ns: deque[int] = deque(maxlen=sample_limit)
    for stage in _STAGES:
        original = getattr(runtime, stage)

        def timed(
            *args: Any,
            _original: Callable[..., Any] = original,
            _stage: str = stage,
            **kwargs: Any,
        ) -> Any:
            started = time.perf_counter_ns()
            if _stage == "_step":
                frame_starts_ns.append(started)
            try:
                return _original(*args, **kwargs)
            finally:
                timings[_stage].append((time.perf_counter_ns() - started) / 1_000_000.0)
                counts[_stage] += 1

        setattr(runtime, stage, timed)
    return timings, counts, frame_starts_ns


def _run_case(
    *,
    led_count: int,
    layout: str,
    zones: int,
    mode: str,
    power_limit: bool,
    warmup_s: float,
    duration_s: float,
    trace_allocations: bool,
    hardware: bool,
    spi_devices: tuple[str, ...],
    spi_speed_hz: int,
    power_budget_watts: float,
    audio_source: str | None,
) -> dict[str, object]:
    gc.collect()
    if trace_allocations:
        tracemalloc.start()
    start_rss = _rss_mb()
    settings_file: Path | None = None
    runtime: LumiStripeRuntime | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="lumistripe-benchmark-") as directory:
            settings_file = Path(directory) / "settings.json"
            CalibrationSettingsStore(settings_file).save_stripes(
                _topology(
                    led_count,
                    layout,
                    zones,
                    power_limit,
                    hardware=hardware,
                    spi_devices=spi_devices,
                    spi_speed_hz=spi_speed_hz,
                    power_budget_watts=power_budget_watts,
                )
            )
            runtime = LumiStripeRuntime(
                RuntimeSettings(
                    pixels=led_count,
                    hardware=hardware,
                    spi_device=spi_devices[0],
                    spi_device_2=spi_devices[1],
                    spi_speed_hz=spi_speed_hz,
                    spi_speed_hz_2=spi_speed_hz,
                    audio_source=audio_source
                    or ("demo" if mode == "dynamic" else "off"),
                    settings_file=settings_file,
                )
            )
            timings, timing_counts, frame_starts_ns = _install_timers(
                runtime,
                sample_limit=512 if duration_s >= 10.0 else None,
            )
            runtime.start()
            if mode == "dynamic":
                runtime.set_mode(PlaybackMode.DYNAMIC).result(timeout=5.0)
            time.sleep(warmup_s)

            for samples in timings.values():
                samples.clear()
            timing_counts.update(dict.fromkeys(_STAGES, 0))
            frame_starts_ns.clear()
            with runtime._worker_state_lock:
                missed_before = runtime._missed_frame_count
            output_stats_before = (
                runtime._output_thread.stats()
                if runtime._output_thread is not None
                else None
            )
            if trace_allocations:
                tracemalloc.clear_traces()
                tracemalloc.reset_peak()

            cpu_before = time.process_time()
            started = time.monotonic()
            rss_samples: list[float] = []
            if duration_s >= 10.0:
                initial_sample = _rss_mb()
                if initial_sample is not None:
                    rss_samples.append(initial_sample)
                deadline = started + duration_s
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        break
                    time.sleep(min(1.0, remaining))
                    sample = _rss_mb()
                    if sample is not None:
                        rss_samples.append(sample)
            else:
                time.sleep(duration_s)
            elapsed = time.monotonic() - started
            cpu_seconds = time.process_time() - cpu_before
            with runtime._worker_state_lock:
                missed_after = runtime._missed_frame_count
                expected_interval_s = runtime._last_step_expected_delay_s
            output_stats = (
                runtime._output_thread.stats()
                if runtime._output_thread is not None
                else None
            )
            measured_frames = timing_counts["_step"]
            measured_frame_starts = list(frame_starts_ns)
            actual_frame_intervals_ms = [
                (later - earlier) / 1_000_000.0
                for earlier, later in pairwise(measured_frame_starts)
            ]
            preview = runtime.preview_frame()
            metrics: dict[str, object] = {
                "led_count": led_count,
                "layout": layout,
                "zones_per_output": zones,
                "mode": mode,
                "power_limit_enabled": power_limit,
                "power_budget_watts": power_budget_watts if power_limit else None,
                "hardware_output_enabled": hardware,
                "duration_seconds": elapsed,
                "frames": measured_frames,
                "effective_fps": measured_frames / max(elapsed, 1e-9),
                "expected_interval_ms": expected_interval_s * 1000.0,
                "actual_frame_interval_ms": summarize(actual_frame_intervals_ms),
                "actual_frame_interval_sample_count": len(actual_frame_intervals_ms),
                "missed_deadlines": missed_after - missed_before,
                "missed_deadline_rate": (missed_after - missed_before)
                / max(measured_frames, 1),
                "output_frame_rate": (
                    output_stats.output_rate if output_stats else 0.0
                ),
                "output_transmitted_frame_sets": (
                    output_stats.transmitted_frames - output_stats_before.transmitted_frames
                    if output_stats is not None and output_stats_before is not None
                    else 0
                ),
                "output_effective_fps": (
                    (
                        output_stats.transmitted_frames
                        - output_stats_before.transmitted_frames
                    )
                    / max(elapsed, 1e-9)
                    if output_stats is not None and output_stats_before is not None
                    else 0.0
                ),
                "output_dropped_frame_count": (
                    output_stats.dropped_frames - output_stats_before.dropped_frames
                    if output_stats is not None and output_stats_before is not None
                    else 0
                ),
                "output_failure_count": (
                    output_stats.failed_flushes - output_stats_before.failed_flushes
                    if output_stats is not None and output_stats_before is not None
                    else 0
                ),
                "output_heartbeat_age_seconds": (
                    max(
                        0.0,
                        time.monotonic() - output_stats.heartbeat_at_monotonic,
                    )
                    if output_stats is not None
                    and output_stats.heartbeat_at_monotonic is not None
                    else None
                ),
                "output_error": output_stats.last_error if output_stats else None,
                "cpu_utilization_percent": cpu_seconds / max(elapsed, 1e-9) * 100.0,
                "rss_start_mb": start_rss,
                "rss_end_mb": _rss_mb(),
                "rss_samples_mb": rss_samples,
                "preview_sequence": preview.sequence,
                "applied_power_scale": runtime._power_budget_state.applied_scale,
                "stage_sample_counts": {
                    stage: timing_counts[stage] for stage in timings
                },
                "timing_samples_retained": {
                    stage: len(samples) for stage, samples in timings.items()
                },
                "timings_ms": {
                    stage: summarize(samples) for stage, samples in timings.items()
                },
            }
            if trace_allocations:
                current, peak = tracemalloc.get_traced_memory()
                snapshot = tracemalloc.take_snapshot()
                metrics["allocations"] = {
                    "traced_current_bytes": current,
                    "traced_peak_bytes": peak,
                    "retained_blocks": sum(
                        statistic.count
                        for statistic in snapshot.statistics("filename")
                    ),
                    "note": "Peak traced live memory and retained blocks; not cumulative allocation traffic.",
                }
            runtime.stop()
            runtime = None
            return metrics
    finally:
        if runtime is not None:
            runtime.stop()
        if trace_allocations:
            tracemalloc.stop()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--led-counts", default="150,300,600")
    parser.add_argument("--layouts", default=",".join(_LAYOUTS))
    parser.add_argument("--zone-counts", default="1,4")
    parser.add_argument("--modes", default="static,dynamic")
    parser.add_argument("--power-limits", default="off,on")
    parser.add_argument("--warmup", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--trace-allocations", action="store_true")
    parser.add_argument(
        "--disable-native-render",
        action="store_true",
        help="force NumPy rendering kernels for native-versus-fallback comparisons",
    )
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="write benchmark frames to the configured physical SPI devices",
    )
    parser.add_argument("--spi-devices", default="/dev/spidev0.0,/dev/spidev0.1")
    parser.add_argument("--spi-speed-hz", type=int, default=1_000_000)
    parser.add_argument("--power-budget-watts", type=float, default=15.0)
    parser.add_argument(
        "--audio-source",
        choices=("auto", "off", "demo", "mic", "bluetooth", "spotify"),
        help="override the default off/static and demo/dynamic source selection",
    )
    parser.add_argument("--output", type=Path, help="write JSON to this path instead of stdout")
    args = parser.parse_args()
    args.led_counts = parse_csv_ints(args.led_counts, argument="--led-counts")
    args.zone_counts = parse_csv_ints(args.zone_counts, argument="--zone-counts")
    args.layouts = tuple(part.strip() for part in args.layouts.split(","))
    args.modes = tuple(part.strip() for part in args.modes.split(","))
    args.power_limits = tuple(part.strip() for part in args.power_limits.split(","))
    args.spi_devices = tuple(part.strip() for part in args.spi_devices.split(","))
    if not args.layouts or any(layout not in _LAYOUTS for layout in args.layouts):
        parser.error(f"--layouts values must be drawn from {', '.join(_LAYOUTS)}")
    if not args.modes or any(mode not in {"static", "dynamic"} for mode in args.modes):
        parser.error("--modes values must be static or dynamic")
    if not args.power_limits or any(
        value not in {"off", "on"} for value in args.power_limits
    ):
        parser.error("--power-limits values must be off or on")
    if args.warmup < 0.0 or args.duration <= 0.0:
        parser.error("--warmup must be nonnegative and --duration must be positive")
    if args.spi_speed_hz <= 0:
        parser.error("--spi-speed-hz must be positive")
    if args.power_budget_watts <= 0.0:
        parser.error("--power-budget-watts must be positive")
    if len(args.spi_devices) != 2 or any(not device for device in args.spi_devices):
        parser.error("--spi-devices must contain two comma-separated paths")
    if any(zones > led_count // 2 for zones in args.zone_counts for led_count in args.led_counts):
        parser.error("zone counts must not exceed half the smallest LED count")
    return args


def main() -> int:
    args = _parse_args()
    if args.disable_native_render:
        render_kernels._native_module = None
    cases = [
        _run_case(
            led_count=led_count,
            layout=layout,
            zones=zones,
            mode=mode,
            power_limit=power_limit == "on",
            warmup_s=args.warmup,
            duration_s=args.duration,
            trace_allocations=args.trace_allocations,
            hardware=args.hardware,
            spi_devices=args.spi_devices,
            spi_speed_hz=args.spi_speed_hz,
            power_budget_watts=args.power_budget_watts,
            audio_source=args.audio_source,
        )
        for led_count in args.led_counts
        for layout in args.layouts
        for zones in args.zone_counts
        for mode in args.modes
        for power_limit in args.power_limits
    ]
    result = {
        "environment": {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "cpu_model": _cpu_model(),
            "logical_cpu_count": os.cpu_count(),
            "python": platform.python_version(),
            "runtime_type": (
                "physical SPI output" if args.hardware else "simulated virtual pixel buffers"
            ),
            "hardware_output_enabled": args.hardware,
            "audio_type": args.audio_source
            or "off for static and synthetic demo for dynamic cases",
            "render_native_enabled": render_kernels._native_module is not None,
            "allocation_tracing": args.trace_allocations,
        },
        "case_count": len(cases),
        "results": cases,
    }
    rendered = json.dumps(result, indent=2)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {len(cases)} runtime benchmark cases to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
