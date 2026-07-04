from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from lumistripe import _audio

FFT_SIZE = 1024
NUM_BANDS = 8
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


@dataclass(frozen=True, slots=True)
class MusicFeatures:
    bpm: float = 120.0
    energy: float = 0.0
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
class AudioConfig:
    smoothing: AudioSmoothing = field(default_factory=AudioSmoothing)
    normalization: AudioNormalization = field(default_factory=AudioNormalization)

    @classmethod
    def raw(cls) -> AudioConfig:
        return cls(
            smoothing=AudioSmoothing(enabled=False),
            normalization=AudioNormalization(enabled=False, dc_block_enabled=False),
        )


@dataclass(frozen=True, slots=True)
class AudioInputDevice:
    index: int
    name: str


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
    }


class AudioState:
    def __init__(self, config: AudioConfig | None = None, sample_rate: float = DEFAULT_SAMPLE_RATE) -> None:
        self._config = config or AudioConfig()
        self._sample_rate = float(sample_rate)
        self._processor = _audio.AudioProcessor(
            _config_to_dict(self._config), self._sample_rate)
        self._frame = AudioFrame()
        self._features = MusicFeatures()

    def frame(self) -> AudioFrame:
        return self._frame

    def music_features(self) -> MusicFeatures:
        return self._features

    @property
    def _normalization_gain(self) -> float:
        return float(self._processor.normalization_gain())

    def feed_samples(self, samples: npt.ArrayLike) -> None:
        array = np.asarray(samples, dtype=np.float32).reshape(-1)
        if array.size == 0:
            return

        self._processor.feed_samples(array)

        rms, bands, beat, beat_strength = self._processor.frame()
        self._frame = AudioFrame(
            rms=float(rms),
            bands=tuple(float(b) for b in bands),
            beat=bool(beat),
            beat_strength=float(beat_strength),
        )
        (bpm, energy, bass, brightness, onset_strength,
         dynamic_range, feat_beat, feat_beat_strength, feat_bands) = self._processor.features()
        self._features = MusicFeatures(
            bpm=float(bpm),
            energy=float(energy),
            bass=float(bass),
            brightness=float(brightness),
            onset_strength=float(onset_strength),
            dynamic_range=float(dynamic_range),
            beat=bool(feat_beat),
            beat_strength=float(feat_beat_strength),
            bands=tuple(float(b) for b in feat_bands),
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

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info, status
            samples = np.asarray(indata, dtype=np.float32)
            if samples.ndim == 2:
                if samples.shape[1] > 1:
                    mono = samples.mean(axis=1, dtype=np.float32)
                else:
                    mono = samples[:, 0]
            else:
                mono = samples.reshape(-1)
            with self._lock:
                self._state.feed_samples(mono)

        try:
            self._stream = sounddevice.InputStream(device=device_id, callback=callback)
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
            return clone

    def read(self) -> AudioFrame:
        with self._lock:
            return self._state.frame()

    def read_features(self) -> MusicFeatures:
        with self._lock:
            return self._state.music_features()

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
