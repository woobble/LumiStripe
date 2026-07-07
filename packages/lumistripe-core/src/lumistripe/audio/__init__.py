from __future__ import annotations

import importlib
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, cast

import numpy as np
import numpy.typing as npt

from . import _audio

FFT_SIZE = 1024
NUM_BANDS = 8
BandTuple = tuple[float, float, float, float, float, float, float, float]
WINDOW_SCALE = FFT_SIZE / 2.0
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
RMS_HISTORY_SIZE = 200
ONSET_HISTORY_SIZE = 512
BPM_INTERVAL_MIN = 0.2
BPM_INTERVAL_MAX = 2.0
MAX_IOI_BUFFER = 12
ONSET_THRESHOLD = 0.12
AUDIO_FRESH_SECONDS = 0.12


@dataclass(frozen=True, slots=True)
class AudioFrame:
    rms: float = 0.0
    bands: tuple[float, float, float, float, float, float, float, float] = (
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
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
    bands: tuple[float, float, float, float, float, float, float, float] = (
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    bass_energy: float = 0.0
    mid_energy: float = 0.0
    treble_energy: float = 0.0
    spectral_centroid: float = 0.0
    spectral_flux: float = 0.0
    beat_confidence: float = 0.0
    rolling_loudness: float = 0.0
    silence: bool = False
    drop_detected: bool = False
    section_change: bool = False

    @property
    def beat_detected(self) -> bool:
        return self.beat


AudioFeatures = MusicFeatures


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
class AudioConfig:
    smoothing: AudioSmoothing = field(default_factory=AudioSmoothing)
    normalization: AudioNormalization = field(default_factory=AudioNormalization)
    analysis: AudioAnalysis = field(default_factory=AudioAnalysis)

    @classmethod
    def raw(cls) -> AudioConfig:
        return cls(
            smoothing=AudioSmoothing(enabled=False),
            normalization=AudioNormalization(enabled=False, dc_block_enabled=False),
            analysis=AudioAnalysis(),
        )


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


@dataclass(frozen=True, slots=True)
class AudioInputHealth:
    callback_count: int = 0
    status_count: int = 0
    last_status: str | None = None
    last_callback_age: float | None = None
    last_frame_age: float | None = None
    processor: AudioProcessorStats = field(default_factory=AudioProcessorStats)


class _AudioSnapshotSilence:
    def __get__(self, obj: AudioSnapshot | None, owner: type[AudioSnapshot]):
        if obj is not None:
            return obj.features.silence

        def create(*, frame: AudioFrame | None = None, health: AudioInputHealth | None = None) -> AudioSnapshot:
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
        return cls(frame=frame, features=features or features_from_frame(frame), health=health)

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
        return _clamp01(self.features.energy * 0.48 + self.low * 0.28 + self.mid * 0.18 + self.onset_strength * 0.18)

    @property
    def accent(self) -> float:
        beat_strength = max(self.frame.beat_strength, self.features.beat_strength)
        if self.frame.beat or self.features.beat:
            return _clamp01(max(beat_strength, self.features.energy * 0.5, self.onset_strength * 0.8))
        return _clamp01(beat_strength * 0.5 + self.onset_strength * 0.5 + self.features.energy * 0.18)

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
        return _clamp01(self.drive * 0.5 + self.accent * 0.24 + self.brightness * 0.16 + self.high * 0.1)

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
    def drop_detected(self) -> bool:
        return self.features.drop_detected

    @property
    def section_change(self) -> bool:
        return self.features.section_change


def features_from_frame(frame: AudioFrame) -> MusicFeatures:
    low, mid, high = _low_mid_high(frame.bands)
    brightness = _clamp01(high * 0.55 + frame.rms * 0.25 + (1.0 - low) * high * 0.2)
    onset = _clamp01(frame.beat_strength if frame.beat else frame.rms * 0.18)
    silence = frame.rms <= 0.003 and max(frame.bands, default=0.0) <= 0.003
    return MusicFeatures(
        bpm=120.0,
        bpm_confidence=0.0,
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
        silence=silence,
        drop_detected=frame.beat and low > 0.75 and onset > 0.55,
        section_change=False,
    )


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
    idle_scale = _clamp(max(noise_floor / AudioSmoothing().noise_floor, 0.5), 0.5, 4.0)

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

    frames: list[AudioFrame] = []
    features: list[MusicFeatures] = []
    deadline = time.monotonic() + duration
    with (
        AudioInput.with_device(device_pattern)
        if device_pattern
        else AudioInput.new()
    ) as audio_input:
        while time.monotonic() < deadline:
            frame = audio_input.read()
            frames.append(frame)
            features.append(audio_input.read_features())
            time.sleep(max(0.0, poll_interval))

    return recommend_audio_calibration(frames, features, duration=duration)


def _low_mid_high(bands: tuple[float, ...]) -> tuple[float, float, float]:
    values = list(bands)
    if len(values) < NUM_BANDS:
        values.extend(0.0 for _ in range(NUM_BANDS - len(values)))
    low = (values[0] + values[1]) * 0.5
    mid = (values[2] + values[3] + values[4]) / 3.0
    high = (values[5] + values[6] + values[7]) / 3.0
    return (_clamp01(low), _clamp01(mid), _clamp01(high))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


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
    def __init__(self, config: AudioConfig | None = None, sample_rate: float = DEFAULT_SAMPLE_RATE) -> None:
        self._config = config or AudioConfig()
        self._sample_rate = float(sample_rate)
        self._processor = _audio.AudioProcessor(
            _config_to_dict(self._config), self._sample_rate)
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
        feed_count, samples_seen, fft_count, sample_abs_sum, normalization_gain = self._processor.stats()
        return AudioProcessorStats(
            feed_count=int(feed_count),
            samples_seen=int(samples_seen),
            fft_count=int(fft_count),
            sample_abs_sum=float(sample_abs_sum),
            normalization_gain=float(normalization_gain),
        )

    def feed_samples(self, samples: npt.ArrayLike) -> None:
        array = np.asarray(samples, dtype=np.float32).reshape(-1)
        if array.size == 0:
            return

        self._processor.feed_samples(array)

        rms, bands, beat, beat_strength, sequence = self._processor.frame()
        sequence = int(sequence)
        timestamp = self._frame.timestamp
        fresh = self._frame.fresh
        if sequence > self._last_sequence:
            timestamp = time.monotonic()
            fresh = True
            self._last_sequence = sequence
        self._frame = AudioFrame(
            rms=float(rms),
            bands=cast(BandTuple, tuple(float(b) for b in bands)),
            beat=bool(beat),
            beat_strength=float(beat_strength),
            sequence=sequence,
            timestamp=timestamp,
            fresh=fresh,
        )
        (
            bpm,
            energy,
            bass,
            brightness,
            onset_strength,
            dynamic_range,
            feat_beat,
            feat_beat_strength,
            feat_bands,
            bass_energy,
            mid_energy,
            treble_energy,
            spectral_centroid,
            spectral_flux,
            beat_confidence,
            rolling_loudness,
            silence,
            drop_detected,
            section_change,
        ) = self._processor.features()
        self._features = MusicFeatures(
            bpm=float(bpm),
            bpm_confidence=_clamp01(float(beat_confidence) * 0.6 + min(1.0, max(0.0, (float(bpm) - 40.0) / 200.0)) * 0.4),
            energy=float(energy),
            volume=float(energy),
            energy_level=float(energy),
            bass=float(bass),
            brightness=float(brightness),
            onset_strength=float(onset_strength),
            dynamic_range=float(dynamic_range),
            beat=bool(feat_beat),
            beat_strength=float(feat_beat_strength),
            bands=cast(BandTuple, tuple(float(b) for b in feat_bands)),
            bass_energy=float(bass_energy),
            mid_energy=float(mid_energy),
            treble_energy=float(treble_energy),
            spectral_centroid=float(spectral_centroid),
            spectral_flux=float(spectral_flux),
            beat_confidence=float(beat_confidence),
            rolling_loudness=float(rolling_loudness),
            silence=bool(silence),
            drop_detected=bool(drop_detected),
            section_change=bool(section_change),
        )


class AudioInput:
    def __init__(
        self,
        device_pattern: str | None = None,
        config: AudioConfig | None = None,
    ) -> None:
        sounddevice = _load_sounddevice()
        self._sounddevice = sounddevice
        self._config = config or AudioConfig()
        self._lock = threading.Lock()
        device = _find_device(sounddevice, device_pattern)
        self._device_name = _device_name(device)
        device_id = _device_id(device)
        sample_rate = _device_sample_rate(device)
        self._state = AudioState(self._config, sample_rate=sample_rate)
        self._callback_count = 0
        self._status_count = 0
        self._last_status: str | None = None
        self._last_callback_at: float | None = None

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info
            samples = np.asarray(indata, dtype=np.float32)
            if samples.ndim == 2:
                if samples.shape[1] > 1:
                    mono = samples.mean(axis=1, dtype=np.float32)
                else:
                    mono = samples[:, 0]
            else:
                mono = samples.reshape(-1)
            with self._lock:
                self._callback_count += 1
                self._last_callback_at = time.monotonic()
                if status:
                    self._status_count += 1
                    self._last_status = str(status)
                self._state.feed_samples(mono)

        try:
            self._stream = sounddevice.InputStream(
                device=device_id,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=FFT_SIZE // 2,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            raise RuntimeError(f'failed to open audio input "{self._device_name}": {exc}') from exc

    @classmethod
    def new(cls) -> AudioInput:
        return cls()

    @classmethod
    def with_device(cls, pattern: str) -> AudioInput:
        return cls(device_pattern=pattern)

    @classmethod
    def with_config(cls, config: AudioConfig) -> AudioInput:
        return cls(config=config)

    @classmethod
    def with_device_config(cls, pattern: str, config: AudioConfig) -> AudioInput:
        return cls(device_pattern=pattern, config=config)

    def state(self) -> AudioState:
        with self._lock:
            clone: AudioState = object.__new__(AudioState)
            clone._config = self._config
            clone._sample_rate = self._state._sample_rate
            clone._processor = self._state._processor.state_copy()
            clone._frame = self._state.frame()
            clone._features = self._state.music_features()
            clone._last_sequence = self._state._last_sequence
            return clone

    def read(self) -> AudioFrame:
        with self._lock:
            frame = self._state.frame()
            age = self._state.frame_age()
            fresh = age is not None and age <= AUDIO_FRESH_SECONDS
            return replace(frame, fresh=fresh)

    def read_features(self) -> MusicFeatures:
        with self._lock:
            return self._state.music_features()

    def health(self) -> AudioInputHealth:
        with self._lock:
            last_callback_age = None
            if self._last_callback_at is not None:
                last_callback_age = max(0.0, time.monotonic() - self._last_callback_at)
            last_frame_age = self._state.frame_age()
            return AudioInputHealth(
                callback_count=self._callback_count,
                status_count=self._status_count,
                last_status=self._last_status,
                last_callback_age=last_callback_age,
                last_frame_age=last_frame_age,
                processor=self._state.stats(),
            )

    def device_name(self) -> str:
        return self._device_name

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()

    def __enter__(self) -> AudioInput:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        self.close()


def list_input_devices() -> list[str]:
    return [device.name for device in list_input_device_details()]


def list_input_device_details() -> list[AudioInputDevice]:
    sounddevice = _load_sounddevice()
    devices = sounddevice.query_devices()
    details: list[AudioInputDevice] = []
    for device in devices:
        if int(device.get("max_input_channels", 0)) > 0:
            details.append(
                AudioInputDevice(
                    index=int(device.get("index", len(details))),
                    name=_device_name(device),
                )
            )
    return details


def _load_sounddevice() -> Any:
    try:
        return importlib.import_module("sounddevice")
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is required for AudioInput; install lumistripe-core[audio]"
        ) from exc


def _device_name(device: Any) -> str:
    if isinstance(device, dict):
        name = str(device.get("name", "default"))
    else:
        name = str(device)
    return name


def _find_device(sounddevice: Any, pattern: str | None) -> Any:
    devices = sounddevice.query_devices()
    if pattern is None:
        for device in devices:
            if int(device.get("max_input_channels", 0)) > 0:
                return device
        raise RuntimeError("no audio input device available")

    if _looks_like_device_index(pattern):
        expected_index = int(pattern)
        for device in devices:
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            if int(device.get("index", -1)) == expected_index:
                return device
        raise RuntimeError(f'no input device with index "{pattern}"')

    lowered = pattern.lower()
    for device in devices:
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        if lowered in str(device.get("name", "")).lower():
            return device
    raise RuntimeError(f'no input device matching "{pattern}"')


def _device_id(device: Any) -> Any:
    if isinstance(device, dict):
        index = device.get("index")
        if index is not None:
            return index
        name = device.get("name")
        if name:
            return str(name)
    return device


def _device_sample_rate(device: Any) -> float:
    if isinstance(device, dict):
        sample_rate = device.get("default_samplerate")
        if sample_rate is not None:
            try:
                return max(float(sample_rate), 1.0)
            except (TypeError, ValueError):
                return DEFAULT_SAMPLE_RATE
    return DEFAULT_SAMPLE_RATE


def _build_band_slices(sample_rate: float) -> tuple[tuple[int, int], ...]:
    frequencies = np.fft.rfftfreq(FFT_SIZE, d=1.0 / max(sample_rate, 1.0))[: FFT_SIZE // 2]
    slices: list[tuple[int, int]] = []
    for low_hz, high_hz in BAND_LIMITS_HZ:
        start = int(np.searchsorted(frequencies, low_hz, side="left"))
        end = int(np.searchsorted(frequencies, high_hz, side="right"))
        start = min(start, FFT_SIZE // 2 - 1)
        end = max(start + 1, min(end, FFT_SIZE // 2))
        slices.append((start, end))
    return tuple(slices)


def _apply_noise_floor(value: float, floor: float) -> float:
    clamped_floor = max(0.0, min(0.99, floor))
    if value <= clamped_floor:
        return 0.0
    scaled = (value - clamped_floor) / (1.0 - clamped_floor)
    return max(0.0, min(1.0, scaled))


def _smooth_value(current: float, target: float, *, attack: float, release: float) -> float:
    factor = attack if target >= current else release
    factor = max(0.0, min(1.0, factor))
    return current + (target - current) * factor


def _looks_like_device_index(value: str) -> bool:
    stripped = value.strip()
    return stripped.isdigit()
