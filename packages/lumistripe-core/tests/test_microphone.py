"""Microphone conditioning tested with deterministic, device-independent signals."""

from dataclasses import replace

import numpy as np
import pytest
from lumistripe.audio.calibration import recommend_microphone_calibration
from lumistripe.audio.config import (
    AudioConfig,
    AudioNormalization,
    MicrophoneConditioning,
)
from lumistripe.audio.dsp import AudioState
from lumistripe.audio.types import AudioProcessorStats


def mic_config(floor: float = 0.003) -> AudioConfig:
    return AudioConfig(
        microphone=MicrophoneConditioning(enabled=True, input_noise_floor=floor),
        normalization=AudioNormalization(max_gain=24, music_max_gain=24),
    )


def feed(state: AudioState, samples: np.ndarray) -> None:
    for offset in range(0, len(samples), 512):
        state.feed_samples(samples[offset:offset + 512].astype(np.float32))


@pytest.mark.parametrize("rate", [44100, 48000])
def test_calibrated_room_noise_stays_idle_without_gain_runaway(rate: int) -> None:
    time = np.arange(rate * 3) / rate
    noise = np.random.default_rng(42).normal(0, 0.002, len(time))
    noise += 0.003 * np.sin(2 * np.pi * 50 * time) + 0.2
    state = AudioState(mic_config(0.004), sample_rate=rate)
    feed(state, noise)
    assert not state.stats().microphone_gate_open
    assert state.stats().normalization_gain == pytest.approx(1)
    assert state.frame().rms < 0.001
    assert not state.frame().beat
    assert state.music_features().silence


@pytest.mark.parametrize("rate", [44100, 48000])
def test_high_pass_rejects_rumble_preserves_music_and_closes_after_music(rate: int) -> None:
    time = np.arange(rate * 2) / rate
    state = AudioState(mic_config(0.001), sample_rate=rate)
    feed(state, 0.004 * np.sin(2 * np.pi * 5 * time))
    assert state.stats().input_rms < 0.0006
    assert not state.stats().microphone_gate_open
    feed(state, 0.01 * np.sin(2 * np.pi * 110 * time))
    assert state.stats().microphone_gate_open
    assert state.frame().rms > 0.05
    gain = state.stats().normalization_gain
    feed(state, np.zeros(rate))
    assert not state.stats().microphone_gate_open
    assert state.stats().normalization_gain < gain
    assert state.frame().rms < 0.001


def test_gate_hysteresis_and_hold_preserve_short_gaps() -> None:
    rate = 48000
    state = AudioState(mic_config(0.005), sample_rate=rate)
    time = np.arange(rate) / rate
    feed(state, 0.03 * np.sin(2 * np.pi * 200 * time))
    assert state.stats().microphone_gate_open
    # Between opening and closing thresholds: stay open.
    feed(state, 0.012 * np.sin(2 * np.pi * 200 * time))
    assert state.stats().microphone_gate_open
    feed(state, np.zeros(2400))
    assert state.stats().microphone_gate_open
    feed(state, np.zeros(rate))
    assert not state.stats().microphone_gate_open


@pytest.mark.parametrize("rate", [44100, 48000])
def test_kick_above_sub_bass_band_produces_beats(rate: int) -> None:
    state = AudioState(mic_config(0.001), sample_rate=rate)
    time = np.arange(rate * 3) / rate
    phase = time % 0.5
    samples = 0.08 * np.sin(2 * np.pi * 110 * time) * np.exp(-phase * 35)
    beats = 0
    for offset in range(0, len(samples), 512):
        state.feed_samples(samples[offset:offset + 512].astype(np.float32))
        beats += state.frame().beat
    assert beats >= 3


def test_clipping_measured_before_filter_and_gain() -> None:
    state = AudioState(mic_config())
    feed(state, np.ones(44100))
    assert state.stats().input_peak == 1
    assert state.stats().clipping_fraction == 1
    assert state.stats().input_rms < 0.001


@pytest.mark.parametrize("rate", [44100, 48000])
def test_loud_transition_limits_gain_without_amplifying_following_noise(rate: int) -> None:
    state = AudioState(mic_config(0.001), sample_rate=rate)
    time = np.arange(rate) / rate
    feed(state, 0.01 * np.sin(2 * np.pi * 200 * time))
    assert state.stats().normalization_gain > 5
    loud = (0.8 * np.sin(2 * np.pi * 200 * time[:2048])).astype(np.float32)
    state.feed_samples(loud)
    assert state.stats().normalization_gain < 1.3
    assert state.stats().clipping_fraction == 0
    feed(state, np.random.default_rng(4).normal(0, 0.0005, rate * 2))
    assert state.stats().normalization_gain == pytest.approx(1)
    assert not state.stats().microphone_gate_open


def test_filter_is_continuous_across_batches_and_state_copies() -> None:
    samples = np.random.default_rng(7).normal(0, 0.05, 10000).astype(np.float32)
    first = AudioState(mic_config())
    first.feed_samples(samples[:4000])
    clone = first.copy()
    first.feed_samples(samples[4000:])
    feed(clone, samples[4000:])
    assert clone.frame().rms == pytest.approx(first.frame().rms)
    assert clone.stats().input_rms == pytest.approx(first.stats().input_rms)


def test_two_step_recommendation_uses_input_levels() -> None:
    quiet = [AudioProcessorStats(input_rms=0.002, input_peak=0.01)] * 20
    music = [AudioProcessorStats(input_rms=0.02, input_peak=0.15, normalization_gain=20)] * 20
    result = recommend_microphone_calibration(quiet, music)
    assert result.input_noise_floor == pytest.approx(0.0022)
    assert result.measured_music_level == 0.02
    assert result.measured_peak == 0.15
    assert result.target_level == 0.24
    assert result == recommend_microphone_calibration(quiet, [replace(s, normalization_gain=1) for s in music])


@pytest.mark.parametrize("problem", ["missing", "noise", "clipping", "invalid"])
def test_two_step_rejects_bad_measurements(problem: str) -> None:
    quiet = [AudioProcessorStats(input_rms=0.002)] * 20
    music = [AudioProcessorStats(input_rms=0.02)] * 20
    if problem == "missing":
        music = []
    elif problem == "noise":
        music = quiet
    elif problem == "clipping":
        music[0] = replace(music[0], clipping_fraction=0.01)
    else:
        music[0] = replace(music[0], input_rms=float("nan"))
    with pytest.raises(ValueError):
        recommend_microphone_calibration(quiet, music)


def test_raw_mode_leaves_microphone_conditioning_disabled() -> None:
    assert not AudioConfig.raw().microphone.enabled
