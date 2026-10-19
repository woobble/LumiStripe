"""Audio lifecycle helpers kept independent from the runtime worker loop."""

from __future__ import annotations

from typing import TypedDict

from lumistripe.audio.calibration import (
    recommend_audio_calibration,
    recommend_microphone_calibration,
)
from lumistripe.audio.types import AudioFrame, AudioProcessorStats, MusicFeatures


class AudioCalibrationResultData(TypedDict):
    duration_seconds: float
    samples: int
    measured_floor: float
    measured_peak: float
    recommended_noise_floor: float
    recommended_target_level: float
    recommended_hardware_gain: float | None
    recommended_idle_threshold_scale: float
    recommended_input_noise_floor: float
    measured_music_level: float
    clipping_fraction: float


def complete_audio_calibration(
    frames: list[AudioFrame],
    features: list[MusicFeatures],
    *,
    duration: float,
) -> AudioCalibrationResultData:
    """Turn captured frames into a serializable calibration recommendation."""
    recommendation = recommend_audio_calibration(frames, features, duration=duration)
    return {
        "duration_seconds": recommendation.duration,
        "samples": recommendation.samples,
        "measured_floor": recommendation.measured_floor,
        "measured_peak": recommendation.measured_peak,
        "recommended_noise_floor": recommendation.recommended_noise_floor,
        "recommended_target_level": recommendation.recommended_target_level,
        "recommended_hardware_gain": None,
        "recommended_idle_threshold_scale": recommendation.recommended_idle_threshold_scale,
        "recommended_input_noise_floor": 0.003,
        "measured_music_level": 0.0,
        "clipping_fraction": 0.0,
    }


def complete_microphone_calibration(
    quiet: list[AudioProcessorStats], music: list[AudioProcessorStats], *, duration: float,
) -> AudioCalibrationResultData:
    recommendation = recommend_microphone_calibration(quiet, music)
    return {
        "duration_seconds": duration * 2,
        "samples": len(quiet) + len(music),
        "measured_floor": recommendation.measured_floor,
        "measured_peak": recommendation.measured_peak,
        "recommended_noise_floor": 0.015,
        "recommended_target_level": recommendation.target_level,
        "recommended_hardware_gain": None,
        "recommended_idle_threshold_scale": 1.0,
        "recommended_input_noise_floor": recommendation.input_noise_floor,
        "measured_music_level": recommendation.measured_music_level,
        "clipping_fraction": recommendation.clipping_fraction,
    }


__all__ = ["AudioCalibrationResultData", "complete_audio_calibration"]
