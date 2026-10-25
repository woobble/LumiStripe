from __future__ import annotations

import numpy as np
import pytest
from lumistripe.audio import (
    FFT_HOP_SIZE,
    AudioState,
    MusicStructurePhase,
    MusicStructureTracker,
)
from lumistripe.audio.types import MusicFeatures

from scripts.generate_audio_sync_track import make_structure_track, make_track
from scripts.validate_music_sequencer import score_structure

_SAMPLE_RATE = 48_000


def _process_native_track(
    samples: np.ndarray,
) -> tuple[list[tuple[float, MusicFeatures]], list[tuple[float, MusicStructurePhase, bool]]]:
    try:
        audio = AudioState(sample_rate=float(_SAMPLE_RATE))
    except RuntimeError as error:
        pytest.skip(f"native audio extension is required: {error}")

    tracker = MusicStructureTracker()
    features_by_time: list[tuple[float, MusicFeatures]] = []
    states: list[tuple[float, MusicStructurePhase, bool]] = []
    previous_sequence = 0
    for start in range(0, samples.size, FFT_HOP_SIZE):
        block = samples[start : start + FFT_HOP_SIZE]
        if not block.size:
            continue
        at_s = (start + block.size) / _SAMPLE_RATE
        audio.feed_samples(block, captured_at=at_s)
        frame = audio.frame()
        if frame.sequence == previous_sequence:
            continue
        previous_sequence = frame.sequence
        features = audio.music_features()
        state = tracker.update(features, at_s)
        features_by_time.append((at_s, features))
        states.append((at_s, state.phase, state.drop_event))
    return features_by_time, states


@pytest.mark.parametrize("bpm", (120.0, 128.0, 150.0))
def test_native_structure_track_classifies_all_sections_and_one_drop(bpm: float) -> None:
    samples, reference = make_structure_track(bpm, _SAMPLE_RATE)
    feature_trace, states = _process_native_track(samples)
    trace = [
        {
            "time_s": at_s,
            "phase": phase.value,
            "phase_confidence": 1.0,
            "drop_event": drop_event,
        }
        for at_s, phase, drop_event in states
    ]

    result = score_structure(reference, trace)

    assert result["status"] == "pass", result
    assert len(result["drop_event_match"]["matched"]) == 1
    assert result["drop_event_match"]["missed_expected_indices"] == []
    assert result["false_drop_times_s"] == []
    assert result["false_phase_sample_count"] == 0
    assert any(phase is MusicStructurePhase.IDLE for _, phase, _ in states)

    def section_values(name: str) -> list[MusicFeatures]:
        section = next(item for item in reference["sections"] if item["name"] == name)
        return [
            features
            for at_s, features in feature_trace
            if float(section["start_time_s"]) + 0.25
            <= at_s
            < float(section["end_time_s"]) - 0.25
        ]

    groove = section_values("groove")
    breakdown = section_values("breakdown")
    buildup = section_values("buildup")
    assert np.median([item.rolling_loudness for item in breakdown]) < np.median(
        [item.rolling_loudness for item in groove]
    ) * 0.5
    assert np.median([item.program_loudness for item in breakdown]) > np.median(
        [item.rolling_loudness for item in breakdown]
    )
    quarter = len(buildup) // 4
    assert np.median([item.rolling_loudness for item in buildup[-quarter:]]) > np.median(
        [item.rolling_loudness for item in buildup[:quarter]]
    )


def test_native_isolated_spike_and_repeated_frame_do_not_create_structure_events() -> None:
    samples = make_track(120.0, 8, _SAMPLE_RATE).copy()
    rng = np.random.default_rng(827)
    spike_start = int(7.25 * _SAMPLE_RATE)
    spike_samples = int(0.04 * _SAMPLE_RATE)
    samples[spike_start : spike_start + spike_samples] += rng.normal(
        0.0, 0.9, spike_samples
    )
    samples = np.clip(samples, -1.0, 1.0)
    feature_trace, states = _process_native_track(samples)

    assert not any(drop_event for _, _, drop_event in states)
    assert all(
        phase not in (MusicStructurePhase.BUILDUP, MusicStructurePhase.DROP)
        for _, phase, _ in states
    )

    steady = next(
        features
        for at_s, features in feature_trace
        if 9.0 <= at_s <= 9.1
    )
    tracker = MusicStructureTracker()
    repeated_states = [
        tracker.update(steady, 0.1 * index)
        for index in range(100)
    ]
    assert all(state.phase is not MusicStructurePhase.BUILDUP for state in repeated_states)
    assert not any(state.drop_event for state in repeated_states)
