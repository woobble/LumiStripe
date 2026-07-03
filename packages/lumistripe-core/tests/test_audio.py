import numpy as np
import pytest

from lumistripe.audio import (
    AudioConfig,
    AudioFrame,
    AudioInput,
    AudioInputDevice,
    AudioNormalization,
    AudioSmoothing,
    AudioState,
    list_input_device_details,
    list_input_devices,
)


def _drop_like_chunk(frame: int, sample_rate: float = 44_100.0) -> np.ndarray:
    t = np.arange(1024, dtype=np.float32) / sample_rate
    bass_env = 1.0 if frame % 2 == 0 else 0.72
    bass = np.sin(2.0 * np.pi * 55.0 * t) * (0.18 * bass_env)
    sub = np.sin(2.0 * np.pi * 110.0 * t) * 0.08
    hat_amp = 0.06 if frame % 2 == 0 else 0.02
    hat = np.sin(2.0 * np.pi * 3_200.0 * t) * hat_amp
    shimmer = np.sin(2.0 * np.pi * 5_400.0 * t) * (0.03 if frame % 4 == 0 else 0.0)
    click = np.zeros_like(t)
    click[:48] = np.linspace(0.16, 0.0, 48, dtype=np.float32)
    return np.clip(bass + sub + hat + shimmer + click, -1.0, 1.0).astype(np.float32)


def test_audio_state_silence_produces_zeroish_frame() -> None:
    state = AudioState()
    state.feed_samples(np.zeros(1024, dtype=np.float32))
    frame = state.frame()

    assert frame.rms == pytest.approx(0.0, abs=1e-6)
    assert frame.beat is False
    assert len(frame.bands) == 8


def test_audio_state_low_frequency_pulse_drives_first_bands() -> None:
    state = AudioState(AudioConfig.raw())
    sample_rate = 44_100.0
    samples = np.sin(2.0 * np.pi * 80.0 * np.arange(1024, dtype=np.float32) / sample_rate).astype(
        np.float32
    )
    state.feed_samples(samples)
    frame = state.frame()

    assert frame.rms > 0.0
    assert frame.bands[0] > frame.bands[-1]


def test_audio_state_high_frequency_tone_reaches_upper_bands() -> None:
    state = AudioState(AudioConfig.raw())
    sample_rate = 44_100.0
    samples = np.sin(2.0 * np.pi * 8_000.0 * np.arange(1024, dtype=np.float32) / sample_rate).astype(
        np.float32
    )

    state.feed_samples(samples)
    frame = state.frame()

    assert frame.rms > 0.0
    assert frame.bands[-1] > frame.bands[1]
    assert max(frame.bands[4:]) > 0.05


def test_audio_state_band_mapping_respects_sample_rate() -> None:
    sample_rate = 16_000.0
    state = AudioState(AudioConfig.raw(), sample_rate=sample_rate)
    samples = np.sin(2.0 * np.pi * 3_200.0 * np.arange(1024, dtype=np.float32) / sample_rate).astype(
        np.float32
    )

    state.feed_samples(samples)
    frame = state.frame()

    assert frame.bands[6] > frame.bands[2]
    assert frame.bands[6] == max(frame.bands)


def test_audio_state_can_trigger_beat_detection() -> None:
    state = AudioState()
    state.feed_samples(np.full(1024, 0.01, dtype=np.float32))
    state.feed_samples(np.sin(2.0 * np.pi * 3.0 * np.arange(1024, dtype=np.float32) / 1024.0))
    frame = state.frame()

    assert isinstance(frame, AudioFrame)
    assert len(frame.bands) == 8


def test_audio_state_noise_floor_suppresses_low_level_noise() -> None:
    state = AudioState(AudioConfig(smoothing=AudioSmoothing(noise_floor=0.95)))
    state.feed_samples(np.full(1024, 0.005, dtype=np.float32))
    frame = state.frame()

    assert frame.rms == pytest.approx(0.0, abs=1e-6)
    assert max(frame.bands) == pytest.approx(0.0, abs=1e-6)


def test_audio_state_default_smoothing_reduces_dropoff() -> None:
    state = AudioState()
    loud = np.sin(2.0 * np.pi * 4.0 * np.arange(1024, dtype=np.float32) / 1024.0).astype(np.float32)
    quiet = np.zeros(1024, dtype=np.float32)

    state.feed_samples(loud)
    first = state.frame()
    state.feed_samples(quiet)
    second = state.frame()

    assert first.rms > 0.0
    assert second.rms > 0.0
    assert second.rms < first.rms


def test_audio_state_raw_config_stays_unsmoothed() -> None:
    state = AudioState(AudioConfig.raw())
    loud = np.sin(2.0 * np.pi * 4.0 * np.arange(1024, dtype=np.float32) / 1024.0).astype(np.float32)
    quiet = np.zeros(1024, dtype=np.float32)

    state.feed_samples(loud)
    assert state.frame().rms > 0.0
    state.feed_samples(quiet)
    assert state.frame().rms == pytest.approx(0.0, abs=1e-6)


def test_audio_state_smoothed_beat_strength_has_decay() -> None:
    smoothing = AudioSmoothing(noise_floor=0.0, beat_release=0.25)
    state = AudioState(AudioConfig(smoothing=smoothing))
    quiet = np.full(1024, 0.01, dtype=np.float32)
    loud = np.sin(2.0 * np.pi * 80.0 * np.arange(1024, dtype=np.float32) / 44_100.0).astype(np.float32)

    state.feed_samples(quiet)
    state.feed_samples(loud)
    peak = state.frame()
    state.feed_samples(np.zeros(1024, dtype=np.float32))
    decayed = state.frame()

    assert peak.beat_strength > 0.0
    assert decayed.beat_strength > 0.0
    assert decayed.beat_strength < peak.beat_strength


def test_audio_state_dc_bias_no_longer_pins_low_band() -> None:
    state = AudioState()
    state.feed_samples(np.full(1024, 0.6, dtype=np.float32))
    frame = state.frame()

    assert frame.rms < 0.1
    assert frame.bands[0] < 0.1


def test_audio_state_normalizes_hot_and_quiet_inputs() -> None:
    hot = AudioState()
    quiet = AudioState()
    phase = 2.0 * np.pi * 80.0 * np.arange(1024, dtype=np.float32) / 44_100.0
    hot_samples = (np.sin(phase) * 0.9).astype(np.float32)
    quiet_samples = (np.sin(phase) * 0.08).astype(np.float32)

    for _ in range(10):
        hot.feed_samples(hot_samples)
        quiet.feed_samples(quiet_samples)

    hot_frame = hot.frame()
    quiet_frame = quiet.frame()
    assert abs(hot_frame.rms - quiet_frame.rms) < 0.25
    assert abs(hot_frame.bands[0] - quiet_frame.bands[0]) < 0.25


def test_audio_state_sustained_music_does_not_saturate() -> None:
    state = AudioState()
    samples = (np.sin(2.0 * np.pi * 160.0 * np.arange(1024, dtype=np.float32) / 44_100.0) * 0.6).astype(
        np.float32
    )

    for _ in range(12):
        state.feed_samples(samples)

    frame = state.frame()
    assert frame.rms < 0.7
    assert max(frame.bands) < 0.95


def test_audio_state_silence_does_not_get_amplified() -> None:
    state = AudioState()
    state.feed_samples(np.zeros(1024, dtype=np.float32))
    state.feed_samples(np.zeros(1024, dtype=np.float32))
    frame = state.frame()

    assert frame.rms == pytest.approx(0.0, abs=1e-6)
    assert max(frame.bands) == pytest.approx(0.0, abs=1e-6)


def test_audio_state_normalization_respects_gain_limits() -> None:
    normalization = AudioNormalization(target_level=0.4, min_gain=0.5, max_gain=2.0)
    state = AudioState(AudioConfig(normalization=normalization))
    quiet = np.sin(2.0 * np.pi * 4.0 * np.arange(1024, dtype=np.float32) / 1024.0).astype(np.float32) * 0.01

    state.feed_samples(quiet)

    assert state._normalization_gain <= 2.0
    assert state._normalization_gain >= 0.5


def test_audio_defaults_match_retuned_baseline() -> None:
    config = AudioConfig()
    assert config.normalization.target_level == pytest.approx(0.36)
    assert config.smoothing.noise_floor == pytest.approx(0.015)


def test_audio_state_music_threshold_prevents_gain_runaway() -> None:
    normalization = AudioNormalization(target_level=0.4, max_gain=4.0, music_threshold=0.05, music_max_gain=3.0)
    state = AudioState(AudioConfig(normalization=normalization))
    samples = (np.sin(2.0 * np.pi * 8.0 * np.arange(1024, dtype=np.float32) / 1024.0) * 0.5).astype(
        np.float32
    )

    for _ in range(8):
        state.feed_samples(samples)

    assert state._normalization_gain <= 3.0
    assert state._normalization_gain >= 1.0


def test_audio_state_drop_like_input_has_stronger_onset_than_low_tone() -> None:
    drop_state = AudioState()
    sample_rate = 44_100.0

    drop_onsets: list[float] = []
    for frame in range(12):
        drop_state.feed_samples(_drop_like_chunk(frame, sample_rate))
        if frame >= 4:
            drop_onsets.append(drop_state.music_features().onset_strength)

    assert max(drop_onsets) > 0.16
    assert sum(drop_onsets) / len(drop_onsets) > 0.18


def test_audio_state_drop_like_input_has_higher_brightness_than_low_tone() -> None:
    drop_state = AudioState()
    low_state = AudioState()
    sample_rate = 44_100.0
    low_tone = (np.sin(2.0 * np.pi * 55.0 * np.arange(1024, dtype=np.float32) / sample_rate) * 0.2).astype(np.float32)

    for frame in range(12):
        drop_state.feed_samples(_drop_like_chunk(frame, sample_rate))
        low_state.feed_samples(low_tone)

    assert drop_state.music_features().brightness > low_state.music_features().brightness + 0.12
    assert drop_state.music_features().brightness > 0.12


def test_audio_state_raw_config_preserves_dc_bias() -> None:
    state = AudioState(AudioConfig.raw())
    state.feed_samples(np.full(1024, 0.6, dtype=np.float32))
    frame = state.frame()

    assert frame.bands[0] > 0.1
    assert frame.beat_strength > 0.0


class FakeInputStream:
    def __init__(self, *, device, callback):
        self.device = device
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self):
        self.devices = [
            {"name": "Speakers", "index": 0, "max_input_channels": 0},
            {"name": "USB Mic", "index": 1, "max_input_channels": 1},
            {"name": "Default Input", "index": 2, "max_input_channels": 2},
        ]
        self.streams: list[FakeInputStream] = []

    def query_devices(self):
        return self.devices

    def InputStream(self, *, device, callback):
        stream = FakeInputStream(device=device, callback=callback)
        self.streams.append(stream)
        return stream


class BrokenSoundDevice(FakeSoundDevice):
    def InputStream(self, *, device, callback):
        del device, callback
        raise Exception("Device unavailable [PaErrorCode -9985]")


def test_audio_input_missing_dependency_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_import(name: str):
        if name == "sounddevice":
            raise ImportError("missing")
        return __import__(name)

    monkeypatch.setattr("lumistripe.audio.importlib.import_module", raising_import)
    with pytest.raises(RuntimeError, match="lumistripe-core\\[audio\\]"):
        AudioInput.new()


def test_list_input_devices_uses_sounddevice(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    assert list_input_devices() == ["USB Mic", "Default Input"]


def test_list_input_device_details_uses_sounddevice(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    assert list_input_device_details() == [
        AudioInputDevice(index=1, name="USB Mic"),
        AudioInputDevice(index=2, name="Default Input"),
    ]


def test_audio_input_reads_callback_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)

    audio_input = AudioInput.with_device("usb")
    assert audio_input.device_name() == "USB Mic"
    assert fake.streams[0].device == 1
    assert fake.streams[0].started is True

    samples = np.sin(2.0 * np.pi * 4.0 * np.arange(1024, dtype=np.float32) / 1024.0).reshape(1024, 1)
    fake.streams[0].callback(samples, 1024, None, None)
    frame = audio_input.read()

    assert frame.rms > 0.0
    audio_input.close()
    assert fake.streams[0].stopped is True
    assert fake.streams[0].closed is True


def test_audio_input_with_config_uses_custom_smoothing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)

    config = AudioConfig(smoothing=AudioSmoothing(enabled=False))
    audio_input = AudioInput.with_config(config)

    assert fake.streams[0].device == 1
    assert audio_input.state().frame() == AudioFrame()
    audio_input.close()


def test_audio_input_with_device_config_preserves_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)

    config = AudioConfig.raw()
    audio_input = AudioInput.with_device_config("default", config)

    assert audio_input.device_name() == "Default Input"
    assert fake.streams[0].device == 2
    audio_input.close()


def test_audio_input_accepts_numeric_device_index(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)

    audio_input = AudioInput.with_device("2")

    assert audio_input.device_name() == "Default Input"
    assert fake.streams[0].device == 2
    audio_input.close()


def test_audio_input_pattern_must_match(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    with pytest.raises(RuntimeError, match='no input device matching "nomatch"'):
        AudioInput.with_device("nomatch")


def test_audio_input_numeric_index_must_match(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    with pytest.raises(RuntimeError, match='no input device with index "9"'):
        AudioInput.with_device("9")


def test_audio_input_open_failure_becomes_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = BrokenSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    with pytest.raises(RuntimeError, match='failed to open audio input "USB Mic"'):
        AudioInput.new()
