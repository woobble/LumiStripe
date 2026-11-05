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
from typing import Any, Literal, cast

from lumistripe.playback import PlaybackMode
from lumistripe.render import kernels as render_kernels
from lumistripe_web.runtime import LumiStripeRuntime, RuntimeSettings
from lumistripe_web.settings import (
    CalibrationSettingsStore,
    StripeOutputSettings,
    StripeTopologySettings,
)

_LAYOUTS = ("mirrored", "continuous", "independent")
_ENGINE_BACKENDS = ("python", "rust")
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


def _counter_delta(after: int | None, before: int | None) -> int | None:
    if after is None:
        return None
    return max(0, after - (before or 0))


def _output_counter_delta(
    after: Any | None,
    before: Any | None,
    *,
    field: str,
) -> int | None:
    if after is None:
        return None
    return _counter_delta(
        getattr(after, field),
        getattr(before, field, None),
    )


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


def _process_rss_mb(pid: int) -> float | None:
    """Return a Linux process's resident memory, when /proc exposes it."""

    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
    return int(match.group(1)) / 1024.0 if match else None


def _thread_cpu_snapshot(pid: int) -> dict[int, tuple[str, float]] | None:
    """Return per-thread CPU seconds from Linux procfs, or None if unavailable."""

    try:
        task_dirs = tuple(Path(f"/proc/{pid}/task").iterdir())
        ticks_per_second = float(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError):
        return None
    if ticks_per_second <= 0.0:
        return None

    snapshot: dict[int, tuple[str, float]] = {}
    for task_dir in task_dirs:
        try:
            stat = (task_dir / "stat").read_text(encoding="utf-8")
            name = (task_dir / "comm").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        close_paren = stat.rfind(")")
        if close_paren < 0:
            continue
        fields = stat[close_paren + 1 :].split()
        if len(fields) <= 12:
            continue
        try:
            tid = int(task_dir.name)
            cpu_ticks = int(fields[11]) + int(fields[12])
        except ValueError:
            continue
        snapshot[tid] = (name, cpu_ticks / ticks_per_second)
    return snapshot


def _thread_cpu_utilization(
    before: dict[int, tuple[str, float]] | None,
    after: dict[int, tuple[str, float]] | None,
    elapsed_s: float,
) -> dict[str, float] | None:
    if before is None or after is None:
        return None
    utilization: dict[str, float] = {}
    for tid in sorted(before.keys() | after.keys()):
        name, after_cpu = after.get(tid, before.get(tid, ("unknown", 0.0)))
        before_cpu = before.get(tid, (name, after_cpu))[1]
        utilization[f"{tid}:{name}"] = (
            max(0.0, after_cpu - before_cpu) / max(elapsed_s, 1e-9) * 100.0
        )
    return utilization


def _audio_backends_for_case(
    engine_backend: str,
    requested: tuple[str, ...],
) -> tuple[str, ...]:
    if engine_backend != "rust":
        return ("python",)
    return requested


def _audio_capture_metrics(
    before: Any,
    after: Any,
    elapsed_seconds: float,
) -> dict[str, object] | None:
    if before is None or after is None:
        return None

    def count_delta(field: str) -> int | None:
        start = before.capture.get(field)
        end = after.capture.get(field)
        if type(start) is not int or type(end) is not int:
            return None
        return max(0, end - start)

    def elapsed_ms(field: str) -> float | None:
        value = after.capture.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) / 1_000_000.0

    def processor_delta(field: str) -> int | None:
        start = before.processor.get(field)
        end = after.processor.get(field)
        if type(start) is not int or type(end) is not int:
            return None
        return max(0, end - start)

    def status_delta(field: str) -> int:
        return max(0, getattr(after, field) - getattr(before, field))

    device = after.device
    callbacks = count_delta("callback_count")
    return {
        "active": after.active,
        "device": device.name if device is not None else None,
        "host": device.host if device is not None else None,
        "sample_rate": device.sample_rate if device is not None else None,
        "channels": device.channels if device is not None else None,
        "callback_count": callbacks,
        "callbacks_per_second": (
            callbacks / max(elapsed_seconds, 1e-9) if callbacks is not None else None
        ),
        "callback_errors": count_delta("callback_errors"),
        "overflowed_batches": count_delta("overflowed_batches"),
        "dropped_samples": count_delta("dropped_samples"),
        "samples_processed": processor_delta("samples_seen"),
        "fft_frames_processed": processor_delta("fft_count"),
        "audio_update_sequence_delta": status_delta("audio_update_sequence"),
        "dropped_audio_updates": status_delta("dropped_audio_updates"),
        "dropped_structure_events": status_delta("dropped_structure_events"),
        "capture_latency_p50_ms": elapsed_ms("capture_latency_p50_ns"),
        "capture_latency_p95_ms": elapsed_ms("capture_latency_p95_ns"),
        "capture_latency_p99_ms": elapsed_ms("capture_latency_p99_ns"),
        "callback_duration_p50_ms": elapsed_ms("callback_duration_p50_ns"),
        "callback_duration_p95_ms": elapsed_ms("callback_duration_p95_ns"),
        "callback_duration_p99_ms": elapsed_ms("callback_duration_p99_ns"),
        "callback_interval_jitter_p95_ms": elapsed_ms(
            "callback_interval_jitter_p95_ns"
        ),
        "last_error": after.last_error,
    }


def _python_audio_capture_metrics(
    audio_input: Any,
    before: Any,
    elapsed_seconds: float,
) -> dict[str, object] | None:
    if audio_input is None or before is None:
        return None
    after = audio_input.health()
    callback_samples = audio_input._callback_timing_samples_ms()
    jitter_samples = audio_input._callback_interval_jitter_samples_ms()
    callbacks = max(0, after.callback_count - before.callback_count)
    return {
        "active": True,
        "device": audio_input.device_name(),
        "callback_count": callbacks,
        "callbacks_per_second": callbacks / max(elapsed_seconds, 1e-9),
        "status_count": max(0, after.status_count - before.status_count),
        "dropped_batches": max(0, after.dropped_batches - before.dropped_batches),
        "dropped_beat_events": max(
            0, after.dropped_beat_events - before.dropped_beat_events
        ),
        "samples_processed": max(
            0, after.processor.samples_seen - before.processor.samples_seen
        ),
        "fft_frames_processed": max(
            0, after.processor.fft_count - before.processor.fft_count
        ),
        "callback_duration_sample_count": len(callback_samples),
        "callback_duration_ms": summarize(callback_samples),
        "callback_interval_jitter_sample_count": len(jitter_samples),
        "callback_interval_jitter_ms": summarize(jitter_samples),
        "last_callback_age_ms": (
            after.last_callback_age * 1000.0
            if after.last_callback_age is not None
            else None
        ),
        "worker_error": after.worker_error,
    }


def _rust_engine_pid(runtime: LumiStripeRuntime) -> int | None:
    output_thread = getattr(runtime, "_output_thread", None)
    engine_process = getattr(output_thread, "_process", None)
    child_process = getattr(engine_process, "_process", None)
    pid = getattr(child_process, "pid", None)
    return pid if isinstance(pid, int) else None


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
    output_count: int = 2,
) -> StripeTopologySettings:
    pixels: tuple[int, ...]
    if output_count == 1:
        pixels = (led_count,)
    elif output_count == 2:
        first, remainder = divmod(led_count, 2)
        pixels = (first + remainder, first)
    else:
        raise ValueError("output_count must be one or two")
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
    output_count: int,
    layout: str,
    zones: int,
    mode: str,
    engine_backend: Literal["python", "rust"],
    audio_backend: Literal["python", "rust"],
    engine_binary: Path | None,
    power_limit: bool,
    warmup_s: float,
    duration_s: float,
    trace_allocations: bool,
    hardware: bool,
    spi_devices: tuple[str, ...],
    spi_speed_hz: int,
    power_budget_watts: float,
    audio_source: str | None,
    audio_device: str | None,
) -> dict[str, object]:
    gc.collect()
    if trace_allocations:
        tracemalloc.start()
    start_rss = _rss_mb()
    settings_file: Path | None = None
    runtime: LumiStripeRuntime | None = None
    runtime_startup_ms: float | None = None
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
                    output_count=output_count,
                )
            )
            runtime_startup_started_ns = time.perf_counter_ns()
            runtime = LumiStripeRuntime(
                RuntimeSettings(
                    pixels=led_count,
                    hardware=hardware,
                    engine_backend=engine_backend,
                    audio_backend=audio_backend,
                    engine_binary=engine_binary,
                    spi_device=spi_devices[0],
                    spi_device_2=(spi_devices[1] if output_count == 2 else None),
                    spi_speed_hz=spi_speed_hz,
                    spi_speed_hz_2=(spi_speed_hz if output_count == 2 else None),
                    audio_source=audio_source
                    or ("demo" if mode == "dynamic" else "off"),
                    audio_device=audio_device,
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
            runtime_startup_ms = (
                time.perf_counter_ns() - runtime_startup_started_ns
            ) / 1_000_000.0
            time.sleep(warmup_s)

            python_audio_input = (
                runtime._audio_input if audio_backend == "python" else None
            )
            python_audio_status_before = None
            if python_audio_input is not None:
                python_audio_input._clear_callback_timing_samples()
                python_audio_status_before = python_audio_input.health()
            audio_status_before = (
                runtime._output_thread.audio_status()
                if audio_backend == "rust" and runtime._output_thread is not None
                else None
            )

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

            rust_engine_pid = (
                _rust_engine_pid(runtime) if engine_backend == "rust" else None
            )
            python_threads_before = _thread_cpu_snapshot(os.getpid())
            rust_threads_before = (
                _thread_cpu_snapshot(rust_engine_pid)
                if rust_engine_pid is not None
                else None
            )
            rust_engine_rss_start = (
                _process_rss_mb(rust_engine_pid)
                if rust_engine_pid is not None
                else None
            )
            cpu_before = time.process_time()
            started = time.monotonic()
            rss_samples: list[float] = []
            rust_engine_rss_samples: list[float] = []
            if duration_s >= 10.0:
                initial_sample = _rss_mb()
                if initial_sample is not None:
                    rss_samples.append(initial_sample)
                rust_initial_sample = (
                    _process_rss_mb(rust_engine_pid)
                    if rust_engine_pid is not None
                    else None
                )
                if rust_initial_sample is not None:
                    rust_engine_rss_samples.append(rust_initial_sample)
                deadline = started + duration_s
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        break
                    time.sleep(min(1.0, remaining))
                    sample = _rss_mb()
                    if sample is not None:
                        rss_samples.append(sample)
                    rust_sample = (
                        _process_rss_mb(rust_engine_pid)
                        if rust_engine_pid is not None
                        else None
                    )
                    if rust_sample is not None:
                        rust_engine_rss_samples.append(rust_sample)
            else:
                time.sleep(duration_s)
            elapsed = time.monotonic() - started
            cpu_seconds = time.process_time() - cpu_before
            python_threads_after = _thread_cpu_snapshot(os.getpid())
            rust_threads_after = (
                _thread_cpu_snapshot(rust_engine_pid)
                if rust_engine_pid is not None
                else None
            )
            rust_engine_cpu_seconds = (
                max(
                    0.0,
                    sum(cpu for _, cpu in rust_threads_after.values())
                    - sum(cpu for _, cpu in rust_threads_before.values()),
                )
                if rust_threads_before is not None and rust_threads_after is not None
                else None
            )
            if rust_engine_pid is None:
                combined_cpu_utilization = (
                    cpu_seconds / max(elapsed, 1e-9) * 100.0
                )
            elif rust_engine_cpu_seconds is not None:
                combined_cpu_utilization = (
                    (cpu_seconds + rust_engine_cpu_seconds)
                    / max(elapsed, 1e-9)
                    * 100.0
                )
            else:
                combined_cpu_utilization = None
            with runtime._worker_state_lock:
                missed_after = runtime._missed_frame_count
                expected_interval_s = runtime._last_step_expected_delay_s
            output_stats = (
                runtime._output_thread.stats()
                if runtime._output_thread is not None
                else None
            )
            audio_status_after = (
                runtime._output_thread.audio_status()
                if audio_backend == "rust" and runtime._output_thread is not None
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
                "output_count": output_count,
                "layout": layout,
                "zones_per_output": zones,
                "mode": mode,
                "engine_backend": engine_backend,
                "audio_backend": audio_backend,
                "power_limit_enabled": power_limit,
                "power_budget_watts": power_budget_watts if power_limit else None,
                "hardware_output_enabled": hardware,
                "runtime_startup_ms": runtime_startup_ms,
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
                "output_frame_latency_ms": (
                    output_stats.frame_latency_ms if output_stats else None
                ),
                "output_frame_latency_p95_ms": (
                    output_stats.frame_latency_p95_ms if output_stats else None
                ),
                "output_frame_latency_p50_ms": (
                    output_stats.frame_latency_p50_ms if output_stats else None
                ),
                "output_frame_latency_p99_ms": (
                    output_stats.frame_latency_p99_ms if output_stats else None
                ),
                "output_queue_latency_ms": (
                    output_stats.queue_latency_ms if output_stats else None
                ),
                "output_transmission_ms": (
                    output_stats.transmission_ms if output_stats else None
                ),
                "output_render_time_ms": (
                    output_stats.render_time_ms if output_stats else None
                ),
                "output_ipc_latency_ms": (
                    output_stats.ipc_latency_ms if output_stats else None
                ),
                "output_audio_feature_to_led_latency_ms": (
                    output_stats.audio_feature_to_led_latency_ms
                    if output_stats
                    else None
                ),
                "output_audio_feature_to_led_p50_ms": (
                    output_stats.audio_feature_to_led_p50_ms if output_stats else None
                ),
                "output_audio_feature_to_led_p95_ms": (
                    output_stats.audio_feature_to_led_p95_ms if output_stats else None
                ),
                "output_audio_feature_to_led_p99_ms": (
                    output_stats.audio_feature_to_led_p99_ms if output_stats else None
                ),
                "output_audio_feature_to_led_jitter_p99_minus_p50_ms": (
                    output_stats.audio_feature_to_led_p99_ms
                    - output_stats.audio_feature_to_led_p50_ms
                    if output_stats is not None
                    and output_stats.audio_feature_to_led_p99_ms is not None
                    and output_stats.audio_feature_to_led_p50_ms is not None
                    else None
                ),
                "output_frame_latency_sample_count": (
                    output_stats.frame_latency_sample_count if output_stats else 0
                ),
                "output_scheduler_missed_deadlines": _output_counter_delta(
                    output_stats,
                    output_stats_before,
                    field="scheduler_missed_deadlines",
                ),
                "audio_capture": (
                    _audio_capture_metrics(
                        audio_status_before,
                        audio_status_after,
                        elapsed,
                    )
                    if audio_backend == "rust"
                    else _python_audio_capture_metrics(
                        python_audio_input,
                        python_audio_status_before,
                        elapsed,
                    )
                ),
                "output_control_update_count": _output_counter_delta(
                    output_stats,
                    output_stats_before,
                    field="scene_control_revision",
                ),
                "output_audio_update_count": _output_counter_delta(
                    output_stats,
                    output_stats_before,
                    field="audio_update_sequence",
                ),
                "output_control_updates_replaced": (
                    output_stats.scene_updates_replaced
                    - output_stats_before.scene_updates_replaced
                    if output_stats is not None and output_stats_before is not None
                    else 0
                ),
                "output_audio_updates_replaced": (
                    output_stats.audio_updates_replaced
                    - output_stats_before.audio_updates_replaced
                    if output_stats is not None and output_stats_before is not None
                    else 0
                ),
                "output_pending_control_updates": (
                    output_stats.pending_scene_updates if output_stats else None
                ),
                "output_audio_update_pending": (
                    output_stats.pending_audio_update if output_stats else None
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
                "python_process_cpu_utilization_percent": cpu_seconds
                / max(elapsed, 1e-9)
                * 100.0,
                "rust_engine_process_cpu_utilization_percent": (
                    rust_engine_cpu_seconds / max(elapsed, 1e-9) * 100.0
                    if rust_engine_cpu_seconds is not None
                    else None
                ),
                "combined_process_cpu_utilization_percent": combined_cpu_utilization,
                "thread_cpu_utilization_percent": {
                    "python": _thread_cpu_utilization(
                        python_threads_before, python_threads_after, elapsed
                    ),
                    "rust_engine": _thread_cpu_utilization(
                        rust_threads_before, rust_threads_after, elapsed
                    ),
                },
                "rss_start_mb": start_rss,
                "rss_end_mb": _rss_mb(),
                "rss_samples_mb": rss_samples,
                "rust_engine_rss_start_mb": rust_engine_rss_start,
                "rust_engine_rss_end_mb": (
                    _process_rss_mb(rust_engine_pid)
                    if rust_engine_pid is not None
                    else None
                ),
                "rust_engine_rss_samples_mb": rust_engine_rss_samples,
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
    parser.add_argument("--output-counts", default="2")
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
    parser.add_argument("--engine-backends", default="python")
    parser.add_argument(
        "--audio-backends",
        default="python",
        help="audio backends to compare (Rust audio requires the Rust engine and a live source)",
    )
    parser.add_argument("--engine-binary", type=Path)
    parser.add_argument(
        "--audio-source",
        choices=("auto", "off", "demo", "mic", "bluetooth", "spotify"),
        help="override the default off/static and demo/dynamic source selection",
    )
    parser.add_argument(
        "--audio-device",
        help="select an input by host/device selector or matching device name",
    )
    parser.add_argument("--output", type=Path, help="write JSON to this path instead of stdout")
    args = parser.parse_args()
    args.led_counts = parse_csv_ints(args.led_counts, argument="--led-counts")
    args.output_counts = parse_csv_ints(
        args.output_counts, argument="--output-counts"
    )
    if any(count not in (1, 2) for count in args.output_counts):
        parser.error("--output-counts values must be 1 or 2")
    args.zone_counts = parse_csv_ints(args.zone_counts, argument="--zone-counts")
    args.layouts = tuple(part.strip() for part in args.layouts.split(","))
    args.modes = tuple(part.strip() for part in args.modes.split(","))
    args.power_limits = tuple(part.strip() for part in args.power_limits.split(","))
    args.engine_backends = tuple(
        part.strip() for part in args.engine_backends.split(",")
    )
    args.audio_backends = tuple(
        part.strip() for part in args.audio_backends.split(",")
    )
    args.spi_devices = tuple(part.strip() for part in args.spi_devices.split(","))
    if not args.layouts or any(layout not in _LAYOUTS for layout in args.layouts):
        parser.error(f"--layouts values must be drawn from {', '.join(_LAYOUTS)}")
    if not args.modes or any(mode not in {"static", "dynamic"} for mode in args.modes):
        parser.error("--modes values must be static or dynamic")
    if not args.power_limits or any(
        value not in {"off", "on"} for value in args.power_limits
    ):
        parser.error("--power-limits values must be off or on")
    if not args.engine_backends or any(
        backend not in _ENGINE_BACKENDS for backend in args.engine_backends
    ):
        parser.error("--engine-backends values must be python or rust")
    if len(set(args.engine_backends)) != len(args.engine_backends):
        parser.error("--engine-backends values must be unique")
    if not args.audio_backends or any(
        backend not in {"python", "rust"} for backend in args.audio_backends
    ):
        parser.error("--audio-backends values must be python or rust")
    if len(set(args.audio_backends)) != len(args.audio_backends):
        parser.error("--audio-backends values must be unique")
    if "rust" in args.audio_backends:
        if "rust" not in args.engine_backends:
            parser.error("Rust audio requires --engine-backends to include rust")
        if args.audio_source not in {"mic", "bluetooth", "spotify"}:
            parser.error(
                "Rust audio requires --audio-source mic, bluetooth, or spotify; "
                "the demo source is generated by Python"
            )
    if "rust" in args.engine_backends:
        if args.engine_binary is None:
            args.engine_binary = (
                Path(__file__).resolve().parents[1]
                / "rust"
                / "target"
                / "release"
                / "lumistripe-engine"
            )
        if not args.engine_binary.is_file():
            parser.error(f"Rust engine binary does not exist: {args.engine_binary}")
    if args.warmup < 0.0 or args.duration <= 0.0:
        parser.error("--warmup must be nonnegative and --duration must be positive")
    if args.spi_speed_hz <= 0:
        parser.error("--spi-speed-hz must be positive")
    if args.power_budget_watts <= 0.0:
        parser.error("--power-budget-watts must be positive")
    if len(args.spi_devices) != 2 or any(not device for device in args.spi_devices):
        parser.error("--spi-devices must contain two comma-separated paths")
    if any(
        zones > (led_count if output_count == 1 else led_count // 2)
        for zones in args.zone_counts
        for led_count in args.led_counts
        for output_count in args.output_counts
    ):
        parser.error("zone counts must not exceed the smallest output pixel count")
    return args


def main() -> int:
    args = _parse_args()
    if args.audio_source in {"mic", "bluetooth", "spotify"}:
        os.environ["LUMISTRIPE_PROFILE_AUDIO_CALLBACKS"] = "1"
    if args.disable_native_render:
        render_kernels._native_module = None
    cases = [
        _run_case(
            led_count=led_count,
            output_count=output_count,
            layout=layout,
            zones=zones,
            mode=mode,
            engine_backend=cast(Literal["python", "rust"], engine_backend),
            audio_backend=cast(Literal["python", "rust"], audio_backend),
            engine_binary=args.engine_binary if engine_backend == "rust" else None,
            power_limit=power_limit == "on",
            warmup_s=args.warmup,
            duration_s=args.duration,
            trace_allocations=args.trace_allocations,
            hardware=args.hardware,
            spi_devices=args.spi_devices,
            spi_speed_hz=args.spi_speed_hz,
            power_budget_watts=args.power_budget_watts,
            audio_source=args.audio_source,
            audio_device=args.audio_device,
        )
        for led_count in args.led_counts
        for output_count in args.output_counts
        for layout in args.layouts
        for zones in args.zone_counts
        for mode in args.modes
        for power_limit in args.power_limits
        for engine_backend in args.engine_backends
        for audio_backend in _audio_backends_for_case(
            engine_backend,
            args.audio_backends,
        )
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
            "engine_backends": args.engine_backends,
            "audio_backends": args.audio_backends,
            "output_counts": args.output_counts,
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
