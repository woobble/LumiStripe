"""Audio DSP configuration and stable processing constants."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FFT_SIZE = 2048
FFT_HOP_SIZE = 512
DEFAULT_SAMPLE_RATE = 44_100.0
BAND_LIMITS_HZ: tuple[tuple[float, float], ...] = (
    (20.0, 60.0),
    (60.0, 120.0),
    (120.0, 250.0),
    (250.0, 500.0),
    (500.0, 1_000.0),
    (1_000.0, 2_500.0),
    (2_500.0, 6_000.0),
    (6_000.0, 16_000.0),
)
AUDIO_FRESH_SECONDS = 0.12


@dataclass(frozen=True, slots=True)
class AudioSmoothing:
    enabled: bool = True
    noise_floor: float = 0.015
    rms_attack: float = 0.45
    rms_release: float = 0.12
    band_attack: float = 0.4
    band_release: float = 0.1
    beat_release: float = 0.18


@dataclass(frozen=True, slots=True)
class AudioNormalization:
    enabled: bool = True
    dc_block_enabled: bool = True
    target_level: float = 0.36
    min_gain: float = 0.35
    max_gain: float = 5.0
    adapt_attack: float = 0.18
    adapt_release: float = 0.18
    music_threshold: float = 0.015
    music_max_gain: float = 4.5
    silence_floor: float = 0.003


@dataclass(frozen=True, slots=True)
class AudioAnalysis:
    drop_bass_threshold: float = 0.45
    drop_bass_delta_threshold: float = 0.16
    drop_onset_threshold: float = 0.12
    section_change_threshold: float = 0.42
    section_onset_threshold: float = 0.08


@dataclass(frozen=True, slots=True)
class MicrophoneConditioning:
    enabled: bool = False
    input_noise_floor: float = 0.003
    high_pass_hz: float = 30.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.input_noise_floor) or not 0.0 <= self.input_noise_floor <= 0.5:
            raise ValueError("input_noise_floor must be between 0 and 0.5")
        if not math.isfinite(self.high_pass_hz) or not 0.0 <= self.high_pass_hz <= 100.0:
            raise ValueError("high_pass_hz must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class AudioConfig:
    smoothing: AudioSmoothing = field(default_factory=AudioSmoothing)
    normalization: AudioNormalization = field(default_factory=AudioNormalization)
    analysis: AudioAnalysis = field(default_factory=AudioAnalysis)
    microphone: MicrophoneConditioning = field(default_factory=MicrophoneConditioning)

    @classmethod
    def raw(cls) -> AudioConfig:
        return cls(
            smoothing=AudioSmoothing(enabled=False),
            normalization=AudioNormalization(enabled=False, dc_block_enabled=False),
            analysis=AudioAnalysis(),
        )


__all__ = [
    "AUDIO_FRESH_SECONDS",
    "BAND_LIMITS_HZ",
    "DEFAULT_SAMPLE_RATE",
    "FFT_HOP_SIZE",
    "FFT_SIZE",
    "AudioAnalysis",
    "AudioConfig",
    "AudioNormalization",
    "AudioSmoothing",
    "MicrophoneConditioning",
]
