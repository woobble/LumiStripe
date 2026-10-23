from __future__ import annotations

import pytest
from lumistripe.audio import (
    BeatClockState,
    MusicalTimelineConfig,
    MusicalTimelineTracker,
)


def _clock(
    beat_index: int,
    *,
    next_beat_at: float | None = None,
    bpm: float = 120.0,
    phase: float = 0.0,
    confidence: float = 0.9,
) -> BeatClockState:
    return BeatClockState(
        bpm=bpm,
        phase=phase,
        confidence=confidence,
        beat_index=beat_index,
        next_beat_at=next_beat_at,
    )


def test_first_observed_beat_anchors_an_estimated_four_four_bar() -> None:
    tracker = MusicalTimelineTracker()

    waiting = tracker.update(_clock(0))
    assert waiting.aligned is False
    assert waiting.beat_index is None
    assert waiting.next_bar_at is None

    state = tracker.update(
        _clock(beat_index=5, next_beat_at=10.5, phase=0.25, confidence=0.4)
    )

    assert state.aligned is True
    assert state.alignment_estimated is True
    assert state.beats_per_bar == 4
    assert state.bars_per_phrase == 8
    assert state.beat_index == 1
    assert state.beat_in_bar == 1
    assert state.bar_index == 1
    assert state.bar_in_phrase == 1
    assert state.phrase_index == 1
    assert state.beat_in_phrase == 1
    assert state.confidence == 0.4
    assert state.phase == 0.25
    assert state.next_beat_at == pytest.approx(10.5)
    assert state.next_bar_at == pytest.approx(12.0)
    assert state.next_phrase_at == pytest.approx(26.0)
    assert state.beat_event and state.bar_event and state.phrase_event


def test_boundary_events_are_one_update_edges_and_follow_four_four_grid() -> None:
    tracker = MusicalTimelineTracker()
    initial = tracker.update(_clock(1, next_beat_at=0.5))
    same_beat = tracker.update(_clock(1, next_beat_at=0.5, phase=0.7))
    second_beat = tracker.update(_clock(2, next_beat_at=1.0))
    next_bar = tracker.update(_clock(5, next_beat_at=2.5))
    next_phrase = tracker.update(_clock(33, next_beat_at=16.5))

    assert initial.beat_event and initial.bar_event and initial.phrase_event
    assert not same_beat.beat_event
    assert not same_beat.bar_event
    assert not same_beat.phrase_event
    assert second_beat.beat_index == 2
    assert second_beat.beat_in_bar == 2
    assert second_beat.beat_event
    assert not second_beat.bar_event
    assert not second_beat.phrase_event
    assert next_bar.beat_index == 5
    assert next_bar.beat_in_bar == 1
    assert next_bar.bar_index == 2
    assert next_bar.bar_in_phrase == 2
    assert next_bar.beat_event and next_bar.bar_event
    assert not next_bar.phrase_event
    assert next_phrase.beat_index == 33
    assert next_phrase.bar_index == 9
    assert next_phrase.bar_in_phrase == 1
    assert next_phrase.phrase_index == 2
    assert next_phrase.phrase_event
    assert next_phrase.next_bar_at == pytest.approx(18.0)
    assert next_phrase.next_phrase_at == pytest.approx(32.0)


def test_phrase_length_is_configurable_and_validated() -> None:
    tracker = MusicalTimelineTracker(MusicalTimelineConfig(bars_per_phrase=2))

    tracker.update(_clock(1, next_beat_at=0.5))
    last_beat_of_phrase = tracker.update(_clock(8, next_beat_at=4.0))
    next_phrase = tracker.update(_clock(9, next_beat_at=4.5))

    assert last_beat_of_phrase.bar_in_phrase == 2
    assert last_beat_of_phrase.beat_in_phrase == 8
    assert last_beat_of_phrase.phrase_index == 1
    assert last_beat_of_phrase.next_phrase_at == pytest.approx(4.0)
    assert next_phrase.bar_in_phrase == 1
    assert next_phrase.beat_in_phrase == 1
    assert next_phrase.phrase_index == 2
    assert next_phrase.phrase_event

    with pytest.raises(ValueError, match="positive integer"):
        MusicalTimelineConfig(bars_per_phrase=0)
    with pytest.raises(ValueError, match="positive integer"):
        MusicalTimelineConfig(bars_per_phrase=1.5)  # type: ignore[arg-type]


def test_positions_continue_when_confidence_fades() -> None:
    tracker = MusicalTimelineTracker()
    tracker.update(_clock(1, next_beat_at=0.5, confidence=0.9))

    uncertain = tracker.update(
        _clock(6, next_beat_at=3.0, phase=0.6, confidence=0.1)
    )

    assert uncertain.aligned is True
    assert uncertain.beat_index == 6
    assert uncertain.beat_in_bar == 2
    assert uncertain.bar_index == 2
    assert uncertain.confidence == 0.1
    assert uncertain.next_beat_at == pytest.approx(3.0)
    assert uncertain.next_bar_at == pytest.approx(4.0)
    assert uncertain.beat_event
    assert uncertain.bar_event


def test_clock_index_regression_and_explicit_reset_reanchor_the_timeline() -> None:
    tracker = MusicalTimelineTracker()
    tracker.update(_clock(40, next_beat_at=20.5))
    tracker.update(_clock(42, next_beat_at=21.5))

    regressed = tracker.update(_clock(2, next_beat_at=1.5))
    assert regressed.beat_index == 1
    assert regressed.bar_index == 1
    assert regressed.phrase_index == 1
    assert regressed.beat_event and regressed.bar_event and regressed.phrase_event

    tracker.reset()
    unaligned = tracker.state
    assert unaligned.aligned is False
    assert unaligned.beat_index is None
    reanchored = tracker.update(_clock(7, next_beat_at=3.5))
    assert reanchored.beat_index == 1
    assert reanchored.alignment_estimated is True
