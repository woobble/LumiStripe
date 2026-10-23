"""PipeWire-routed audio cue for audio-to-LED sync calibration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np
from lumistripe.audio.devices import _load_sounddevice

SYNC_PULSE_TIMES_S = (0.0, 0.19, 0.69, 1.53, 2.22)
SYNC_PULSE_DURATION_S = 0.07
SYNC_PATTERN_PERIOD_S = 3.0
SYNC_TONE_FREQUENCY_HZ = 880.0
SYNC_TONE_AMPLITUDE = 0.18
SYNC_START_DELAY_S = 1.5
_CALLBACK_SAMPLE_CAPACITY = 2048


@dataclass(frozen=True, slots=True)
class CalibrationOutputDiagnostics:
    callback_count: int = 0
    sample_rate: float = 0.0
    block_duration_ms: float = 0.0
    callback_p50_ms: float = 0.0
    callback_p95_ms: float = 0.0
    callback_p99_ms: float = 0.0
    interval_jitter_p95_ms: float = 0.0
    underflow_count: int = 0
    overflow_count: int = 0
    callback_error_count: int = 0


def sync_calibration_led_on(
    now_s: float,
    start_at_s: float,
    offset_s: float,
) -> bool:
    """Return whether the calibration flash is active at this monotonic time."""
    elapsed = now_s - start_at_s - offset_s
    if elapsed < -1e-6:
        return False
    position = max(0.0, elapsed) % SYNC_PATTERN_PERIOD_S
    return any(
        pulse_at <= position < pulse_at + SYNC_PULSE_DURATION_S
        for pulse_at in SYNC_PULSE_TIMES_S
    )


class AudioSyncCalibrationTone:
    """Play a precomputed cue through the system's PipeWire/Pulse output."""

    def __init__(self, sounddevice_module: Any | None = None) -> None:
        self._sounddevice = sounddevice_module or _load_sounddevice()
        self._start_at_monotonic: float | None = None
        self._start_at_stream_time: float | None = None
        self._callback_error: str | None = None
        self._closed = False
        self._armed = False
        self._callback_timing_ready = False
        self._latest_dac_time: float | None = None
        self._latest_stream_offset_s: float | None = None
        self._sample_rate, device_index = self._select_pipewire_output()
        self._waveform = self._build_waveform(self._sample_rate)
        self._waveform_sample_rate = self._sample_rate
        self._callback_count = 0
        self._callback_durations_ns = [0] * _CALLBACK_SAMPLE_CAPACITY
        self._callback_interval_jitter_ns = [0] * _CALLBACK_SAMPLE_CAPACITY
        self._last_callback_started_ns: int | None = None
        self._last_callback_frames = 0
        self._underflow_count = 0
        self._overflow_count = 0
        self._callback_error_count = 0
        self._diagnostics_updated_at_s = float("-inf")
        self._cached_callback_percentiles_ms = (0.0, 0.0, 0.0, 0.0)
        self._stream: Any | None = None
        self._open_stream(device_index)
        deadline = time.monotonic() + 2.0
        while (
            not self._callback_timing_ready
            and self._callback_error is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        if not self._callback_timing_ready:
            self.close()
            if self._callback_error is not None:
                raise RuntimeError(self._callback_error)
            raise RuntimeError(
                "The selected PipeWire output did not provide audio timing data."
            )
        if self._callback_error is not None:
            error = self._callback_error
            self.close()
            raise RuntimeError(error)

    @property
    def start_at_monotonic(self) -> float:
        if self._start_at_monotonic is None:
            raise RuntimeError("The calibration output stream has not been armed.")
        return self._start_at_monotonic

    def arm(self) -> None:
        """Schedule the cue after the caller has verified its PipeWire route."""
        if self._callback_error is not None:
            raise RuntimeError(self._callback_error)
        if self._latest_dac_time is None or self._latest_stream_offset_s is None:
            raise RuntimeError("The calibration output stream has no timing anchor.")
        self._start_at_stream_time = self._latest_dac_time + SYNC_START_DELAY_S
        self._start_at_monotonic = (
            self._start_at_stream_time + self._latest_stream_offset_s
        )
        self._armed = True

    def diagnostics(self) -> CalibrationOutputDiagnostics:
        count = self._callback_count
        now_s = time.monotonic()
        if now_s - self._diagnostics_updated_at_s >= 0.25:
            self._cached_callback_percentiles_ms = self._calculate_callback_percentiles(
                count
            )
            self._diagnostics_updated_at_s = now_s
        p50, p95, p99, interval_jitter_p95 = self._cached_callback_percentiles_ms
        return CalibrationOutputDiagnostics(
            callback_count=count,
            sample_rate=self._sample_rate,
            block_duration_ms=512.0 / self._sample_rate * 1000.0,
            callback_p50_ms=p50,
            callback_p95_ms=p95,
            callback_p99_ms=p99,
            interval_jitter_p95_ms=interval_jitter_p95,
            underflow_count=self._underflow_count,
            overflow_count=self._overflow_count,
            callback_error_count=self._callback_error_count,
        )

    def _calculate_callback_percentiles(
        self, count: int
    ) -> tuple[float, float, float, float]:
        sample_count = min(count, _CALLBACK_SAMPLE_CAPACITY)
        if not sample_count:
            return (0.0, 0.0, 0.0, 0.0)
        first = (count - sample_count) % _CALLBACK_SAMPLE_CAPACITY
        samples = [
            self._callback_durations_ns[(first + index) % _CALLBACK_SAMPLE_CAPACITY]
            for index in range(sample_count)
        ]
        samples.sort()
        p50 = self._duration_percentile_ms(samples, 0.50)
        p95 = self._duration_percentile_ms(samples, 0.95)
        p99 = self._duration_percentile_ms(samples, 0.99)
        interval_count = min(max(0, count - 1), _CALLBACK_SAMPLE_CAPACITY)
        interval_first = (count - interval_count) % _CALLBACK_SAMPLE_CAPACITY
        intervals = [
            self._callback_interval_jitter_ns[
                (interval_first + index) % _CALLBACK_SAMPLE_CAPACITY
            ]
            for index in range(interval_count)
        ]
        intervals.sort()
        interval_jitter_p95 = (
            self._duration_percentile_ms(intervals, 0.95) if intervals else 0.0
        )
        return (p50, p95, p99, interval_jitter_p95)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def _select_pipewire_output(self) -> tuple[float, int]:
        try:
            devices = self._sounddevice.query_devices()
            host_apis = self._sounddevice.query_hostapis()
        except Exception as exc:
            raise RuntimeError(f"Could not inspect PipeWire audio outputs: {exc}") from exc

        candidates: list[tuple[int, dict[str, Any]]] = []
        devices_by_index: dict[int, dict[str, Any]] = {}
        for device in devices:
            if int(device.get("max_output_channels", 0)) < 1:
                continue
            name = str(device.get("name", "")).casefold()
            try:
                device_index = int(device["index"])
            except (KeyError, TypeError, ValueError):
                continue
            devices_by_index[device_index] = device
            if name in {"pulse", "pipewire"}:
                priority = 0 if name == "pulse" else 1
                candidates.append((priority, device))

        for api in host_apis:
            api_name = str(api.get("name", "")).casefold()
            if "pulse" in api_name:
                priority = 2
            elif "pipewire" in api_name:
                priority = 3
            else:
                continue
            try:
                default_index = int(api.get("default_output_device", -1))
            except (TypeError, ValueError):
                continue
            default_device = devices_by_index.get(default_index)
            if default_device is not None:
                candidates.append((priority, default_device))
        if not candidates:
            raise RuntimeError(
                "Sync calibration needs a PipeWire/Pulse output visible to PortAudio. "
                "Start pipewire-pulse and select a system audio output."
            )

        candidates.sort(key=lambda item: item[0])
        device = candidates[0][1]
        try:
            device_index = int(device["index"])
            default_rate = float(device.get("default_samplerate", 48_000.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("The PipeWire output device information is invalid.") from exc
        if not isfinite(default_rate) or default_rate <= 0.0:
            default_rate = 48_000.0
        return default_rate, device_index

    def _open_stream(self, device_index: int) -> None:
        rates = tuple(dict.fromkeys((self._sample_rate, 48_000.0, 44_100.0)))
        errors: list[str] = []
        for sample_rate in rates:
            try:
                stream = self._sounddevice.OutputStream(
                    device=device_index,
                    samplerate=sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=512,
                    callback=self._callback,
                )
                self._stream = stream
                self._sample_rate = sample_rate
                if sample_rate != self._waveform_sample_rate:
                    self._waveform = self._build_waveform(sample_rate)
                    self._waveform_sample_rate = sample_rate
                stream.start()
                return
            except Exception as exc:  # noqa: BLE001 - host support varies
                errors.append(str(exc))
                stream = self._stream
                self._stream = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception as close_exc:  # noqa: BLE001 - retain open and close details
                        errors.append(f"{exc} (stream cleanup also failed: {close_exc})")
        detail = errors[-1] if errors else "no compatible sample rate"
        raise RuntimeError(f"Could not open the PipeWire calibration output: {detail}")

    @staticmethod
    def _build_waveform(sample_rate: float) -> np.ndarray:
        period_samples = round(sample_rate * SYNC_PATTERN_PERIOD_S)
        waveform = np.zeros(period_samples, dtype=np.float32)
        duration_samples = round(sample_rate * SYNC_PULSE_DURATION_S)
        phase = np.arange(duration_samples, dtype=np.float64) / sample_rate
        envelope = np.minimum(1.0, phase / 0.005)
        envelope = np.minimum(envelope, (SYNC_PULSE_DURATION_S - phase) / 0.02)
        pulse = (
            np.sin(2.0 * np.pi * SYNC_TONE_FREQUENCY_HZ * phase)
            * np.maximum(0.0, envelope)
            * SYNC_TONE_AMPLITUDE
        ).astype(np.float32)
        for pulse_at in SYNC_PULSE_TIMES_S:
            start = round(pulse_at * sample_rate)
            waveform[start : start + duration_samples] = pulse
        return waveform

    def _callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        callback_started_ns = time.perf_counter_ns()
        previous_started_ns = self._last_callback_started_ns
        if previous_started_ns is not None:
            expected_interval_ns = round(
                self._last_callback_frames * 1_000_000_000 / self._sample_rate
            )
            self._callback_interval_jitter_ns[
                self._callback_count % _CALLBACK_SAMPLE_CAPACITY
            ] = abs(callback_started_ns - previous_started_ns - expected_interval_ns)
        self._last_callback_started_ns = callback_started_ns
        self._last_callback_frames = frames
        outdata.fill(0.0)
        try:
            current_time = float(time_info.currentTime)
            dac_time = float(time_info.outputBufferDacTime)
            if not isfinite(current_time) or not isfinite(dac_time):
                raise ValueError("The PipeWire output returned invalid timing data.")
            self._latest_dac_time = dac_time
            self._latest_stream_offset_s = time.monotonic() - current_time
            self._callback_timing_ready = True
            if not self._closed and self._armed:
                self._render_callback_audio(outdata, frames, dac_time)
            if bool(getattr(status, "output_underflow", False)):
                self._underflow_count += 1
            if bool(getattr(status, "output_overflow", False)):
                self._overflow_count += 1
        except Exception as exc:  # noqa: BLE001 - exceptions cannot cross PortAudio callback
            self._callback_error = str(exc)
            self._callback_error_count += 1
            outdata.fill(0.0)
        finally:
            index = self._callback_count % _CALLBACK_SAMPLE_CAPACITY
            self._callback_durations_ns[index] = time.perf_counter_ns() - callback_started_ns
            self._callback_count += 1

    def _render_callback_audio(
        self, outdata: np.ndarray, frames: int, dac_time: float
    ) -> None:
        start_at = self._start_at_stream_time
        if start_at is None:
            return
        sample_index = round((dac_time - start_at) * self._sample_rate)
        destination = 0
        if sample_index < 0:
            destination = min(frames, -sample_index)
            sample_index = 0
        while destination < frames:
            waveform_index = sample_index % len(self._waveform)
            count = min(frames - destination, len(self._waveform) - waveform_index)
            outdata[destination : destination + count, 0] = self._waveform[
                waveform_index : waveform_index + count
            ]
            destination += count
            sample_index += count

    @staticmethod
    def _duration_percentile_ms(samples: list[int], percentile: float) -> float:
        index = min(len(samples) - 1, int((len(samples) - 1) * percentile))
        return samples[index] / 1_000_000.0
