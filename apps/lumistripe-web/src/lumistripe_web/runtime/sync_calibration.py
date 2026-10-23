"""PipeWire-routed audio cue for audio-to-LED sync calibration."""

from __future__ import annotations

import threading
import time
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
    """Play a distinctive cue through the system's PulseAudio default sink.

    The cue uses PortAudio's DAC timestamps. Its monotonic start is derived from
    the same stream clock, so browser time and browser output latency do not
    enter the LED comparison.
    """

    def __init__(self, sounddevice_module: Any | None = None) -> None:
        self._sounddevice = sounddevice_module or _load_sounddevice()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._start_at_monotonic: float | None = None
        self._start_at_stream_time: float | None = None
        self._callback_error: str | None = None
        self._closed = False
        self._sample_rate, device_index = self._select_pipewire_output()
        self._stream: Any | None = None
        self._open_stream(device_index)
        if not self._ready.wait(timeout=2.0):
            self.close()
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
            raise RuntimeError("The calibration output stream has not started.")
        return self._start_at_monotonic

    def close(self) -> None:
        with self._lock:
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

    def _callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        del status
        outdata.fill(0.0)
        try:
            current_time = float(time_info.currentTime)
            dac_time = float(time_info.outputBufferDacTime)
            if not isfinite(current_time) or not isfinite(dac_time):
                raise ValueError("The PipeWire output returned invalid timing data.")
            callback_monotonic = time.monotonic()
            stream_offset = callback_monotonic - current_time
            with self._lock:
                if self._closed:
                    return
                if self._start_at_stream_time is None:
                    self._start_at_stream_time = dac_time + SYNC_START_DELAY_S
                    self._start_at_monotonic = (
                        self._start_at_stream_time + stream_offset
                    )
                    self._ready.set()
                start_at = self._start_at_stream_time

            assert start_at is not None
            stream_times = dac_time + np.arange(frames, dtype=np.float64) / self._sample_rate
            relative = stream_times - start_at
            pattern_position = np.mod(relative, SYNC_PATTERN_PERIOD_S)
            active = relative >= 0.0
            samples = np.zeros(frames, dtype=np.float32)
            for pulse_at in SYNC_PULSE_TIMES_S:
                pulse_phase = pattern_position - pulse_at
                in_pulse = active & (pulse_phase >= 0.0) & (pulse_phase < SYNC_PULSE_DURATION_S)
                if not np.any(in_pulse):
                    continue
                phase = pulse_phase[in_pulse]
                envelope = np.minimum(1.0, phase / 0.005)
                envelope = np.minimum(
                    envelope,
                    (SYNC_PULSE_DURATION_S - phase) / 0.02,
                )
                samples[in_pulse] = (
                    np.sin(2.0 * np.pi * SYNC_TONE_FREQUENCY_HZ * phase)
                    * np.maximum(0.0, envelope)
                    * SYNC_TONE_AMPLITUDE
                ).astype(np.float32)
            outdata[:, 0] = samples
        except Exception as exc:  # noqa: BLE001 - exceptions cannot cross PortAudio callback
            with self._lock:
                self._callback_error = str(exc)
                self._ready.set()
