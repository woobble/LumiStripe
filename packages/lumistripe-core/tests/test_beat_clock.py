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


def test_beat_clock_reanchors_and_relocks_after_long_silence() -> None:
    tracker = BeatClockTracker()
    period = 60.0 / 128.0
    for index in range(12):
        state = tracker.update(
            (BeatEvent(index + 1, index * period, 0.9),),
            now_s=index * period,
            fallback_bpm=128.0,
        )

    recovery_at = 11 * period + 6.0
    recovered = tracker.update(
        (BeatEvent(13, recovery_at, 0.9),),
        now_s=recovery_at + 0.01,
        fallback_bpm=128.0,
    )

    assert state.beat_index == 12
    assert recovered.beat_index >= state.beat_index + 12
    assert recovered.phase == pytest.approx(0.01 / period, abs=0.01)
    assert recovered.confidence == 0.0

    for index in range(1, 9):
        recovered = tracker.update(
            (BeatEvent(13 + index, recovery_at + index * period, 0.9),),
            now_s=recovery_at + index * period + 0.01,
            fallback_bpm=128.0,
        )

    assert recovered.bpm == pytest.approx(128.0, abs=1.0)
    assert recovered.confidence > 0.8
    assert recovered.phase == pytest.approx(0.02, abs=0.04)


@pytest.mark.parametrize("now_s", [float("nan"), float("inf"), float("-inf")])
def test_beat_clock_rejects_non_finite_update_time_without_mutating(
    now_s: float,
) -> None:
    tracker = BeatClockTracker()
    before = tracker.state(0.0)

    with pytest.raises(ValueError, match="now_s must be finite"):
        tracker.update((BeatEvent(1, 0.0, 0.9),), now_s=now_s)

    assert tracker.state(0.0) == before


@pytest.mark.parametrize("now_s", [float("nan"), float("inf"), float("-inf")])
def test_beat_clock_state_rejects_non_finite_time(now_s: float) -> None:
    tracker = BeatClockTracker()
    tracker.update((BeatEvent(1, 0.0, 0.9),), now_s=0.0)
    before = tracker.state(0.0)

    with pytest.raises(ValueError, match="now_s must be finite"):
        tracker.state(now_s)

    assert tracker.state(0.0) == before


def test_beat_clock_rejects_non_finite_event_without_consuming_its_sequence() -> None:
    tracker = BeatClockTracker()
    tracker.update((BeatEvent(1, 0.0, 0.9),), now_s=0.0)

    with pytest.raises(ValueError, match="event timestamps must be finite"):
        tracker.update((BeatEvent(2, float("nan"), 0.9),), now_s=0.5)

    state = tracker.update((BeatEvent(2, 0.5, 0.9),), now_s=0.5)

    assert state.beat_index == 2
    assert state.next_beat_at == pytest.approx(1.0)


def test_beat_clock_ignores_out_of_order_event_timestamps() -> None:
    tracker = BeatClockTracker()
    tracker.update((BeatEvent(1, 1.0, 0.9),), now_s=1.0)
    tracker.update((BeatEvent(2, 0.5, 0.9),), now_s=1.0)

    state = tracker.update((BeatEvent(3, 1.5, 0.9),), now_s=1.5)

    assert state.beat_index == 2
    assert state.next_beat_at == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("min_bpm", "max_bpm", "tempo_gain", "phase_gain"),
    [
        (0.0, 200.0, 0.12, 0.35),
        (float("nan"), 200.0, 0.12, 0.35),
        (60.0, 0.0, 0.12, 0.35),
        (60.0, float("inf"), 0.12, 0.35),
        (120.0, 100.0, 0.12, 0.35),
        (60.0, 200.0, float("nan"), 0.35),
        (60.0, 200.0, -0.01, 0.35),
        (60.0, 200.0, 1.01, 0.35),
        (60.0, 200.0, 0.12, float("inf")),
        (60.0, 200.0, 0.12, -0.01),
        (60.0, 200.0, 0.12, 1.01),
    ],
)
def test_beat_clock_rejects_invalid_bounds_and_gains(
    min_bpm: float,
    max_bpm: float,
    tempo_gain: float,
    phase_gain: float,
) -> None:
    with pytest.raises(ValueError):
        BeatClockTracker(
            min_bpm=min_bpm,
            max_bpm=max_bpm,
            tempo_gain=tempo_gain,
            phase_gain=phase_gain,
        )


@pytest.mark.parametrize(
    ("min_bpm", "max_bpm", "expected_bpm"),
    [(140.0, 200.0, 140.0), (60.0, 100.0, 100.0), (100.0, 140.0, 120.0)],
)
def test_beat_clock_initial_period_and_reset_respect_configured_bounds(
    min_bpm: float,
    max_bpm: float,
    expected_bpm: float,
) -> None:
    tracker = BeatClockTracker(min_bpm=min_bpm, max_bpm=max_bpm)

    assert tracker.state(0.0).bpm == pytest.approx(expected_bpm)
    tracker.reset()
    assert tracker.state(0.0).bpm == pytest.approx(expected_bpm)
