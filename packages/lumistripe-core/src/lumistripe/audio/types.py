"""Audio value objects shared by capture, DSP, and playback."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AudioConfig


NUM_BANDS = 8
BandTuple = tuple[float, float, float, float, float, float, float, float]
_ZERO_BANDS: BandTuple = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class AudioFrame:
    rms: float = 0.0
    bands: BandTuple = _ZERO_BANDS
    beat: bool = False
    beat_strength: float = 0.0
    sequence: int = 0
    timestamp: float = 0.0
    fresh: bool = False


@dataclass(frozen=True, slots=True)
class MusicFeatures:
    bpm: float = 120.0
    bpm_confidence: float = 0.0
    energy: float = 0.0
    volume: float = 0.0
    energy_level: float = 0.0
    bass: float = 0.0
    brightness: float = 0.0
    onset_strength: float = 0.0
    dynamic_range: float = 0.0
    beat: bool = False
    beat_strength: float = 0.0
    bands: BandTuple = _ZERO_BANDS
    bass_energy: float = 0.0
    mid_energy: float = 0.0
    treble_energy: float = 0.0
    spectral_centroid: float = 0.0
    spectral_flux: float = 0.0
    beat_confidence: float = 0.0
    rolling_loudness: float = 0.0
    program_loudness: float = 0.0
    musical_impact: float = 0.0
    silence: bool = False
    drop_detected: bool = False
    section_change: bool = False

    @property
    def beat_detected(self) -> bool:
        return self.beat


AudioFeatures = MusicFeatures


@dataclass(frozen=True, slots=True)
class AudioCalibrationResult:
    duration: float
    samples: int
    measured_floor: float
    measured_peak: float
    recommended_noise_floor: float
    recommended_target_level: float
    recommended_idle_threshold_scale: float

    def audio_config(self) -> AudioConfig:
        from .config import AudioConfig, AudioNormalization, AudioSmoothing

        return AudioConfig(
            smoothing=AudioSmoothing(noise_floor=self.recommended_noise_floor),
            normalization=AudioNormalization(target_level=self.recommended_target_level),
        )


@dataclass(frozen=True, slots=True)
class AudioInputDevice:
    index: int
    name: str


@dataclass(frozen=True, slots=True)
class AudioProcessorStats:
    feed_count: int = 0
    samples_seen: int = 0
    fft_count: int = 0
    sample_abs_sum: float = 0.0
    normalization_gain: float = 1.0
    input_rms: float = 0.0
    program_loudness: float = 0.0
    musical_impact: float = 0.0


@dataclass(frozen=True, slots=True)
class AudioInputHealth:
    callback_count: int = 0
    status_count: int = 0
    last_status: str | None = None
    last_callback_age: float | None = None
    last_frame_age: float | None = None
    dropped_batches: int = 0
    worker_error: str | None = None
    processor: AudioProcessorStats = field(default_factory=AudioProcessorStats)


class _AudioSnapshotSilence:
    def __get__(self, obj: AudioSnapshot | None, owner: type[AudioSnapshot]):
        if obj is not None:
            return obj.features.silence

        def create(
            *,
            frame: AudioFrame | None = None,
            health: AudioInputHealth | None = None,
        ) -> AudioSnapshot:
            if frame is None:
                return owner(health=health)
            silent = replace(
                AudioFrame(),
                sequence=frame.sequence,
                timestamp=frame.timestamp,
                fresh=False,
            )
            return owner(frame=silent, health=health)

        return create


@dataclass(frozen=True, slots=True)
class AudioSnapshot:
    frame: AudioFrame = field(default_factory=AudioFrame)
    features: MusicFeatures = field(default_factory=MusicFeatures)
    health: AudioInputHealth | None = None

    silence = _AudioSnapshotSilence()

    @classmethod
    def from_parts(
        cls,
        frame: AudioFrame,
        features: MusicFeatures | None = None,
        health: AudioInputHealth | None = None,
    ) -> AudioSnapshot:
        if features is None:
            from .dsp import features_from_frame

            features = features_from_frame(frame)
        return cls(frame=frame, features=features, health=health)

    @property
    def fresh(self) -> bool:
        return self.frame.fresh

    @property
    def sequence(self) -> int:
        return self.frame.sequence

    @property
    def timestamp(self) -> float:
        return self.frame.timestamp

    @property
    def low(self) -> float:
        return _clamp01(self.features.bass_energy or self.features.bass)

    @property
    def mid(self) -> float:
        return _clamp01(self.features.mid_energy or _low_mid_high(self.features.bands)[1])

    @property
    def high(self) -> float:
        return _clamp01(self.features.treble_energy or _low_mid_high(self.features.bands)[2])

    @property
    def drive(self) -> float:
        return _clamp01(
            self.features.energy * 0.48
            + self.low * 0.28
            + self.mid * 0.18
            + self.onset_strength * 0.18
        )

    @property
    def accent(self) -> float:
        beat_strength = max(self.frame.beat_strength, self.features.beat_strength)
        if self.frame.beat or self.features.beat:
            return _clamp01(
                max(beat_strength, self.features.energy * 0.5, self.onset_strength * 0.8)
            )
        return _clamp01(
            beat_strength * 0.5
            + self.onset_strength * 0.5
            + self.features.energy * 0.18
        )

    @property
    def brightness(self) -> float:
        return _clamp01(self.features.brightness)

    @property
    def onset_strength(self) -> float:
        return _clamp01(self.features.onset_strength)

    @property
    def bpm(self) -> float:
        return self.features.bpm

    @property
    def activity(self) -> float:
        return _clamp01(
            self.drive * 0.5
            + self.accent * 0.24
            + self.brightness * 0.16
            + self.high * 0.1
        )

    @property
    def bass_energy(self) -> float:
        return self.low

    @property
    def mid_energy(self) -> float:
        return self.mid

    @property
    def treble_energy(self) -> float:
        return self.high

    @property
    def spectral_centroid(self) -> float:
        return _clamp01(self.features.spectral_centroid)

    @property
    def spectral_flux(self) -> float:
        return _clamp01(self.features.spectral_flux)

    @property
    def beat_confidence(self) -> float:
        return _clamp01(self.features.beat_confidence)

    @property
    def rolling_loudness(self) -> float:
        return _clamp01(self.features.rolling_loudness)

    @property
    def musical_impact(self) -> float:
        return _clamp01(self.features.musical_impact)

    @property
    def drop_detected(self) -> bool:
        return self.features.drop_detected

    @property
    def section_change(self) -> bool:
        return self.features.section_change


def _low_mid_high(bands: tuple[float, ...]) -> tuple[float, float, float]:
    values = list(bands[:NUM_BANDS])
    values.extend(0.0 for _ in range(NUM_BANDS - len(values)))
    return (
        _clamp01((values[0] + values[1]) * 0.5),
        _clamp01((values[2] + values[3] + values[4]) / 3.0),
        _clamp01((values[5] + values[6] + values[7]) / 3.0),
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


__all__ = [
    "NUM_BANDS",
    "AudioCalibrationResult",
    "AudioFeatures",
    "AudioFrame",
    "AudioInputDevice",
    "AudioInputHealth",
    "AudioProcessorStats",
    "AudioSnapshot",
    "BandTuple",
    "MusicFeatures",
]
