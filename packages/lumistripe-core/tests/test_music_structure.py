from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from lumistripe.audio import (
    MusicStructurePhase,
    MusicStructureState,
    MusicStructureTracker,
)
from lumistripe.audio.types import MusicFeatures


def _features(
    energy: float,
    *,
    bass: float | None = None,
    onset: float = 0.0,
    beat: bool = False,
    drop_detected: bool = False,
    section_change: bool = False,
    silence: bool = False,
) -> MusicFeatures:
    return MusicFeatures(
        energy=energy,
        volume=energy,
        energy_level=energy,
        bass=energy * 0.7 if bass is None else bass,
        bass_energy=energy * 0.7 if bass is None else bass,
        onset_strength=onset,
        spectral_flux=onset,
        beat=beat,
        beat_strength=onset,
        drop_detected=drop_detected,
        section_change=section_change,
        silence=silence,
    )


def _feed(
    tracker: MusicStructureTracker,
    value: MusicFeatures,
    timestamp: float,
) -> MusicStructureState:
    return tracker.update(value, timestamp)


def test_gradual_energy_rise_is_classified_as_build_up_with_progress() -> None:
    tracker = MusicStructureTracker()
    states = []
    for index in range(45):
        timestamp = index * 0.1
        energy = 0.18 + 0.008 * index
        states.append(_feed(tracker, _features(energy), timestamp))

    buildup_states = [
        state for state in states if state.phase is MusicStructurePhase.BUILDUP
    ]
    assert buildup_states
    assert buildup_states[-1].buildup_progress > buildup_states[0].buildup_progress
    assert buildup_states[-1].confidence >= 0.55


def test_steady_music_stabilizes_in_groove_without_false_build_or_drop() -> None:
    tracker = MusicStructureTracker()
    states = [
        _feed(tracker, _features(0.42, onset=0.04), index * 0.1) for index in range(50)
    ]

    assert states[-1].phase is MusicStructurePhase.GROOVE
    assert not any(state.phase is MusicStructurePhase.BUILDUP for state in states[20:])
    assert not any(state.drop_event for state in states)


def test_breakdown_requires_sustained_low_non_silent_energy() -> None:
    tracker = MusicStructureTracker()
    for index in range(20):
        state = _feed(tracker, _features(0.5), index * 0.1)
    for index in range(20, 36):
        state = _feed(tracker, _features(0.06), index * 0.1)

    assert state.phase is MusicStructurePhase.BREAKDOWN
    assert state.confidence > 0.6


def test_silence_immediately_falls_back_to_idle_and_clears_build_up() -> None:
    tracker = MusicStructureTracker()
    for index in range(40):
        state = _feed(tracker, _features(0.18 + index * 0.008), index * 0.1)
    assert state.phase is MusicStructurePhase.BUILDUP

    state = _feed(tracker, _features(0.0, silence=True), 4.0)

    assert state.phase is MusicStructurePhase.IDLE
    assert state.confidence == 1.0
    assert state.buildup_progress == 0.0
    assert not state.drop_event


def test_drop_needs_build_up_and_emits_one_update_edge() -> None:
    tracker = MusicStructureTracker()
    for index in range(40):
        state = _feed(tracker, _features(0.18 + index * 0.008), index * 0.1)
    assert state.phase is MusicStructurePhase.BUILDUP

    state = _feed(
        tracker,
        _features(0.72, bass=0.7, onset=0.35, beat=True),
        4.0,
    )
    assert state.phase is MusicStructurePhase.DROP
    assert state.drop_event
    assert state.buildup_progress == 1.0

    next_state = _feed(tracker, _features(0.72, bass=0.7), 4.1)
    assert next_state.phase is MusicStructurePhase.DROP
    assert not next_state.drop_event


def test_drop_flag_without_preceding_build_up_is_ignored() -> None:
    tracker = MusicStructureTracker()
    state = MusicStructureState()
    for index in range(20):
        state = _feed(
            tracker,
            _features(0.45, bass=0.8, onset=0.5, drop_detected=True, beat=True),
            index * 0.1,
        )

    assert state.phase is MusicStructurePhase.GROOVE
    assert not state.drop_event


def test_drop_cooldown_suppresses_second_drop_during_a_new_build_up() -> None:
    tracker = MusicStructureTracker()
    for index in range(40):
        state = _feed(tracker, _features(0.18 + index * 0.008), index * 0.1)
    state = _feed(tracker, _features(0.72, bass=0.7, onset=0.35, beat=True), 4.0)
    assert state.drop_event

    timestamp = 4.1
    while timestamp < 5.1:
        _feed(tracker, _features(0.06), timestamp)
        timestamp += 0.1
    for index in range(24):
        timestamp = 5.2 + index * 0.1
        state = _feed(tracker, _features(0.16 + index * 0.015), timestamp)

    assert state.phase is MusicStructurePhase.BUILDUP
    state = _feed(tracker, _features(0.68, bass=0.7, onset=0.35, beat=True), 7.7)

    assert state.phase is MusicStructurePhase.BUILDUP
    assert not state.drop_event


def test_history_is_bounded_by_sample_count_and_time_window() -> None:
    tracker = MusicStructureTracker()
    for index in range(500):
        _feed(tracker, _features(0.4, onset=0.02), index * 0.05)

    history = tracker._history
    assert len(history) <= 256
    assert history[-1].timestamp - history[0].timestamp <= 8.0


def test_irregular_frame_intervals_use_elapsed_time_for_smoothing_and_hysteresis() -> (
    None
):
    tracker = MusicStructureTracker()
    timestamps = [0.0, 0.08, 0.21, 0.47, 0.83, 1.2, 1.65, 2.2, 2.9, 3.6, 4.3]
    states = [
        _feed(tracker, _features(0.17 + min(timestamp, 3.0) * 0.12), timestamp)
        for timestamp in timestamps
    ]

    assert states[-1].phase is MusicStructurePhase.BUILDUP
    assert all(0.0 <= state.confidence <= 1.0 for state in states)
    assert all(0.0 <= state.buildup_progress <= 1.0 for state in states)


def test_large_timestamp_gap_resets_classification_before_new_audio() -> None:
    tracker = MusicStructureTracker()
    for index in range(40):
        state = _feed(tracker, _features(0.18 + index * 0.008), index * 0.1)
    assert state.phase is MusicStructurePhase.BUILDUP

    state = _feed(tracker, _features(0.42), 8.0)

    assert state.phase is MusicStructurePhase.IDLE
    assert not state.drop_event


def test_reset_clears_history_cooldown_and_progress() -> None:
    tracker = MusicStructureTracker()
    for index in range(40):
        state = _feed(tracker, _features(0.18 + index * 0.008), index * 0.1)
    assert state.phase is MusicStructurePhase.BUILDUP

    tracker.reset()
    state = _feed(tracker, _features(0.4), 0.0)

    assert state.phase is MusicStructurePhase.IDLE
    assert state.buildup_progress == 0.0
    assert not state.drop_event


def test_timestamp_must_be_finite_and_monotonic() -> None:
    tracker = MusicStructureTracker()
    _feed(tracker, _features(0.3), 1.0)

    with pytest.raises(ValueError, match="finite"):
        tracker.update(_features(0.3), float("nan"))
    with pytest.raises(ValueError, match="monotonic"):
        tracker.update(_features(0.3), 0.9)


def test_structure_state_is_immutable() -> None:
    state = MusicStructureState()

    with pytest.raises(FrozenInstanceError):
        state.confidence = 0.5  # type: ignore[misc]
