"""Audio calibration workflows, kept independent from device discovery."""

from __future__ import annotations

import time

from .config import AudioSmoothing
from .dsp import features_from_frame
from .types import AudioCalibrationResult, AudioFrame, MusicFeatures


def recommend_audio_calibration(
    frames: list[AudioFrame],
    features: list[MusicFeatures] | None = None,
    *,
    duration: float = 0.0,
) -> AudioCalibrationResult:
    if not frames:
        raise ValueError("audio calibration requires at least one frame")

    rms_values = [max(0.0, min(1.0, frame.rms)) for frame in frames]
    feature_values = features or [features_from_frame(frame) for frame in frames]
    energy_values = [max(0.0, min(1.0, feature.energy)) for feature in feature_values]
    floor = _percentile_value(rms_values, 20.0)
    peak = max(max(rms_values), max(energy_values, default=0.0))
    active = [value for value in energy_values if value >= max(floor * 1.8, 0.01)]
    active_level = _percentile_value(active or energy_values or rms_values, 80.0)

    noise_floor = _clamp(floor * 1.6 + 0.004, 0.003, 0.12)
    target_level = _clamp(max(active_level * 1.25, 0.24), 0.24, 0.62)
    idle_scale = _clamp(
        max(noise_floor / AudioSmoothing().noise_floor, 0.5),
        0.5,
        4.0,
    )

    return AudioCalibrationResult(
        duration=max(0.0, duration),
        samples=len(frames),
        measured_floor=floor,
        measured_peak=peak,
        recommended_noise_floor=noise_floor,
        recommended_target_level=target_level,
        recommended_idle_threshold_scale=idle_scale,
    )


def calibrate_audio_input(
    *,
    duration: float,
    device_pattern: str | None = None,
    poll_interval: float = 0.02,
) -> AudioCalibrationResult:
    if duration <= 0.0:
        raise ValueError("audio calibration duration must be > 0")

    from .capture import AudioInput

    frames: list[AudioFrame] = []
    features: list[MusicFeatures] = []
    deadline = time.monotonic() + duration
    with (
        AudioInput.with_device(device_pattern)
        if device_pattern
        else AudioInput.new()
    ) as audio_input:
        while time.monotonic() < deadline:
            frames.append(audio_input.read())
            features.append(audio_input.read_features())
            time.sleep(max(0.0, poll_interval))

    return recommend_audio_calibration(frames, features, duration=duration)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _percentile_value(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = _clamp(percentile, 0.0, 100.0) / 100.0 * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


__all__ = [
    "AudioCalibrationResult",
    "calibrate_audio_input",
    "recommend_audio_calibration",
]
