from __future__ import annotations

import pytest
from lumistripe.audio.beat import BeatClockTracker
from lumistripe.audio.types import BeatEvent


def test_beat_clock_locks_to_synthetic_128_bpm_onsets() -> None:
    tracker = BeatClockTracker()
    period = 60.0 / 128.0
    state = None
    for index in range(12):
        state = tracker.update(
            (BeatEvent(index + 1, index * period, 0.9),),
            now_s=index * period + 0.02,
            fallback_bpm=120.0,
        )

    assert state is not None
    assert state.bpm == pytest.approx(128.0, abs=1.0)
    assert state.confidence > 0.8
    assert state.phase == pytest.approx(0.04, abs=0.04)
    assert state.next_beat_at == pytest.approx(12 * period, abs=0.02)


def test_beat_clock_corrects_double_interval_using_tempo_hint() -> None:
    tracker = BeatClockTracker()
    period = 60.0 / 140.0
    for index in range(8):
        state = tracker.update(
            (BeatEvent(index + 1, index * period * 2.0, 0.9),),
            now_s=index * period * 2.0,
            fallback_bpm=140.0,
        )

    assert state.bpm == pytest.approx(140.0, abs=1.0)
    assert state.beat_index >= 14


def test_beat_clock_ignores_repeated_half_beat_subdivisions_for_phase() -> None:
    tracker = BeatClockTracker()
    period = 60.0 / 128.0
    events = tuple(BeatEvent(index + 1, index * period / 2, 0.3) for index in range(20))
    state = tracker.update(events, now_s=events[-1].timestamp + 0.01, fallback_bpm=128.0)

    assert state.bpm == pytest.approx(128.0, abs=1.0)
    assert state.confidence > 0.7
    assert state.phase == pytest.approx(0.52, abs=0.04)


def test_beat_clock_deduplicates_sequences_and_advances_continuously() -> None:
    tracker = BeatClockTracker()
    event = BeatEvent(12, 10.0, 0.9)
    tracker.update((event,), now_s=10.0, fallback_bpm=120.0)
    state = tracker.update((event,), now_s=10.25, fallback_bpm=120.0)

    assert state.beat_index == 1
    assert state.phase == pytest.approx(0.5)
    assert state.next_beat_at == pytest.approx(10.5)


def test_beat_clock_confidence_fades_when_onsets_stop() -> None:
    tracker = BeatClockTracker()
    period = 60.0 / 128.0
    for index in range(12):
        state = tracker.update(
            (BeatEvent(index + 1, index * period, 0.9),),
            now_s=index * period,
            fallback_bpm=128.0,
        )

    stale = tracker.state(11 * period + 5 * period)

    assert state.confidence > 0.8
    assert stale.confidence < 0.65
    assert stale.bpm == pytest.approx(128.0, abs=1.0)
