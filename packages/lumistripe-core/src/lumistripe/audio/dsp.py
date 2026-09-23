"""DSP adapter and feature derivation independent of audio capture."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any, cast

import numpy as np
import numpy.typing as npt

from .config import DEFAULT_SAMPLE_RATE, AudioConfig
from .types import (
    NUM_BANDS,
    AudioFrame,
    AudioProcessorStats,
    BandTuple,
    MusicFeatures,
)

_native_audio: ModuleType | None
try:
    from . import _audio as _native_audio
except (ImportError, OSError):
    _native_audio = None
_audio = _native_audio


@dataclass(frozen=True, slots=True)
class NativeFrameResult:
    rms: float
    bands: BandTuple
    beat: bool
    beat_strength: float
    sequence: int

    @classmethod
    def from_native(cls, value: object) -> NativeFrameResult:
        return cls(
            rms=float(_native_field(value, "rms", 0)),
            bands=_bands(_native_field(value, "bands", 1)),
            beat=bool(_native_field(value, "beat", 2)),
            beat_strength=float(_native_field(value, "beat_strength", 3)),
            sequence=int(_native_field(value, "sequence", 4)),
        )


@dataclass(frozen=True, slots=True)
class NativeFeaturesResult:
    bpm: float
    energy: float
    bass: float
    brightness: float
    onset_strength: float
    dynamic_range: float
    beat: bool
    beat_strength: float
    bands: BandTuple
    bass_energy: float
    mid_energy: float
    treble_energy: float
    spectral_centroid: float
    spectral_flux: float
    beat_confidence: float
    rolling_loudness: float
    silence: bool
    drop_detected: bool
    section_change: bool
    program_loudness: float
    musical_impact: float

    @classmethod
    def from_native(cls, value: object) -> NativeFeaturesResult:
        return cls(
            bpm=float(_native_field(value, "bpm", 0)),
            energy=float(_native_field(value, "energy", 1)),
            bass=float(_native_field(value, "bass", 2)),
            brightness=float(_native_field(value, "brightness", 3)),
            onset_strength=float(_native_field(value, "onset_strength", 4)),
            dynamic_range=float(_native_field(value, "dynamic_range", 5)),
            beat=bool(_native_field(value, "beat", 6)),
            beat_strength=float(_native_field(value, "beat_strength", 7)),
            bands=_bands(_native_field(value, "bands", 8)),
            bass_energy=float(_native_field(value, "bass_energy", 9)),
            mid_energy=float(_native_field(value, "mid_energy", 10)),
            treble_energy=float(_native_field(value, "treble_energy", 11)),
            spectral_centroid=float(_native_field(value, "spectral_centroid", 12)),
            spectral_flux=float(_native_field(value, "spectral_flux", 13)),
            beat_confidence=float(_native_field(value, "beat_confidence", 14)),
            rolling_loudness=float(_native_field(value, "rolling_loudness", 15)),
            silence=bool(_native_field(value, "silence", 16)),
            drop_detected=bool(_native_field(value, "drop_detected", 17)),
            section_change=bool(_native_field(value, "section_change", 18)),
            program_loudness=float(_native_field(value, "program_loudness", 19)),
            musical_impact=float(_native_field(value, "musical_impact", 20)),
        )


@dataclass(frozen=True, slots=True)
class NativeStatsResult:
    feed_count: int
    samples_seen: int
    fft_count: int
    sample_abs_sum: float
    normalization_gain: float
    input_rms: float
    program_loudness: float
    musical_impact: float

    @classmethod
    def from_native(cls, value: object) -> NativeStatsResult:
        return cls(
            feed_count=int(_native_field(value, "feed_count", 0)),
            samples_seen=int(_native_field(value, "samples_seen", 1)),
            fft_count=int(_native_field(value, "fft_count", 2)),
            sample_abs_sum=float(_native_field(value, "sample_abs_sum", 3)),
            normalization_gain=float(_native_field(value, "normalization_gain", 4)),
            input_rms=float(_native_field(value, "input_rms", 5)),
            program_loudness=float(_native_field(value, "program_loudness", 6)),
            musical_impact=float(_native_field(value, "musical_impact", 7)),
        )


def _native_field(value: object, name: str, index: int) -> Any:
    if isinstance(value, Mapping):
        return value[name]
    # This compatibility branch lets an already-installed older extension keep
    # working while a wheel is rebuilt with the named native result contract.
    return cast(tuple[object, ...], value)[index]


def _bands(value: object) -> BandTuple:
    values = [float(item) for item in cast(Any, value)]
    values = (values + [0.0] * NUM_BANDS)[:NUM_BANDS]
    return cast(BandTuple, tuple(values))


def _require_native_audio() -> ModuleType:
    if _audio is None:
        raise RuntimeError(
            "the native audio processor is not installed; build lumistripe-core "
            "with LUMISTRIPE_BUILD_EXTENSIONS=audio or install a native wheel"
        )
    return _audio


def features_from_frame(frame: AudioFrame) -> MusicFeatures:
    low, mid, high = _low_mid_high(frame.bands)
    brightness = _clamp01(high * 0.55 + frame.rms * 0.25 + (1.0 - low) * high * 0.2)
    onset = _clamp01(frame.beat_strength if frame.beat else frame.rms * 0.18)
    silence = frame.rms <= 0.003 and max(frame.bands, default=0.0) <= 0.003
    return MusicFeatures(
        bpm=120.0,
        energy=frame.rms,
        volume=frame.rms,
        energy_level=frame.rms,
        bass=low,
        brightness=brightness,
        onset_strength=onset,
        dynamic_range=frame.beat_strength,
        beat=frame.beat,
        beat_strength=frame.beat_strength,
        bands=frame.bands,
        bass_energy=low,
        mid_energy=mid,
        treble_energy=high,
        spectral_centroid=brightness,
        spectral_flux=onset,
        beat_confidence=frame.beat_strength,
        rolling_loudness=frame.rms,
        program_loudness=frame.rms,
        musical_impact=frame.rms,
        silence=silence,
        drop_detected=frame.beat and low > 0.75 and onset > 0.55,
    )


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


def _config_to_dict(config: AudioConfig) -> dict[str, float | int]:
    return {
        "noise_floor": config.smoothing.noise_floor,
        "rms_attack": config.smoothing.rms_attack,
        "rms_release": config.smoothing.rms_release,
        "band_attack": config.smoothing.band_attack,
        "band_release": config.smoothing.band_release,
        "beat_release": config.smoothing.beat_release,
        "smoothing_enabled": config.smoothing.enabled,
        "target_level": config.normalization.target_level,
        "min_gain": config.normalization.min_gain,
        "max_gain": config.normalization.max_gain,
        "adapt_attack": config.normalization.adapt_attack,
        "adapt_release": config.normalization.adapt_release,
        "music_threshold": config.normalization.music_threshold,
        "music_max_gain": config.normalization.music_max_gain,
        "silence_floor": config.normalization.silence_floor,
        "normalization_enabled": config.normalization.enabled,
        "dc_block_enabled": config.normalization.dc_block_enabled,
        "drop_bass_threshold": config.analysis.drop_bass_threshold,
        "drop_bass_delta_threshold": config.analysis.drop_bass_delta_threshold,
        "drop_onset_threshold": config.analysis.drop_onset_threshold,
        "section_change_threshold": config.analysis.section_change_threshold,
        "section_onset_threshold": config.analysis.section_onset_threshold,
    }


class AudioState:
    """Single-threaded DSP state owned by the capture worker."""

    def __init__(
        self,
        config: AudioConfig | None = None,
        sample_rate: float = DEFAULT_SAMPLE_RATE,
    ) -> None:
        native_audio = _require_native_audio()
        self._config = config or AudioConfig()
        self._sample_rate = float(sample_rate)
        self._processor = native_audio.AudioProcessor(
            _config_to_dict(self._config), self._sample_rate
        )
        self._frame = AudioFrame()
        self._features = MusicFeatures()
        self._last_sequence = 0

    def frame(self) -> AudioFrame:
        return self._frame

    def music_features(self) -> MusicFeatures:
        return self._features

    def frame_age(self, now: float | None = None) -> float | None:
        if self._frame.sequence <= 0 or self._frame.timestamp <= 0.0:
            return None
        current = time.monotonic() if now is None else now
        return max(0.0, current - self._frame.timestamp)

    @property
    def _normalization_gain(self) -> float:
        return float(self._processor.normalization_gain())

    def stats(self) -> AudioProcessorStats:
        result = NativeStatsResult.from_native(self._processor.stats())
        return AudioProcessorStats(
            feed_count=result.feed_count,
            samples_seen=result.samples_seen,
            fft_count=result.fft_count,
            sample_abs_sum=result.sample_abs_sum,
            normalization_gain=result.normalization_gain,
            input_rms=result.input_rms,
            program_loudness=result.program_loudness,
            musical_impact=result.musical_impact,
        )

    def reconfigure(self, config: AudioConfig) -> None:
        native_audio = _require_native_audio()
        self._config = config
        self._processor = native_audio.AudioProcessor(
            _config_to_dict(config), self._sample_rate
        )
        self._frame = AudioFrame()
        self._features = MusicFeatures()
        self._last_sequence = 0

    def copy(self) -> AudioState:
        clone = object.__new__(AudioState)
        clone._config = self._config
        clone._sample_rate = self._sample_rate
        clone._processor = self._processor.state_copy()
        clone._frame = self._frame
        clone._features = self._features
        clone._last_sequence = self._last_sequence
        return clone

    def feed_samples(self, samples: npt.ArrayLike) -> None:
        array = np.asarray(samples, dtype=np.float32).reshape(-1)
        if array.size == 0:
            return

        self._processor.feed_samples(array)
        frame = NativeFrameResult.from_native(self._processor.frame())
        timestamp = self._frame.timestamp
        fresh = self._frame.fresh
        if frame.sequence > self._last_sequence:
            timestamp = time.monotonic()
            fresh = True
            self._last_sequence = frame.sequence
        self._frame = AudioFrame(
            rms=frame.rms,
            bands=frame.bands,
            beat=frame.beat,
            beat_strength=frame.beat_strength,
            sequence=frame.sequence,
            timestamp=timestamp,
            fresh=fresh,
        )

        features = NativeFeaturesResult.from_native(self._processor.features())
        self._features = MusicFeatures(
            bpm=features.bpm,
            bpm_confidence=_clamp01(
                features.beat_confidence * 0.6
                + min(1.0, max(0.0, (features.bpm - 40.0) / 200.0)) * 0.4
            ),
            energy=features.energy,
            volume=features.energy,
            energy_level=features.energy,
            bass=features.bass,
            brightness=features.brightness,
            onset_strength=features.onset_strength,
            dynamic_range=features.dynamic_range,
            beat=features.beat,
            beat_strength=features.beat_strength,
            bands=features.bands,
            bass_energy=features.bass_energy,
            mid_energy=features.mid_energy,
            treble_energy=features.treble_energy,
            spectral_centroid=features.spectral_centroid,
            spectral_flux=features.spectral_flux,
            beat_confidence=features.beat_confidence,
            rolling_loudness=features.rolling_loudness,
            program_loudness=features.program_loudness,
            musical_impact=features.musical_impact,
            silence=features.silence,
            drop_detected=features.drop_detected,
            section_change=features.section_change,
        )


__all__ = [
    "AudioState",
    "NativeFeaturesResult",
    "NativeFrameResult",
    "NativeStatsResult",
    "features_from_frame",
]
