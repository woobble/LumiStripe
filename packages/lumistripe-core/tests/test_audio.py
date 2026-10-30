from types import SimpleNamespace

import numpy as np
import pytest
from lumistripe.animation.reactive import AudioReactive
from lumistripe.audio import (
    FFT_HOP_SIZE,
    FFT_SIZE,
    AudioAnalysis,
    AudioConfig,
    AudioFeatures,
    AudioFrame,
    AudioInput,
    AudioInputDevice,
    AudioNormalization,
    AudioSmoothing,
    AudioSnapshot,
    AudioState,
    MusicFeatures,
    features_from_frame,
    list_input_device_details,
    list_input_devices,
    recommend_audio_calibration,
)
from lumistripe.audio.capture import _capture_timestamp


def _drop_like_chunk(frame: int, sample_rate: float = 44_100.0) -> np.ndarray:
    t = np.arange(FFT_SIZE, dtype=np.float32) / sample_rate
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
    for _ in range(8):
        state.feed_samples(np.zeros(FFT_SIZE, dtype=np.float32))
    frame = state.frame()
    features = state.music_features()

    assert frame.rms == pytest.approx(0.0, abs=1e-6)
    assert frame.beat is False
    assert len(frame.bands) == 8
    assert features.silence is True
    assert features.rolling_loudness == pytest.approx(0.0, abs=1e-6)


def test_audio_dynamic_range_matches_rolling_rms_percentiles() -> None:
    state = AudioState(AudioConfig.raw())
    rng = np.random.default_rng(413)
    history: list[float] = []
    for index in range(240):
        samples = rng.standard_normal(512) * ((index % 37 + 1) / 80)
        previous_sequence = state.frame().sequence
        state.feed_samples(samples.astype(np.float32))
        frame = state.frame()
        if frame.sequence == previous_sequence:
            continue
        history.append(frame.rms)
        if len(history) > 10:
            recent = history[-200:]
            expected = np.percentile(recent, 95) - np.percentile(recent, 10)
            assert state.music_features().dynamic_range == pytest.approx(expected, abs=1e-6)


def test_audio_state_accepts_strided_and_unaligned_samples() -> None:
    state = AudioState(AudioConfig.raw())
    samples = np.full(FFT_SIZE * 2, 0.25, dtype=np.float32)[::2]
    state.feed_samples(samples)
    assert state.frame().rms > 0
    unaligned = np.ndarray((FFT_SIZE,), dtype=np.float32, buffer=bytearray(FFT_SIZE * 4 + 1), offset=1)
    unaligned[:] = 0.2
    state.feed_samples(unaligned)
    assert state.stats().samples_seen == FFT_SIZE * 2


def test_native_audio_rejects_invalid_configuration_and_unaligned_buffers() -> None:
    from lumistripe.audio._audio import AudioProcessor

    for rate in (
        0.0,
        -1.0,
        1e-20,
        1e30,
        2_147_483_649.0,
        float("nan"),
        float("inf"),
    ):
        with pytest.raises(ValueError, match="sample rate"):
            AudioProcessor({}, rate)
    for config in ({"smoothing_enabled": float("nan")}, {"noise_floor": float("inf")}):
        with pytest.raises(ValueError):
            AudioProcessor(config)
    with pytest.raises(UnicodeEncodeError):
        AudioProcessor({"\ud800": 1})
    processor = AudioProcessor({})
    with pytest.raises(RuntimeError, match="already initialized"):
        processor.__init__({})
    unaligned = np.ndarray((4,), dtype=np.float32, buffer=bytearray(17), offset=1)
    with pytest.raises(TypeError, match="float32"):
        processor.feed_samples(unaligned)
    uninitialized = AudioProcessor.__new__(AudioProcessor)
    with pytest.raises(RuntimeError, match="not initialized"):
        uninitialized.feed_samples(np.zeros(FFT_SIZE, dtype=np.float32))
    with pytest.raises(RuntimeError, match="not initialized"):
        uninitialized.state_copy()


def test_native_audio_accounts_for_dropped_stream_samples() -> None:
    from lumistripe.audio._audio import AudioProcessor

    processor = AudioProcessor({})
    processor.feed_samples(np.zeros(FFT_SIZE, dtype=np.float32))
    previous_samples_seen = processor.stats()["samples_seen"]

    processor.mark_discontinuity(512)

    assert processor.stats()["samples_seen"] == previous_samples_seen + 512
    with pytest.raises(ValueError, match="non-negative"):
        processor.mark_discontinuity(-1)


def test_native_audio_nonfinite_samples_do_not_poison_statistics() -> None:
    state = AudioState()
    samples = np.zeros(FFT_SIZE, dtype=np.float32)
    samples[:3] = [np.nan, np.inf, -np.inf]
    state.feed_samples(samples)
    assert np.isfinite(state.stats().sample_abs_sum)
    assert np.isfinite(state.frame().rms)


def test_native_audio_saturates_sample_sum_for_large_finite_samples() -> None:
    from lumistripe.audio._audio import AudioProcessor

    processor = AudioProcessor({})
    samples = np.full(FFT_SIZE, np.finfo(np.float32).max, dtype=np.float32)

    processor.feed_samples(samples)

    assert processor.stats()["sample_abs_sum"] == float(np.finfo(np.float32).max)
    assert np.isfinite(processor.frame()["rms"])


def test_native_audio_rejects_sample_counter_overflow_atomically() -> None:
    from lumistripe.audio._audio import AudioProcessor

    processor = AudioProcessor({})
    max_samples = np.iinfo(np.int64).max
    processor.mark_discontinuity(max_samples - FFT_SIZE)
    processor.feed_samples(np.zeros(FFT_SIZE, dtype=np.float32))
    before = processor.stats()
    assert before["samples_seen"] == max_samples

    with pytest.raises(OverflowError, match="sample counter"):
        processor.feed_samples(np.zeros(1, dtype=np.float32))

    after = processor.stats()
    assert after["samples_seen"] == max_samples
    assert after["feed_count"] == before["feed_count"]


def test_audio_state_low_frequency_pulse_drives_first_bands() -> None:
    state = AudioState(AudioConfig.raw())
    sample_rate = 44_100.0
    samples = np.sin(2.0 * np.pi * 80.0 * np.arange(FFT_SIZE, dtype=np.float32) / sample_rate).astype(
        np.float32
    )
    state.feed_samples(samples)
    frame = state.frame()

    assert frame.rms > 0.0
    assert frame.bands[0] > frame.bands[-1]


def test_audio_state_high_frequency_tone_reaches_upper_bands() -> None:
    state = AudioState(AudioConfig.raw())
    sample_rate = 44_100.0
    samples = np.sin(2.0 * np.pi * 8_000.0 * np.arange(FFT_SIZE, dtype=np.float32) / sample_rate).astype(
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
    samples = np.sin(2.0 * np.pi * 3_200.0 * np.arange(FFT_SIZE, dtype=np.float32) / sample_rate).astype(
        np.float32
    )

    state.feed_samples(samples)
    frame = state.frame()

    assert frame.bands[6] > frame.bands[2]
    assert frame.bands[6] == max(frame.bands)


def test_audio_state_can_trigger_beat_detection() -> None:
    state = AudioState()
    state.feed_samples(np.full(FFT_SIZE, 0.01, dtype=np.float32))
    state.feed_samples(
        np.sin(
            2.0 * np.pi * 3.0 * np.arange(FFT_SIZE, dtype=np.float32) / float(FFT_SIZE)
        )
    )
    frame = state.frame()

    assert isinstance(frame, AudioFrame)
    assert len(frame.bands) == 8


def test_audio_state_noise_floor_suppresses_low_level_noise() -> None:
    state = AudioState(AudioConfig(smoothing=AudioSmoothing(noise_floor=0.95)))
    state.feed_samples(np.full(FFT_SIZE, 0.005, dtype=np.float32))
    frame = state.frame()

    assert frame.rms == pytest.approx(0.0, abs=1e-6)
    assert max(frame.bands) == pytest.approx(0.0, abs=1e-6)


def test_audio_state_default_smoothing_reduces_dropoff() -> None:
    state = AudioState()
    loud = np.sin(
        2.0 * np.pi * 4.0 * np.arange(FFT_SIZE, dtype=np.float32) / float(FFT_SIZE)
    ).astype(np.float32)
    quiet = np.zeros(FFT_SIZE, dtype=np.float32)

    state.feed_samples(loud)
    first = state.frame()
    state.feed_samples(quiet)
    state.feed_samples(quiet)
    decayed = state.frame()

    assert first.rms > 0.0
    assert decayed.rms > 0.0
    assert decayed.rms < first.rms


def test_audio_state_raw_config_stays_unsmoothed() -> None:
    state = AudioState(AudioConfig.raw())
    loud = np.sin(
        2.0 * np.pi * 4.0 * np.arange(FFT_SIZE, dtype=np.float32) / float(FFT_SIZE)
    ).astype(np.float32)
    quiet = np.zeros(FFT_SIZE, dtype=np.float32)

    state.feed_samples(loud)
    assert state.frame().rms > 0.0
    state.feed_samples(quiet)
    assert state.frame().rms == pytest.approx(0.0, abs=1e-6)


def test_audio_state_smoothed_beat_strength_has_decay() -> None:
    smoothing = AudioSmoothing(noise_floor=0.0, beat_release=0.25)
    state = AudioState(AudioConfig(smoothing=smoothing))
    quiet = np.full(FFT_SIZE, 0.01, dtype=np.float32)
    loud = np.sin(2.0 * np.pi * 80.0 * np.arange(FFT_SIZE, dtype=np.float32) / 44_100.0).astype(np.float32)

    state.feed_samples(quiet)
    state.feed_samples(loud)
    peak = state.frame()
    state.feed_samples(np.zeros(FFT_SIZE, dtype=np.float32))
    decayed = state.frame()

    assert peak.beat_strength > 0.0
    assert decayed.beat_strength > 0.0
    assert decayed.beat_strength < peak.beat_strength


def test_audio_state_dc_bias_no_longer_pins_low_band() -> None:
    state = AudioState()
    state.feed_samples(np.full(FFT_SIZE, 0.6, dtype=np.float32))
    frame = state.frame()

    assert frame.rms < 0.1
    assert frame.bands[0] < 0.1


def test_audio_state_normalization_preserves_hot_and_quiet_contrast() -> None:
    hot = AudioState()
    quiet = AudioState()
    phase = 2.0 * np.pi * 80.0 * np.arange(FFT_SIZE, dtype=np.float32) / 44_100.0
    hot_samples = (np.sin(phase) * 0.9).astype(np.float32)
    quiet_samples = (np.sin(phase) * 0.08).astype(np.float32)

    for _ in range(10):
        hot.feed_samples(hot_samples)
        quiet.feed_samples(quiet_samples)

    hot_frame = hot.frame()
    quiet_frame = quiet.frame()
    assert hot_frame.rms > quiet_frame.rms + 0.2
    assert hot.music_features().musical_impact > quiet.music_features().musical_impact


def test_audio_state_quiet_passage_does_not_pump_gain_after_loud_music() -> None:
    state = AudioState()
    phase = 2.0 * np.pi * 80.0 * np.arange(FFT_SIZE, dtype=np.float32) / 44_100.0
    loud = (np.sin(phase) * 0.6).astype(np.float32)
    quiet = (np.sin(phase) * 0.04).astype(np.float32)

    for _ in range(8):
        state.feed_samples(loud)
    loud_stats = state.stats()
    loud_level = state.frame().rms

    for _ in range(8):
        state.feed_samples(quiet)
    quiet_stats = state.stats()

    assert quiet_stats.normalization_gain < loud_stats.normalization_gain * 1.1
    assert quiet_stats.musical_impact < 0.2
    assert state.frame().rms < loud_level * 0.1


def test_audio_state_sustained_music_does_not_saturate() -> None:
    state = AudioState()
    samples = (np.sin(2.0 * np.pi * 160.0 * np.arange(FFT_SIZE, dtype=np.float32) / 44_100.0) * 0.6).astype(
        np.float32
    )

    for _ in range(12):
        state.feed_samples(samples)

    frame = state.frame()
    assert frame.rms < 0.7
    assert max(frame.bands) < 0.95


def test_audio_state_silence_does_not_get_amplified() -> None:
    state = AudioState()
    state.feed_samples(np.zeros(FFT_SIZE, dtype=np.float32))
    state.feed_samples(np.zeros(FFT_SIZE, dtype=np.float32))
    frame = state.frame()

    assert frame.rms == pytest.approx(0.0, abs=1e-6)
    assert max(frame.bands) == pytest.approx(0.0, abs=1e-6)


def test_audio_state_normalization_respects_gain_limits() -> None:
    normalization = AudioNormalization(target_level=0.4, min_gain=0.5, max_gain=2.0)
    state = AudioState(AudioConfig(normalization=normalization))
    quiet = np.sin(
        2.0 * np.pi * 4.0 * np.arange(FFT_SIZE, dtype=np.float32) / float(FFT_SIZE)
    ).astype(np.float32) * 0.01

    state.feed_samples(quiet)

    assert state._normalization_gain <= 2.0
    assert state._normalization_gain >= 0.5


def test_audio_defaults_match_retuned_baseline() -> None:
    config = AudioConfig()
    assert config.normalization.target_level == pytest.approx(0.36)
    assert config.smoothing.noise_floor == pytest.approx(0.015)


def test_audio_calibration_recommends_from_measured_frames() -> None:
    frames = [
        AudioFrame(rms=0.004),
        AudioFrame(rms=0.006),
        AudioFrame(rms=0.05),
        AudioFrame(rms=0.2),
    ]
    features = [
        MusicFeatures(energy=0.004),
        MusicFeatures(energy=0.006),
        MusicFeatures(energy=0.05),
        MusicFeatures(energy=0.2),
    ]

    result = recommend_audio_calibration(frames, features, duration=1.5)

    assert result.samples == 4
    assert result.duration == pytest.approx(1.5)
    assert result.recommended_noise_floor > result.measured_floor
    assert 0.24 <= result.recommended_target_level <= 0.62
    assert result.audio_config().smoothing.noise_floor == pytest.approx(result.recommended_noise_floor)
    assert result.audio_config().normalization.target_level == pytest.approx(result.recommended_target_level)


def test_audio_calibration_requires_frames() -> None:
    with pytest.raises(ValueError, match="at least one frame"):
        recommend_audio_calibration([])


def test_audio_state_music_threshold_prevents_gain_runaway() -> None:
    normalization = AudioNormalization(target_level=0.4, max_gain=4.0, music_threshold=0.05, music_max_gain=3.0)
    state = AudioState(AudioConfig(normalization=normalization))
    samples = (np.sin(2.0 * np.pi * 8.0 * np.arange(FFT_SIZE, dtype=np.float32) / float(FFT_SIZE)) * 0.5).astype(
        np.float32
    )

    for _ in range(8):
        state.feed_samples(samples)

    assert state._normalization_gain <= 3.0
    assert state._normalization_gain >= 1.0


def test_audio_state_drop_like_input_produces_measurable_onsets() -> None:
    drop_state = AudioState()
    sample_rate = 44_100.0

    drop_onsets: list[float] = []
    for frame in range(12):
        drop_state.feed_samples(_drop_like_chunk(frame, sample_rate))
        if frame >= 4:
            drop_onsets.append(drop_state.music_features().onset_strength)

    assert max(drop_onsets) > 0.12
    assert sum(drop_onsets) / len(drop_onsets) > 0.05


def test_audio_state_keeps_bpm_finite_during_repeated_onsets() -> None:
    sample_rate = 44_100.0
    state = AudioState(AudioConfig.raw())
    silence = np.zeros(FFT_HOP_SIZE, dtype=np.float32)
    t = np.arange(FFT_HOP_SIZE, dtype=np.float32) / sample_rate
    pulse = (0.8 * np.sin(2.0 * np.pi * 80.0 * t)).astype(np.float32)

    for _ in range(6):
        for _ in range(44):
            state.feed_samples(silence)
        for _ in range(4):
            state.feed_samples(pulse)
        assert np.isfinite(state.music_features().bpm)

    assert state.music_features().bpm != 120.0


def test_audio_state_drop_like_input_has_higher_brightness_than_low_tone() -> None:
    drop_state = AudioState()
    low_state = AudioState()
    sample_rate = 44_100.0
    low_tone = (np.sin(2.0 * np.pi * 55.0 * np.arange(FFT_SIZE, dtype=np.float32) / sample_rate) * 0.2).astype(np.float32)

    for frame in range(12):
        drop_state.feed_samples(_drop_like_chunk(frame, sample_rate))
        low_state.feed_samples(low_tone)

    assert drop_state.music_features().brightness > low_state.music_features().brightness
    assert drop_state.music_features().brightness > 0.12


def test_audio_state_exposes_richer_band_and_spectral_features() -> None:
    low_state = AudioState()
    bright_state = AudioState()
    sample_rate = 44_100.0
    low_tone = (np.sin(2.0 * np.pi * 55.0 * np.arange(FFT_SIZE, dtype=np.float32) / sample_rate) * 0.2).astype(np.float32)
    rng = np.random.default_rng(0)
    low_flux: list[float] = []
    bright_flux: list[float] = []

    for frame in range(12):
        low_state.feed_samples(low_tone)
        bright_noise = rng.normal(0.0, 0.12, FFT_SIZE).astype(np.float32)
        bright_state.feed_samples(bright_noise)
        low_flux.append(low_state.music_features().spectral_flux)
        bright_flux.append(bright_state.music_features().spectral_flux)

    low = low_state.music_features()
    bright = bright_state.music_features()

    assert bright.bass_energy >= 0.0
    assert bright.mid_energy >= 0.0
    assert bright.treble_energy >= 0.0
    assert bright.spectral_centroid > low.spectral_centroid
    assert max(bright_flux[1:]) > max(low_flux[1:])


def test_audio_state_can_detect_bass_drop_event() -> None:
    state = AudioState()
    sample_rate = 44_100.0
    quiet = np.zeros(FFT_SIZE, dtype=np.float32)
    drop = _drop_like_chunk(0, sample_rate) * 2.0

    for _ in range(8):
        state.feed_samples(quiet)

    seen_drop = False
    for _ in range(12):
        for offset in range(0, FFT_SIZE, FFT_HOP_SIZE):
            state.feed_samples(drop[offset : offset + FFT_HOP_SIZE])
            seen_drop = seen_drop or state.music_features().drop_detected

    assert seen_drop is True


def test_audio_state_drop_threshold_is_configurable() -> None:
    sample_rate = 44_100.0
    quiet = np.zeros(FFT_SIZE, dtype=np.float32)
    drop = _drop_like_chunk(0, sample_rate) * 2.0
    strict = AudioState(AudioConfig(analysis=AudioAnalysis(drop_bass_threshold=0.98)))

    for _ in range(8):
        strict.feed_samples(quiet)

    seen_drop = False
    for _ in range(12):
        strict.feed_samples(drop)
        seen_drop = seen_drop or strict.music_features().drop_detected

    assert seen_drop is False


def test_audio_state_silence_suppresses_beat_features() -> None:
    config = AudioConfig(
        smoothing=AudioSmoothing(noise_floor=0.0),
        normalization=AudioNormalization(enabled=False),
    )
    state = AudioState(config)
    sample_rate = 44_100.0
    quiet_bass = (np.sin(2.0 * np.pi * 55.0 * np.arange(FFT_SIZE, dtype=np.float32) / sample_rate) * 0.012).astype(
        np.float32
    )

    for _ in range(12):
        state.feed_samples(quiet_bass)

    frame = state.frame()
    features = state.music_features()
    assert features.silence is True
    assert features.beat is False
    assert features.beat_strength == pytest.approx(0.0)
    assert features.drop_detected is False
    assert features.section_change is False
    assert frame.beat is False
    assert frame.beat_strength == pytest.approx(0.0)


def test_audio_state_steady_tone_does_not_trigger_semantic_events() -> None:
    state = AudioState()
    sample_rate = 44_100.0
    tone = (np.sin(2.0 * np.pi * 220.0 * np.arange(FFT_SIZE, dtype=np.float32) / sample_rate) * 0.18).astype(np.float32)

    seen_drop = False
    seen_section = False
    for _ in range(30):
        state.feed_samples(tone)
        features = state.music_features()
        seen_drop = seen_drop or features.drop_detected
        seen_section = seen_section or features.section_change

    assert seen_drop is False
    assert seen_section is False


def test_audio_state_raw_config_preserves_dc_bias() -> None:
    state = AudioState(AudioConfig.raw())
    state.feed_samples(np.full(FFT_SIZE, 0.6, dtype=np.float32))
    frame = state.frame()

    assert frame.bands[0] > 0.1
    assert frame.beat_strength > 0.0


def test_audio_state_waits_for_full_fft_window() -> None:
    state = AudioState(AudioConfig.raw())
    first = np.sin(
        2.0 * np.pi * 80.0 * np.arange(FFT_SIZE - 1, dtype=np.float32) / 44_100.0
    ).astype(np.float32)

    state.feed_samples(first)
    assert state.frame() == AudioFrame()
    assert state.stats().fft_count == 0

    state.feed_samples(np.array([0.1], dtype=np.float32))
    assert state.frame().rms > 0.0
    assert state.frame().sequence == 1
    assert state.frame().fresh is True
    assert state.stats().fft_count == 1
    assert state.stats().samples_seen == FFT_SIZE


def test_audio_state_emits_capture_timestamped_beat_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumistripe.audio import dsp

    class FakeProcessor:
        def __init__(self, config, sample_rate):
            del config, sample_rate
            self.sequence = 0

        def feed_samples(self, samples):
            del samples
            self.sequence += 1

        def frame(self):
            return {
                "rms": 0.5,
                "bands": [0.5] * 8,
                "beat": True,
                "beat_strength": 0.8,
                "sequence": self.sequence,
            }

        def features(self):
            return {
                "bpm": 120.0,
                "energy": 0.5,
                "bass": 0.5,
                "brightness": 0.5,
                "onset_strength": 0.8,
                "dynamic_range": 0.5,
                "beat": True,
                "beat_strength": 0.8,
                "bands": [0.5] * 8,
                "bass_energy": 0.5,
                "mid_energy": 0.5,
                "treble_energy": 0.5,
                "spectral_centroid": 0.5,
                "spectral_flux": 0.5,
                "beat_confidence": 0.8,
                "rolling_loudness": 0.5,
                "silence": False,
                "drop_detected": False,
                "section_change": False,
                "program_loudness": 0.5,
                "musical_impact": 0.7,
            }

    monkeypatch.setattr(dsp, "_audio", SimpleNamespace(AudioProcessor=FakeProcessor))
    state = dsp.AudioState()

    state.feed_samples(np.ones(512, dtype=np.float32), captured_at=123.25)
    state.feed_samples(np.ones(512, dtype=np.float32), captured_at=124.0)
    events = state.take_beat_events()

    assert state.frame().timestamp == pytest.approx(124.0)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.timestamp for event in events] == pytest.approx([123.25, 124.0])
    assert all(event.strength == pytest.approx(0.8) for event in events)
    assert state.take_beat_events() == ()


def test_audio_state_bounds_queued_beat_events_and_reports_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lumistripe.audio import dsp

    class FakeProcessor:
        def __init__(self, config, sample_rate):
            del config, sample_rate
            self.events = tuple(
                {
                    "sequence": sequence,
                    "sample_position": sequence,
                    "strength": 0.8,
                }
                for sequence in range(1, 301)
            )

        def feed_samples(self, samples):
            del samples

        def frame(self):
            return {
                "rms": 0.0,
                "bands": [0.0] * 8,
                "beat": False,
                "beat_strength": 0.0,
                "sequence": 1,
            }

        def drain_beat_events(self):
            events = self.events
            self.events = ()
            return events

        def features(self):
            return {
                "bpm": 120.0,
                "energy": 0.0,
                "bass": 0.0,
                "brightness": 0.0,
                "onset_strength": 0.0,
                "dynamic_range": 0.0,
                "beat": False,
                "beat_strength": 0.0,
                "bands": [0.0] * 8,
                "bass_energy": 0.0,
                "mid_energy": 0.0,
                "treble_energy": 0.0,
                "spectral_centroid": 0.0,
                "spectral_flux": 0.0,
                "beat_confidence": 0.0,
                "rolling_loudness": 0.0,
                "silence": True,
                "drop_detected": False,
                "section_change": False,
                "program_loudness": 0.0,
                "musical_impact": 0.0,
            }

        def stats(self):
            return {
                "feed_count": 1,
                "samples_seen": 300,
                "fft_count": 1,
                "sample_abs_sum": 0.0,
                "normalization_gain": 1.0,
                "input_rms": 0.0,
                "program_loudness": 0.0,
                "musical_impact": 0.0,
                "dropped_beat_events": 0,
            }

    monkeypatch.setattr(dsp, "_audio", SimpleNamespace(AudioProcessor=FakeProcessor))
    state = dsp.AudioState()
    state.feed_samples(np.zeros(300, dtype=np.float32), captured_at=100.0)

    events = state.take_beat_events()

    assert len(events) == 256
    assert [event.sequence for event in events] == list(range(45, 301))
    assert state.stats().queued_dropped_beat_events == 44


def test_audio_state_uses_configured_fft_hop() -> None:
    state = AudioState(AudioConfig.raw())
    window = np.sin(
        2.0 * np.pi * 80.0 * np.arange(FFT_SIZE, dtype=np.float32) / 44_100.0
    ).astype(np.float32)
    hop = window[:FFT_HOP_SIZE]

    state.feed_samples(window)
    assert state.stats().fft_count == 1

    state.feed_samples(hop)
    assert state.stats().fft_count == 2
    assert state.frame().sequence == 2


def test_audio_state_stats_count_multiple_ffts_from_one_feed() -> None:
    state = AudioState(AudioConfig.raw())
    sample_count = FFT_SIZE + (2 * FFT_HOP_SIZE)
    samples = np.sin(
        2.0 * np.pi * 80.0 * np.arange(sample_count, dtype=np.float32) / 44_100.0
    ).astype(np.float32)

    state.feed_samples(samples)
    stats = state.stats()

    assert stats.feed_count == 1
    assert stats.samples_seen == sample_count
    assert stats.fft_count == 3
    assert stats.sample_abs_sum > 0.0
    assert stats.normalization_gain == pytest.approx(1.0)


def test_audio_state_drains_each_synthetic_onset_from_a_large_batch() -> None:
    sample_rate = 44_100.0
    duration_s = 5.0
    sample_count = int(sample_rate * duration_s)
    times = np.arange(sample_count, dtype=np.float32) / sample_rate
    samples = np.zeros(sample_count, dtype=np.float32)
    onset_times = np.arange(0.25, 4.76, 0.5)
    for onset_at in onset_times:
        start = int(onset_at * sample_rate)
        end = min(sample_count, start + int(0.14 * sample_rate))
        samples[start:end] = 0.9 * np.sin(
            2.0 * np.pi * 80.0 * times[start:end]
        )

    captured_at = 50.0 + sample_count / sample_rate
    state = AudioState(AudioConfig.raw(), sample_rate=sample_rate)
    state.feed_samples(samples, captured_at=captured_at)
    events = state.take_beat_events()

    assert len(events) == len(onset_times)
    assert [event.sequence for event in events] == sorted(
        event.sequence for event in events
    )
    assert [event.timestamp for event in events] == sorted(
        event.timestamp for event in events
    )
    assert all(50.0 <= event.timestamp <= captured_at for event in events)
    assert [event.timestamp for event in events] == pytest.approx(
        [50.0 + onset_at for onset_at in onset_times], abs=0.05
    )
    assert state.stats().native_dropped_beat_events == 0


def test_audio_state_preserves_bpm_timing_across_dropped_capture_samples() -> None:
    sample_rate = 44_100.0
    batch_size = 512
    sample_count = int(sample_rate * 4.0)
    times = np.arange(sample_count, dtype=np.float64) / sample_rate
    samples = np.zeros(sample_count, dtype=np.float32)
    onset_times = np.arange(0.25, 3.76, 0.5)
    for onset_at in onset_times:
        start = int(onset_at * sample_rate)
        end = min(sample_count, start + int(0.14 * sample_rate))
        samples[start:end] = 0.9 * np.sin(2.0 * np.pi * 80.0 * times[start:end])

    dropped_at = batch_size * 39
    dropped_samples = batch_size * 9

    def process(
        stream: np.ndarray, *, has_gap: bool
    ) -> tuple[list[float], list[float]]:
        state = AudioState(AudioConfig.raw(), sample_rate=sample_rate)
        event_times: list[float] = []
        event_bpms: list[float] = []
        for offset in range(0, stream.size, batch_size):
            end = min(stream.size, offset + batch_size)
            missing = dropped_samples if has_gap and offset == dropped_at else 0
            gap_offset = dropped_samples if has_gap and offset >= dropped_at else 0
            state.feed_samples(
                stream[offset:end],
                captured_at=50.0 + (end + gap_offset) / sample_rate,
                dropped_samples_before=missing,
            )
            for event in state.take_beat_events():
                event_times.append(event.timestamp - 50.0)
                event_bpms.append(state.music_features().bpm)
        return event_times, event_bpms

    reference_times, reference_bpms = process(samples, has_gap=False)
    gap_stop = dropped_at + dropped_samples
    gap_stream = np.concatenate((samples[:dropped_at], samples[gap_stop:]))
    gap_times, gap_bpms = process(gap_stream, has_gap=True)

    assert gap_times == pytest.approx(reference_times, abs=0.01)
    assert len(gap_bpms) > 1
    assert gap_bpms[1] == pytest.approx(reference_bpms[1], abs=1.0)


def test_audio_stats_expose_level_before_noise_floor() -> None:
    state = AudioState(
        AudioConfig(
            smoothing=AudioSmoothing(noise_floor=0.95),
            normalization=AudioNormalization(enabled=False, dc_block_enabled=False),
        )
    )
    state.feed_samples(np.full(FFT_SIZE, 0.1, dtype=np.float32))

    assert state.frame().rms == pytest.approx(0.0)
    assert state.stats().input_rms == pytest.approx(0.1, rel=0.05)


def test_audio_state_does_not_raise_agc_for_subthreshold_noise() -> None:
    normalization = AudioNormalization(
        target_level=0.36,
        max_gain=5.0,
        music_threshold=0.015,
        adapt_attack=1.0,
        adapt_release=1.0,
    )
    state = AudioState(AudioConfig(normalization=normalization))
    phase = 2.0 * np.pi * 220.0 * np.arange(FFT_SIZE, dtype=np.float32) / 44_100.0
    state.feed_samples((np.sin(phase) * 0.008).astype(np.float32))

    stats = state.stats()
    assert stats.input_rms < normalization.music_threshold
    assert stats.normalization_gain == pytest.approx(1.0)


def test_audio_stats_input_rms_is_measured_before_agc() -> None:
    normalization = AudioNormalization(
        target_level=0.36,
        music_threshold=0.01,
        adapt_attack=1.0,
        adapt_release=1.0,
    )
    state = AudioState(AudioConfig(normalization=normalization))
    phase = 2.0 * np.pi * 220.0 * np.arange(FFT_SIZE, dtype=np.float32) / 44_100.0
    samples = (np.sin(phase) * 0.1).astype(np.float32)
    state.feed_samples(samples)

    stats = state.stats()
    assert stats.input_rms == pytest.approx(float(np.sqrt(np.mean(samples**2))), rel=0.03)
    assert stats.normalization_gain > 1.0
    assert state.frame().rms > stats.input_rms


def test_audio_state_reconfigure_replaces_dsp_without_reusing_old_frame() -> None:
    state = AudioState(AudioConfig.raw())
    state.feed_samples(np.full(FFT_SIZE, 0.2, dtype=np.float32))
    assert state.frame().rms > 0.0

    state.reconfigure(AudioConfig(smoothing=AudioSmoothing(noise_floor=0.95)))

    assert state.frame() == AudioFrame()
    state.feed_samples(np.full(FFT_SIZE, 0.2, dtype=np.float32))
    assert state.frame().rms == pytest.approx(0.0)


def test_features_from_frame_derives_music_features() -> None:
    frame = AudioFrame(
        rms=0.5,
        bands=(0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.25, 0.3),
        beat=True,
        beat_strength=0.75,
    )

    features = features_from_frame(frame)

    assert features.energy == pytest.approx(0.5)
    assert features.bass == pytest.approx(0.7)
    assert features.beat is True
    assert features.beat_strength == pytest.approx(0.75)
    assert features.onset_strength == pytest.approx(0.75)
    assert features.brightness > 0.0
    assert features.bass_energy == pytest.approx(0.7)
    assert features.mid_energy == pytest.approx(0.4)
    assert features.treble_energy == pytest.approx(0.25)
    assert features.spectral_centroid > 0.0
    assert features.spectral_flux == pytest.approx(0.75)
    assert features.beat_confidence == pytest.approx(0.75)
    assert features.rolling_loudness == pytest.approx(0.5)


def test_audio_features_alias_exposes_requested_field_names() -> None:
    features = AudioFeatures(
        volume=0.4,
        energy_level=0.5,
        bpm=128.0,
        bpm_confidence=0.7,
        beat=True,
        beat_confidence=0.8,
        drop_detected=True,
    )

    assert isinstance(features, MusicFeatures)
    assert features.volume == pytest.approx(0.4)
    assert features.energy_level == pytest.approx(0.5)
    assert features.bpm_confidence == pytest.approx(0.7)
    assert features.beat_detected is True
    assert features.drop_detected is True


def test_audio_snapshot_silence_defaults_to_safe_zero_values() -> None:
    snapshot = AudioSnapshot.silence()

    assert snapshot.frame == AudioFrame()
    assert snapshot.features == MusicFeatures()
    assert snapshot.fresh is False
    assert snapshot.sequence == 0
    assert snapshot.low == 0.0
    assert snapshot.mid == 0.0
    assert snapshot.high == 0.0
    assert snapshot.drive == 0.0
    assert snapshot.accent == 0.0
    assert snapshot.activity == 0.0
    assert snapshot.silence is False
    assert snapshot.drop_detected is False
    assert snapshot.section_change is False


def test_audio_snapshot_silence_preserves_frame_metadata_only() -> None:
    stale = AudioFrame(
        rms=0.9,
        bands=(0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2),
        beat=True,
        beat_strength=1.0,
        sequence=12,
        timestamp=34.5,
        fresh=False,
    )

    snapshot = AudioSnapshot.silence(frame=stale)

    assert snapshot.frame.rms == 0.0
    assert max(snapshot.frame.bands) == 0.0
    assert snapshot.frame.beat is False
    assert snapshot.sequence == 12
    assert snapshot.timestamp == pytest.approx(34.5)
    assert snapshot.fresh is False


def test_audio_snapshot_from_parts_derives_features() -> None:
    frame = AudioFrame(
        rms=0.5,
        bands=(0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.25, 0.3),
        beat=True,
        beat_strength=0.75,
        sequence=2,
        timestamp=1.25,
        fresh=True,
    )

    snapshot = AudioSnapshot.from_parts(frame)

    assert snapshot.fresh is True
    assert snapshot.sequence == 2
    assert snapshot.timestamp == pytest.approx(1.25)
    assert snapshot.low == pytest.approx(0.7)
    assert snapshot.mid == pytest.approx(0.4)
    assert snapshot.high == pytest.approx(0.25)
    assert snapshot.drive > 0.0
    assert snapshot.accent >= 0.75
    assert snapshot.activity > 0.0


def test_audio_snapshot_from_parts_uses_explicit_features() -> None:
    frame = AudioFrame(
        rms=0.9,
        bands=(0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2),
        beat=True,
        beat_strength=1.0,
        fresh=True,
    )
    features = MusicFeatures(
        bpm=128.0,
        energy=0.25,
        bass=0.0,
        brightness=0.1,
        onset_strength=0.2,
        beat=False,
        beat_strength=0.0,
        bands=(0.0, 0.0, 0.2, 0.2, 0.2, 0.4, 0.4, 0.4),
    )

    snapshot = AudioSnapshot.from_parts(frame, features)

    assert snapshot.low == 0.0
    assert snapshot.mid == pytest.approx(0.2)
    assert snapshot.high == pytest.approx(0.4)
    assert snapshot.bpm == pytest.approx(128.0)


def test_audio_snapshot_exposes_semantic_feature_properties() -> None:
    frame = AudioFrame(fresh=True)
    features = MusicFeatures(
        bass_energy=0.8,
        mid_energy=0.4,
        treble_energy=0.2,
        spectral_centroid=0.3,
        spectral_flux=0.7,
        beat_confidence=0.9,
        rolling_loudness=0.5,
        silence=True,
        drop_detected=True,
        section_change=True,
    )

    snapshot = AudioSnapshot.from_parts(frame, features)

    assert snapshot.bass_energy == pytest.approx(0.8)
    assert snapshot.mid_energy == pytest.approx(0.4)
    assert snapshot.treble_energy == pytest.approx(0.2)
    assert snapshot.spectral_centroid == pytest.approx(0.3)
    assert snapshot.spectral_flux == pytest.approx(0.7)
    assert snapshot.beat_confidence == pytest.approx(0.9)
    assert snapshot.rolling_loudness == pytest.approx(0.5)
    assert snapshot.silence is True
    assert snapshot.drop_detected is True
    assert snapshot.section_change is True


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_audio_snapshot_non_finite_features_do_not_become_full_scale(
    invalid: float,
) -> None:
    features = MusicFeatures(
        energy=invalid,
        bass_energy=invalid,
        mid_energy=invalid,
        treble_energy=invalid,
        brightness=invalid,
        onset_strength=invalid,
        beat_strength=invalid,
        bands=(invalid,) * 8,
        spectral_centroid=invalid,
        spectral_flux=invalid,
        beat_confidence=invalid,
        rolling_loudness=invalid,
        musical_impact=invalid,
        bpm=invalid,
    )
    frame = AudioFrame(
        rms=invalid,
        bands=(invalid,) * 8,
        beat=True,
        beat_strength=invalid,
    )
    snapshot = AudioSnapshot.from_parts(frame, features)

    assert snapshot.low == 0.0
    assert snapshot.mid == 0.0
    assert snapshot.high == 0.0
    assert snapshot.drive == 0.0
    assert snapshot.accent == 0.0
    assert snapshot.activity == 0.0
    assert snapshot.brightness == 0.0
    assert snapshot.onset_strength == 0.0
    assert snapshot.spectral_centroid == 0.0
    assert snapshot.spectral_flux == 0.0
    assert snapshot.beat_confidence == 0.0
    assert snapshot.rolling_loudness == 0.0
    assert snapshot.musical_impact == 0.0
    assert snapshot.bpm == 0.0

    for reactive in (AudioReactive.from_frame(frame), AudioReactive.from_snapshot(snapshot)):
        assert reactive.rms == 0.0
        assert reactive.accent == 0.0
        assert reactive.low == 0.0
        assert reactive.mid == 0.0
        assert reactive.high == 0.0
        assert reactive.brightness == 0.0
        assert reactive.activity() == 0.0


class FakeInputStream:
    def __init__(self, *, device, samplerate, channels, dtype, blocksize, callback):
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.blocksize = blocksize
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
            {"name": "USB Mic", "index": 1, "max_input_channels": 1, "default_samplerate": 48_000.0},
            {"name": "Default Input", "index": 2, "max_input_channels": 2, "default_samplerate": 44_100.0},
        ]
        self.streams: list[FakeInputStream] = []

    def query_devices(self):
        return self.devices

    def InputStream(self, *, device, samplerate, channels, dtype, blocksize, callback):
        stream = FakeInputStream(
            device=device,
            samplerate=samplerate,
            channels=channels,
            dtype=dtype,
            blocksize=blocksize,
            callback=callback,
        )
        self.streams.append(stream)
        return stream


class BrokenSoundDevice(FakeSoundDevice):
    def InputStream(self, **kwargs):
        del kwargs
        raise RuntimeError("Device unavailable [PaErrorCode -9985]")


def test_audio_input_missing_dependency_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_import(name: str):
        if name == "sounddevice":
            raise ImportError("missing")
        return __import__(name)

    monkeypatch.setattr("lumistripe.audio.importlib.import_module", raising_import)
    with pytest.raises(RuntimeError, match="lumistripe-core\\[audio\\]"):
        AudioInput.new()


def test_audio_input_missing_native_backend_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising_import(name: str):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
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
    monkeypatch.delenv("LUMISTRIPE_PROFILE_AUDIO_CALLBACKS", raising=False)

    audio_input = AudioInput.with_device("usb")
    assert audio_input.device_name() == "USB Mic"
    assert fake.streams[0].device == 1
    assert fake.streams[0].samplerate == 48_000.0
    assert fake.streams[0].channels == 1
    assert fake.streams[0].dtype == "float32"
    assert fake.streams[0].blocksize == 512
    assert fake.streams[0].started is True

    samples = np.sin(
        2.0 * np.pi * 4.0 * np.arange(FFT_SIZE, dtype=np.float32) / float(FFT_SIZE)
    ).reshape(FFT_SIZE, 1)
    fake.streams[0].callback(samples, FFT_SIZE, None, None)
    assert audio_input._buffer.wait_until_idle(timeout=1.0)
    frame = audio_input.read()

    assert frame.rms > 0.0
    assert audio_input._callback_timing_samples_ms() == ()
    audio_input.close()
    assert fake.streams[0].stopped is True
    assert fake.streams[0].closed is True


def test_audio_input_health_tracks_callback_status(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)

    audio_input = AudioInput.with_device("usb")
    samples = np.sin(
        2.0 * np.pi * 4.0 * np.arange(FFT_SIZE, dtype=np.float32) / float(FFT_SIZE)
    ).reshape(FFT_SIZE, 1)
    fake.streams[0].callback(samples, FFT_SIZE, None, "input overflow")
    assert audio_input._buffer.wait_until_idle(timeout=1.0)
    health = audio_input.health()

    assert health.callback_count == 1
    assert health.status_count == 1
    assert health.last_status == "input overflow"
    assert health.last_callback_age is not None
    assert health.last_frame_age is not None
    assert health.processor.feed_count == 1
    assert health.processor.samples_seen == FFT_SIZE
    assert health.processor.fft_count == 1
    audio_input.close()


def test_audio_input_callback_age_is_available_without_dsp_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    audio_input = AudioInput.with_device("usb")
    samples = np.zeros((FFT_SIZE, 1), dtype=np.float32)

    assert audio_input.last_callback_age is None
    fake.streams[0].callback(samples, FFT_SIZE, None, None)
    age = audio_input.last_callback_age

    assert age is not None
    assert 0.0 <= age < 1.0
    audio_input.close()


def test_audio_input_snapshot_reads_do_not_wait_for_the_batch_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    audio_input = AudioInput.with_device("usb")

    def unexpected_wait() -> None:
        pytest.fail("real-time audio snapshots must not wait for the batch queue")

    monkeypatch.setattr(audio_input, "_wait_for_worker", unexpected_wait)
    try:
        audio_input.read()
        audio_input.read_features()
        audio_input.read_beat_events()
        audio_input.health()
    finally:
        audio_input.close()


def test_capture_timestamp_maps_portaudio_clock_into_monotonic_time() -> None:
    time_info = SimpleNamespace(inputBufferAdcTime=100.0, currentTime=100.02)

    captured_at = _capture_timestamp(
        time_info,
        frames=512,
        sample_rate=48_000.0,
        fallback=50.0,
    )

    assert captured_at == pytest.approx(50.0 - (0.02 - 511 / 48_000.0))


def test_capture_timestamp_falls_back_when_portaudio_times_are_impossible() -> None:
    time_info = SimpleNamespace(inputBufferAdcTime=100.0, currentTime=100.0)

    assert _capture_timestamp(
        time_info,
        frames=512,
        sample_rate=48_000.0,
        fallback=50.0,
    ) == 50.0


@pytest.mark.parametrize("captured_at", [float("nan"), float("inf"), -float("inf")])
def test_audio_state_replaces_nonfinite_capture_timestamp(
    captured_at: float,
) -> None:
    state = AudioState(AudioConfig.raw())
    state.feed_samples(np.zeros(FFT_SIZE, dtype=np.float32), captured_at=captured_at)

    assert np.isfinite(state.frame().timestamp)
    assert state.frame_age() is not None
    assert state.frame_age() < 1.0


def test_audio_input_callback_timing_is_opt_in_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    monkeypatch.setenv("LUMISTRIPE_PROFILE_AUDIO_CALLBACKS", "1")

    audio_input = AudioInput.with_device("usb")
    samples = np.zeros((FFT_SIZE, 1), dtype=np.float32)
    for _ in range(520):
        fake.streams[0].callback(samples, FFT_SIZE, None, None)

    durations = audio_input._callback_timing_samples_ms()
    assert len(durations) == 512
    assert all(duration >= 0.0 for duration in durations)
    audio_input._clear_callback_timing_samples()
    assert audio_input._callback_timing_samples_ms() == ()
    audio_input.close()


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


def test_audio_input_closes_stream_when_start_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)

    def fail_start(self: FakeInputStream) -> None:
        raise RuntimeError("start failed")

    monkeypatch.setattr(FakeInputStream, "start", fail_start)
    with pytest.raises(RuntimeError, match="start failed"):
        AudioInput.new()
    assert fake.streams[0].closed


def test_audio_input_preserves_start_error_if_stream_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)

    def fail_start(self: FakeInputStream) -> None:
        raise RuntimeError("start failed")

    def fail_close(self: FakeInputStream) -> None:
        raise RuntimeError("close failed")

    monkeypatch.setattr(FakeInputStream, "start", fail_start)
    monkeypatch.setattr(FakeInputStream, "close", fail_close)
    with pytest.raises(RuntimeError, match="start failed") as exc:
        AudioInput.new()
    assert exc.value.__cause__ is not None
    assert any("close failed" in note for note in exc.value.__cause__.__notes__)


def test_audio_input_stops_worker_even_when_stream_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSoundDevice()
    monkeypatch.setattr("lumistripe.audio.importlib.import_module", lambda name: fake)
    audio_input = AudioInput.new()

    def fail_close() -> None:
        raise RuntimeError("close failed")

    monkeypatch.setattr(fake.streams[0], "close", fail_close)
    with pytest.raises(RuntimeError, match="close failed"):
        audio_input.close()
    assert not audio_input._worker.is_alive()
    assert not audio_input._buffer.push(np.zeros(1, dtype=np.float32))
