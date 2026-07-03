from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

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


class AudioState:
    def __init__(self, config: AudioConfig | None = None, sample_rate: float = DEFAULT_SAMPLE_RATE) -> None:
        self._config = config or AudioConfig()
        self._sample_rate = float(sample_rate)
        self._frame = AudioFrame()
        self._features = MusicFeatures()
        self._buffer = np.zeros(FFT_SIZE, dtype=np.float32)
        self._analysis_buffer = np.zeros(FFT_SIZE, dtype=np.float32)
        self._buffer_pos = 0
        self._window = np.hanning(FFT_SIZE).astype(np.float32)
        self._magnitudes = np.zeros(FFT_SIZE // 2, dtype=np.float32)
        self._frequencies = np.fft.rfftfreq(FFT_SIZE, d=1.0 / self._sample_rate).astype(np.float32)
        self._band_slices = _build_band_slices(self._sample_rate)
        self._prev_bass_energy = 0.0
        self._smoothed_rms = 0.0
        self._smoothed_bands = np.zeros(NUM_BANDS, dtype=np.float32)
        self._beat_envelope = 0.0
        self._level_estimate = 0.0
        self._normalization_gain = 1.0
        self.callback_count = 0
        self.sample_sum = 0.0

        self._rms_history = np.zeros(RMS_HISTORY_SIZE, dtype=np.float32)
        self._rms_idx = 0
        self._onset_history = np.zeros(ONSET_HISTORY_SIZE, dtype=np.float32)
        self._onset_idx = 0
        self._prev_rms = 0.0
        self._prev_feature_bands = np.zeros(NUM_BANDS, dtype=np.float32)
        self._prev_onset = 0.0
        self._smooth_onset = 0.0
        self._last_onset_frame = 0
        self._ioi_buffer: list[float] = []
        self._bpm = 120.0
        self._brightness = 0.0
        self._dynamic_range = 0.0
        self._fft_call_count = 0

    def frame(self) -> AudioFrame:
        return self._frame

    def music_features(self) -> MusicFeatures:
        return self._features

    def feed_samples(self, samples: npt.ArrayLike) -> None:
        array = np.asarray(samples, dtype=np.float32).reshape(-1)
        if array.size == 0:
            return

        self.callback_count += 1
        self.sample_sum += float(np.abs(array).sum())

        offset = 0
        while offset < array.size:
            remaining = FFT_SIZE - self._buffer_pos
            chunk = min(remaining, array.size - offset)
            self._buffer[self._buffer_pos : self._buffer_pos + chunk] = array[offset : offset + chunk]
            self._buffer_pos += chunk
            offset += chunk
            if self._buffer_pos >= FFT_SIZE:
                self._buffer_pos = 0
                self._process_fft()

    def _process_fft(self) -> None:
        self._fft_call_count += 1
        smoothing = self._config.smoothing
        normalized = self._normalized_buffer()
        self._analysis_buffer[:] = normalized
        spectrum = np.fft.rfft(self._analysis_buffer * self._window)
        magnitudes = (np.abs(spectrum[: FFT_SIZE // 2]) / WINDOW_SCALE).astype(np.float32)
        self._magnitudes[:] = magnitudes

        raw_rms = min(float(np.sqrt(float(np.square(normalized).mean()))), 1.0)

        raw_bands: list[float] = []
        power = np.square(magnitudes, dtype=np.float32)
        for start, end in self._band_slices:
            actual_end = min(end, FFT_SIZE // 2)
            band = power[start:actual_end]
            if band.size == 0:
                raw_bands.append(0.0)
                continue
            value = float(np.sqrt(float(band.mean())))
            compressed = value / (value + 0.12) if value > 0.0 else 0.0
            raw_bands.append(min(compressed * 1.2, 1.0))

        gated_rms = _apply_noise_floor(raw_rms, smoothing.noise_floor)
        gated_bands = np.array(
            [_apply_noise_floor(value, smoothing.noise_floor) for value in raw_bands],
            dtype=np.float32,
        )

        bass = float(gated_bands[0])
        bass_rise = bass - self._prev_bass_energy
        threshold = max(self._prev_bass_energy * 1.12, smoothing.noise_floor + 0.01)
        if bass > threshold and bass_rise > 0.015:
            beat = True
            raw_beat_strength = min(bass_rise * 1.6, 1.0)
        else:
            beat = False
            raw_beat_strength = 0.0
        self._prev_bass_energy = self._prev_bass_energy * 0.9 + bass * 0.1

        if smoothing.enabled:
            self._smoothed_rms = _smooth_value(
                self._smoothed_rms,
                gated_rms,
                attack=smoothing.rms_attack,
                release=smoothing.rms_release,
            )
            for index, value in enumerate(gated_bands):
                self._smoothed_bands[index] = _smooth_value(
                    float(self._smoothed_bands[index]),
                    float(value),
                    attack=smoothing.band_attack,
                    release=smoothing.band_release,
                )
            self._beat_envelope = _smooth_value(
                self._beat_envelope,
                raw_beat_strength,
                attack=1.0,
                release=smoothing.beat_release,
            )
            rms = self._smoothed_rms
            bands = tuple(float(value) for value in self._smoothed_bands)
            beat_strength = self._beat_envelope
        else:
            self._smoothed_rms = gated_rms
            self._smoothed_bands[:] = gated_bands
            self._beat_envelope = raw_beat_strength
            rms = gated_rms
            bands = tuple(float(value) for value in gated_bands)
            beat_strength = raw_beat_strength

        self._frame = AudioFrame(
            rms=rms,
            bands=bands,  # type: ignore[arg-type]
            beat=beat,
            beat_strength=beat_strength,
        )

        self._compute_music_features(rms, bands, magnitudes, beat, beat_strength)

    def _compute_music_features(
        self,
        rms: float,
        bands: tuple[float, ...],
        magnitudes: npt.NDArray[np.float32],
        beat: bool,
        beat_strength: float,
    ) -> None:
        self._rms_history[self._rms_idx % RMS_HISTORY_SIZE] = rms
        self._rms_idx += 1

        band_array = np.asarray(bands, dtype=np.float32)
        prev_bands = self._prev_feature_bands[: len(bands)]
        positive_band_changes = np.maximum(band_array - prev_bands, 0.0)
        band_weights = np.asarray((0.2, 0.3, 0.8, 1.0, 1.15, 1.35, 1.5, 1.6)[: len(bands)], dtype=np.float32)
        weighted_band_energy = float(np.dot(band_array, band_weights)) if band_weights.size else 0.0
        raw_flux = float(np.dot(positive_band_changes, band_weights)) if band_weights.size else 0.0
        spectral_flux = min(1.0, raw_flux / max(weighted_band_energy, 0.08))
        onset_raw = min(1.0, max(0.0, rms - self._prev_rms) * 0.55 + spectral_flux * 0.85)
        self._prev_rms = rms
        self._prev_feature_bands[: len(bands)] = band_array
        self._smooth_onset = _smooth_value(self._smooth_onset, onset_raw, attack=0.3, release=0.08)
        self._onset_history[self._onset_idx % ONSET_HISTORY_SIZE] = self._smooth_onset
        self._onset_idx += 1

        if self._smooth_onset > ONSET_THRESHOLD and self._smooth_onset > self._prev_onset * 1.4:
            interval = self._fft_call_count - self._last_onset_frame
            interval_sec = interval * FFT_SIZE / self._sample_rate
            if BPM_INTERVAL_MIN < interval_sec < BPM_INTERVAL_MAX:
                self._ioi_buffer.append(interval_sec)
                if len(self._ioi_buffer) > MAX_IOI_BUFFER:
                    self._ioi_buffer.pop(0)
                self._bpm = 60.0 / float(np.median(self._ioi_buffer))
            self._last_onset_frame = self._fft_call_count
        self._prev_onset = self._smooth_onset

        total_mag = float(magnitudes.sum())
        if total_mag > 1e-9:
            freqs = self._frequencies[: len(magnitudes)]
            centroid = float(np.sum(freqs * magnitudes) / total_mag)
            centroid_norm = centroid / (self._sample_rate / 2.0)
        else:
            centroid_norm = 0.0

        total_band_energy = float(band_array.sum())
        if total_band_energy > 1e-9:
            off_bass_share = float(band_array[2:].sum() / total_band_energy)
            high_share = float(band_array[5:].sum() / total_band_energy)
        else:
            off_bass_share = 0.0
            high_share = 0.0
        self._brightness = min(1.0, centroid_norm * 0.35 + off_bass_share * 0.75 + high_share * 0.35)

        window = min(self._rms_idx, RMS_HISTORY_SIZE)
        if window > 10:
            valid = self._rms_history[:window]
            p95 = float(np.percentile(valid, 95))
            p10 = float(np.percentile(valid, 10))
            self._dynamic_range = max(0.0, p95 - p10)
        else:
            self._dynamic_range = 0.0

        bass = (bands[0] + bands[1]) * 0.5 if len(bands) >= 2 else 0.0

        self._features = MusicFeatures(
            bpm=float(self._bpm),
            energy=rms,
            bass=bass,
            brightness=float(self._brightness),
            onset_strength=float(self._smooth_onset),
            dynamic_range=float(self._dynamic_range),
            beat=beat,
            beat_strength=beat_strength,
            bands=bands,  # type: ignore[arg-type]
        )

    def _normalized_buffer(self) -> npt.NDArray[np.float32]:
        normalization = self._config.normalization
        samples = self._buffer.astype(np.float32, copy=True)

        if normalization.dc_block_enabled and samples.size > 0:
            samples -= np.float32(float(samples.mean()))

        if not normalization.enabled:
            return samples

        level = float(np.sqrt(float(np.square(samples).mean()))) if samples.size else 0.0
        if level > self._level_estimate:
            level_factor = normalization.adapt_attack
        else:
            level_factor = normalization.adapt_release
        self._level_estimate = _smooth_value(self._level_estimate, level, attack=level_factor, release=level_factor)

        effective_level = max(level, self._level_estimate)
        if effective_level <= normalization.silence_floor:
            target_gain = normalization.min_gain
        else:
            target_gain = normalization.target_level / effective_level
            if effective_level >= normalization.music_threshold:
                target_gain = min(target_gain, normalization.music_max_gain)
            target_gain = max(normalization.min_gain, min(normalization.max_gain, target_gain))

        if target_gain > self._normalization_gain:
            gain_factor = normalization.adapt_attack
        else:
            gain_factor = normalization.adapt_release
        self._normalization_gain = _smooth_value(
            self._normalization_gain,
            target_gain,
            attack=gain_factor,
            release=gain_factor,
        )

        normalized = samples * np.float32(self._normalization_gain)
        return np.clip(normalized, -1.0, 1.0).astype(np.float32, copy=False)


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
            clone = AudioState(self._config, sample_rate=self._state._sample_rate)
            clone._frame = self._state.frame()
            clone._features = self._state.music_features()
            clone._buffer = self._state._buffer.copy()
            clone._analysis_buffer = self._state._analysis_buffer.copy()
            clone._buffer_pos = self._state._buffer_pos
            clone._magnitudes = self._state._magnitudes.copy()
            clone._frequencies = self._state._frequencies.copy()
            clone._band_slices = tuple(self._state._band_slices)
            clone._prev_bass_energy = self._state._prev_bass_energy
            clone._smoothed_rms = self._state._smoothed_rms
            clone._smoothed_bands = self._state._smoothed_bands.copy()
            clone._beat_envelope = self._state._beat_envelope
            clone._level_estimate = self._state._level_estimate
            clone._normalization_gain = self._state._normalization_gain
            clone.callback_count = self._state.callback_count
            clone.sample_sum = self._state.sample_sum
            clone._rms_history = self._state._rms_history.copy()
            clone._rms_idx = self._state._rms_idx
            clone._onset_history = self._state._onset_history.copy()
            clone._onset_idx = self._state._onset_idx
            clone._prev_rms = self._state._prev_rms
            clone._prev_feature_bands = self._state._prev_feature_bands.copy()
            clone._prev_onset = self._state._prev_onset
            clone._smooth_onset = self._state._smooth_onset
            clone._last_onset_frame = self._state._last_onset_frame
            clone._ioi_buffer = list(self._state._ioi_buffer)
            clone._bpm = self._state._bpm
            clone._brightness = self._state._brightness
            clone._dynamic_range = self._state._dynamic_range
            clone._fft_call_count = self._state._fft_call_count
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
