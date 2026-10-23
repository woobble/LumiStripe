"""Estimated beat, bar, and phrase positions derived from the beat clock."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .types import BeatClockState

_BEATS_PER_BAR = 4


@dataclass(frozen=True, slots=True)
class MusicalTimelineConfig:
    """Configuration for the initial estimated 4/4 timeline."""

    bars_per_phrase: int = 8

    def __post_init__(self) -> None:
        if (
            isinstance(self.bars_per_phrase, bool)
            or not isinstance(self.bars_per_phrase, int)
            or self.bars_per_phrase < 1
        ):
            raise ValueError("bars_per_phrase must be a positive integer")


@dataclass(frozen=True, slots=True)
class MusicalTimelineState:
    """Immutable estimated position and upcoming musical boundaries.

    Beat, bar, and phrase indices are one-based relative to the first beat
    observed by the tracker. Boundary flags are true only when an update
    observes a new corresponding index; the initial anchor marks all three.
    """

    aligned: bool = False
    alignment_estimated: bool = False
    beats_per_bar: int = _BEATS_PER_BAR
    bars_per_phrase: int = 8
    beat_index: int | None = None
    beat_in_bar: int | None = None
    bar_index: int | None = None
    bar_in_phrase: int | None = None
    phrase_index: int | None = None
    beat_in_phrase: int | None = None
    bpm: float = 120.0
    phase: float = 0.0
    confidence: float = 0.0
    next_beat_at: float | None = None
    next_bar_at: float | None = None
    next_phrase_at: float | None = None
    beat_event: bool = False
    bar_event: bool = False
    phrase_event: bool = False


class MusicalTimelineTracker:
    """Map BeatClockState positions onto an estimated 4/4 phrase grid.

    The first positive beat index observed is treated as beat one. This is an
    estimated alignment, not downbeat detection. Beat/bar/phrase positions
    continue to follow extrapolated clock state as confidence fades; callers
    can use ``confidence`` to decide whether to act on them.
    """

    __slots__ = (
        "_anchor_beat_index",
        "_last_bar_index",
        "_last_clock_beat_index",
        "_last_phrase_index",
        "_last_timeline_beat_index",
        "_state",
        "config",
    )

    def __init__(self, config: MusicalTimelineConfig | None = None) -> None:
        self.config = config or MusicalTimelineConfig()
        self._anchor_beat_index: int | None = None
        self._last_clock_beat_index: int | None = None
        self._last_timeline_beat_index: int | None = None
        self._last_bar_index: int | None = None
        self._last_phrase_index: int | None = None
        self._state = self._unaligned_state(BeatClockState(), self.config)

    @property
    def state(self) -> MusicalTimelineState:
        return self._state

    def reset(self) -> None:
        """Clear the estimated anchor; the next observed beat starts bar one."""

        self._anchor_beat_index = None
        self._last_clock_beat_index = None
        self._last_timeline_beat_index = None
        self._last_bar_index = None
        self._last_phrase_index = None
        self._state = self._unaligned_state(BeatClockState(), self.config)

    def update(self, clock: BeatClockState) -> MusicalTimelineState:
        """Return positions and predicted boundaries for the supplied clock."""

        clock_index = clock.beat_index
        if (
            self._last_clock_beat_index is not None
            and clock_index < self._last_clock_beat_index
        ):
            self._anchor_beat_index = None
            self._last_timeline_beat_index = None
            self._last_bar_index = None
            self._last_phrase_index = None

        self._last_clock_beat_index = clock_index
        if clock_index <= 0:
            self._anchor_beat_index = None
            self._last_timeline_beat_index = None
            self._last_bar_index = None
            self._last_phrase_index = None
            self._state = self._unaligned_state(clock, self.config)
            return self._state

        if self._anchor_beat_index is None:
            self._anchor_beat_index = clock_index

        timeline_beat = clock_index - self._anchor_beat_index + 1
        zero_based_beat = timeline_beat - 1
        phrase_length = _BEATS_PER_BAR * self.config.bars_per_phrase
        beat_in_bar = zero_based_beat % _BEATS_PER_BAR + 1
        bar_index = zero_based_beat // _BEATS_PER_BAR + 1
        bar_in_phrase = (zero_based_beat // _BEATS_PER_BAR) % self.config.bars_per_phrase + 1
        phrase_index = zero_based_beat // phrase_length + 1
        beat_in_phrase = zero_based_beat % phrase_length + 1

        bpm = _valid_bpm(clock.bpm)
        next_beat_at = _valid_timestamp(clock.next_beat_at)
        next_bar_at = None
        next_phrase_at = None
        if bpm > 0.0 and next_beat_at is not None:
            beat_period = 60.0 / bpm
            beats_to_bar = _BEATS_PER_BAR - (zero_based_beat % _BEATS_PER_BAR)
            beats_to_phrase = phrase_length - (zero_based_beat % phrase_length)
            next_bar_at = next_beat_at + (beats_to_bar - 1) * beat_period
            next_phrase_at = next_beat_at + (beats_to_phrase - 1) * beat_period

        beat_event = self._last_timeline_beat_index != timeline_beat
        bar_event = self._last_bar_index != bar_index
        phrase_event = self._last_phrase_index != phrase_index
        self._last_timeline_beat_index = timeline_beat
        self._last_bar_index = bar_index
        self._last_phrase_index = phrase_index

        self._state = MusicalTimelineState(
            aligned=True,
            alignment_estimated=True,
            beats_per_bar=_BEATS_PER_BAR,
            bars_per_phrase=self.config.bars_per_phrase,
            beat_index=timeline_beat,
            beat_in_bar=beat_in_bar,
            bar_index=bar_index,
            bar_in_phrase=bar_in_phrase,
            phrase_index=phrase_index,
            beat_in_phrase=beat_in_phrase,
            bpm=bpm,
            phase=_unit_interval(clock.phase),
            confidence=_unit_interval(clock.confidence),
            next_beat_at=next_beat_at,
            next_bar_at=next_bar_at,
            next_phrase_at=next_phrase_at,
            beat_event=beat_event,
            bar_event=bar_event,
            phrase_event=phrase_event,
        )
        return self._state

    @staticmethod
    def _unaligned_state(
        clock: BeatClockState,
        config: MusicalTimelineConfig,
    ) -> MusicalTimelineState:
        return MusicalTimelineState(
            bars_per_phrase=config.bars_per_phrase,
            bpm=_valid_bpm(clock.bpm),
            phase=_unit_interval(clock.phase),
            confidence=_unit_interval(clock.confidence),
        )


def _valid_bpm(value: float) -> float:
    return value if math.isfinite(value) and value > 0.0 else 0.0


def _valid_timestamp(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return value


def _unit_interval(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, value))


__all__ = [
    "MusicalTimelineConfig",
    "MusicalTimelineState",
    "MusicalTimelineTracker",
]
