"""Validated animation sequences and confidence-gated bar scheduling."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..audio.timeline import MusicalTimelineState

MAX_SEQUENCE_STEPS = 32
_STEP_DURATIONS_BARS = frozenset({1, 2, 4, 8})


@dataclass(frozen=True, slots=True)
class AnimationSequenceStep:
    """One animation and the number of estimated bars it occupies."""

    animation_name: str
    duration_bars: int

    def __post_init__(self) -> None:
        if not isinstance(self.animation_name, str) or not self.animation_name.strip():
            raise ValueError("animation_name must not be empty")
        object.__setattr__(self, "animation_name", self.animation_name.strip())
        if (
            isinstance(self.duration_bars, bool)
            or not isinstance(self.duration_bars, int)
            or self.duration_bars not in _STEP_DURATIONS_BARS
        ):
            raise ValueError("duration_bars must be 1, 2, 4, or 8")


@dataclass(frozen=True, slots=True)
class AnimationSequence:
    """Immutable ordered sequence definition."""

    id: str
    name: str
    steps: tuple[AnimationSequenceStep, ...]
    repeat: bool

    def __post_init__(self) -> None:
        for field_name in ("id", "name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value.strip())
        steps = tuple(self.steps)
        if not steps:
            raise ValueError("a sequence must contain at least one step")
        if len(steps) > MAX_SEQUENCE_STEPS:
            raise ValueError(f"a sequence may contain at most {MAX_SEQUENCE_STEPS} steps")
        if any(not isinstance(step, AnimationSequenceStep) for step in steps):
            raise TypeError("steps must contain AnimationSequenceStep values")
        object.__setattr__(self, "steps", steps)
        if not isinstance(self.repeat, bool):
            raise TypeError("repeat must be a boolean")


@dataclass(frozen=True, slots=True)
class SequencePlaybackState:
    """Immutable sequence progress; step indexes are zero-based."""

    sequence_id: str | None = None
    active: bool = False
    waiting_for_clock: bool = False
    current_step_index: int | None = None
    animation_name: str | None = None
    remaining_bars: int | None = None
    next_transition_at_s: float | None = None

    def __post_init__(self) -> None:
        if self.sequence_id is not None and (
            not isinstance(self.sequence_id, str) or not self.sequence_id.strip()
        ):
            raise ValueError("sequence_id must be a non-empty string or None")
        if not isinstance(self.active, bool) or not isinstance(
            self.waiting_for_clock, bool
        ):
            raise TypeError("active and waiting_for_clock must be booleans")
        if self.current_step_index is not None and (
            isinstance(self.current_step_index, bool)
            or not isinstance(self.current_step_index, int)
            or self.current_step_index < 0
        ):
            raise ValueError("current_step_index must be a non-negative integer")
        if self.animation_name is not None and (
            not isinstance(self.animation_name, str) or not self.animation_name.strip()
        ):
            raise ValueError("animation_name must be a non-empty string or None")
        if self.remaining_bars is not None and (
            isinstance(self.remaining_bars, bool)
            or not isinstance(self.remaining_bars, int)
            or self.remaining_bars < 0
        ):
            raise ValueError("remaining_bars must be a non-negative integer or None")
        if self.next_transition_at_s is not None and not math.isfinite(
            self.next_transition_at_s
        ):
            raise ValueError("next_transition_at_s must be finite or None")
        if self.active and (
            self.sequence_id is None
            or self.current_step_index is None
            or self.animation_name is None
            or self.remaining_bars is None
        ):
            raise ValueError("active state must identify its current sequence step")


class AnimationSequenceCoordinator:
    """Schedule sequence activation and transitions on confident bar boundaries.

    Progress counts only bar boundaries observed while the clock is confident.
    A lost or relocked clock and a skipped frame interval re-anchor the pending
    boundary without consuming the remaining duration.
    """

    __slots__ = (
        "_clock_valid",
        "_completed",
        "_current_step_index",
        "_last_bar_index",
        "_last_boundary_at_s",
        "_last_now_s",
        "_next_transition_at_s",
        "_remaining_bars",
        "_session_active",
        "_state",
        "_target_bar_index",
        "confidence_threshold",
        "sequence",
        "subdivisions_per_beat",
    )

    def __init__(
        self,
        sequence: AnimationSequence | None = None,
        *,
        confidence_threshold: float,
        subdivisions_per_beat: int,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between zero and one")
        if (
            isinstance(subdivisions_per_beat, bool)
            or subdivisions_per_beat not in (1, 2, 4)
        ):
            raise ValueError("subdivisions_per_beat must be 1, 2, or 4")
        if sequence is not None and not isinstance(sequence, AnimationSequence):
            raise TypeError("sequence must be an AnimationSequence or None")
        self.sequence = sequence
        self.confidence_threshold = confidence_threshold
        self.subdivisions_per_beat = subdivisions_per_beat
        self._state = SequencePlaybackState(
            sequence_id=None if sequence is None else sequence.id
        )
        self._session_active = False
        self._completed = False
        self._clock_valid = False
        self._current_step_index: int | None = None
        self._remaining_bars: int | None = None
        self._last_bar_index: int | None = None
        self._target_bar_index: int | None = None
        self._last_boundary_at_s: float | None = None
        self._next_transition_at_s: float | None = None
        self._last_now_s: float | None = None

    @property
    def state(self) -> SequencePlaybackState:
        return self._state

    @property
    def controls_playback(self) -> bool:
        return (
            self.sequence is not None
            and self._session_active
            and not self._completed
        )

    def set_sequence(self, sequence: AnimationSequence | None) -> None:
        if sequence is not None and not isinstance(sequence, AnimationSequence):
            raise TypeError("sequence must be an AnimationSequence or None")
        if sequence == self.sequence:
            return
        self.sequence = sequence
        self.reset_session()

    def reset_session(self) -> None:
        """Cancel pending work; the next active session starts at step one."""

        self._session_active = False
        self._completed = False
        self._clock_valid = False
        self._current_step_index = None
        self._remaining_bars = None
        self._last_bar_index = None
        self._target_bar_index = None
        self._last_boundary_at_s = None
        self._next_transition_at_s = None
        self._last_now_s = None
        self._refresh_state(waiting_for_clock=False)

    def update(
        self,
        timeline: MusicalTimelineState,
        *,
        now_s: float,
        sync_offset_s: float,
        music_active: bool,
    ) -> str | None:
        """Return an animation name only when its sequence boundary is due."""

        if self.sequence is None:
            self.reset_session()
            return None
        if not music_active:
            self.reset_session()
            return None
        self._session_active = True
        if self._completed:
            self._refresh_state(waiting_for_clock=False)
            return None
        if (
            not math.isfinite(now_s)
            or not math.isfinite(sync_offset_s)
            or not self._timeline_is_confident(timeline)
        ):
            self._clock_valid = False
            self._next_transition_at_s = None
            self._last_now_s = now_s if math.isfinite(now_s) else None
            self._refresh_state(waiting_for_clock=True)
            return None

        if self._last_now_s is not None and now_s < self._last_now_s:
            self._clock_valid = False
        if not self._clock_valid:
            self._clock_valid = True
            self._reanchor(timeline, now_s, sync_offset_s)
            self._last_now_s = now_s
            self._refresh_state(waiting_for_clock=False)
            return None

        bar_index = timeline.bar_index
        assert bar_index is not None
        if self._current_step_index is None:
            target = self._target_bar_index
            if target is None:
                self._schedule_start(timeline, now_s, sync_offset_s)
                target = self._target_bar_index
            assert target is not None
            if bar_index > target:
                self._schedule_start(timeline, now_s, sync_offset_s)
                target = self._target_bar_index
            assert target is not None
            boundary_at = _boundary_at_bar(timeline, target)
            due_at = boundary_at + sync_offset_s
            self._next_transition_at_s = due_at
            if now_s >= due_at:
                if now_s - due_at <= self._maximum_lateness_s(timeline.bpm):
                    self._start_step(target, boundary_at)
                    self._last_now_s = now_s
                    self._refresh_state(waiting_for_clock=False)
                    assert self.sequence is not None
                    return self.sequence.steps[0].animation_name
                self._schedule_start(timeline, now_s, sync_offset_s)
            self._last_now_s = now_s
            self._refresh_state(waiting_for_clock=False)
            return None

        assert self._remaining_bars is not None
        assert self._last_bar_index is not None
        assert self._target_bar_index is not None
        if bar_index < self._last_bar_index:
            # A one-bar lead is expected after a negative sync offset: the
            # activation can occur before the timeline advances into that bar.
            before_activation_bar = (
                bar_index == self._last_bar_index - 1
                and self._last_boundary_at_s is not None
                and now_s < self._last_boundary_at_s
            )
            if not before_activation_bar:
                self._reanchor(timeline, now_s, sync_offset_s)
        elif bar_index > self._last_bar_index:
            bar_delta = bar_index - self._last_bar_index
            if bar_delta == 1:
                self._remaining_bars = max(0, self._remaining_bars - 1)
                self._last_bar_index = bar_index
                if self._remaining_bars == 0:
                    self._target_bar_index = bar_index
            else:
                # A stall skipped bars. Preserve logical progress and resume
                # from the current position instead of catching up.
                self._last_bar_index = bar_index
                self._remaining_bars = max(1, self._remaining_bars)
                self._target_bar_index = bar_index + self._remaining_bars
                self._last_boundary_at_s = _boundary_at_bar(timeline, bar_index)

        target = self._target_bar_index
        boundary_at = _boundary_at_bar(timeline, target)
        due_at = boundary_at + sync_offset_s
        self._next_transition_at_s = due_at
        if now_s >= due_at:
            if now_s - due_at <= self._maximum_lateness_s(timeline.bpm):
                animation_name = self._advance_step(target, boundary_at)
                self._last_now_s = now_s
                self._refresh_state(waiting_for_clock=False)
                return animation_name
            # The transition was missed. Keep the current step for one more
            # confident bar, then try again; never activate it retroactively.
            self._last_bar_index = bar_index
            self._remaining_bars = 1
            self._target_bar_index = bar_index + 1
            self._target_bar_index, next_boundary = _next_future_bar_boundary(
                timeline,
                self._target_bar_index,
                now_s=now_s,
                sync_offset_s=sync_offset_s,
            )
            self._remaining_bars = self._target_bar_index - bar_index
            self._next_transition_at_s = next_boundary + sync_offset_s
        self._last_now_s = now_s
        self._refresh_state(waiting_for_clock=False)
        return None

    def _reanchor(
        self,
        timeline: MusicalTimelineState,
        now_s: float,
        sync_offset_s: float,
    ) -> None:
        bar_index = timeline.bar_index
        assert bar_index is not None
        self._last_bar_index = bar_index
        self._last_boundary_at_s = _boundary_at_bar(timeline, bar_index)
        if self._current_step_index is None:
            self._schedule_start(timeline, now_s, sync_offset_s)
            return
        self._remaining_bars = max(1, self._remaining_bars or 0)
        self._target_bar_index = bar_index + self._remaining_bars
        self._target_bar_index, boundary_at = _next_future_bar_boundary(
            timeline,
            self._target_bar_index,
            now_s=now_s,
            sync_offset_s=sync_offset_s,
        )
        self._remaining_bars = self._target_bar_index - bar_index
        self._next_transition_at_s = boundary_at + sync_offset_s
        self._clock_valid = True

    def _schedule_start(
        self,
        timeline: MusicalTimelineState,
        now_s: float,
        sync_offset_s: float,
    ) -> None:
        assert timeline.bar_index is not None
        self._last_bar_index = timeline.bar_index
        self._target_bar_index = timeline.bar_index + 1
        self._target_bar_index, boundary_at = _next_future_bar_boundary(
            timeline,
            self._target_bar_index,
            now_s=now_s,
            sync_offset_s=sync_offset_s,
        )
        self._next_transition_at_s = boundary_at + sync_offset_s

    def _start_step(self, bar_index: int, boundary_at_s: float) -> None:
        assert self.sequence is not None
        self._current_step_index = 0
        self._remaining_bars = self.sequence.steps[0].duration_bars
        self._last_bar_index = bar_index
        self._target_bar_index = bar_index + self._remaining_bars
        self._last_boundary_at_s = boundary_at_s

    def _advance_step(self, boundary_bar_index: int, boundary_at_s: float) -> str | None:
        assert self.sequence is not None
        assert self._current_step_index is not None
        next_index = self._current_step_index + 1
        if next_index >= len(self.sequence.steps):
            if not self.sequence.repeat:
                self._completed = True
                self._remaining_bars = 0
                self._target_bar_index = boundary_bar_index
                self._next_transition_at_s = None
                return None
            next_index = 0
        self._current_step_index = next_index
        self._remaining_bars = self.sequence.steps[next_index].duration_bars
        self._last_bar_index = boundary_bar_index
        self._target_bar_index = boundary_bar_index + self._remaining_bars
        self._last_boundary_at_s = boundary_at_s
        return self.sequence.steps[next_index].animation_name

    def _timeline_is_confident(self, timeline: MusicalTimelineState) -> bool:
        return (
            timeline.aligned
            and timeline.confidence >= self.confidence_threshold
            and timeline.bar_index is not None
            and timeline.bar_index >= 1
            and timeline.beat_in_bar is not None
            and 1 <= timeline.beat_in_bar <= timeline.beats_per_bar
            and timeline.beats_per_bar > 0
            and math.isfinite(timeline.bpm)
            and timeline.bpm > 0.0
            and timeline.next_bar_at is not None
            and math.isfinite(timeline.next_bar_at)
            and math.isfinite(timeline.confidence)
            and 0.0 <= timeline.confidence <= 1.0
        )

    def _maximum_lateness_s(self, bpm: float) -> float:
        subdivision_s = 60.0 / bpm / self.subdivisions_per_beat
        return min(0.05, subdivision_s / 2.0)

    def _refresh_state(self, *, waiting_for_clock: bool) -> None:
        sequence = self.sequence
        step = (
            None
            if sequence is None or self._current_step_index is None
            else sequence.steps[self._current_step_index]
        )
        self._state = SequencePlaybackState(
            sequence_id=None if sequence is None else sequence.id,
            active=step is not None and not self._completed,
            waiting_for_clock=waiting_for_clock,
            current_step_index=self._current_step_index,
            animation_name=None if step is None else step.animation_name,
            remaining_bars=self._remaining_bars,
            next_transition_at_s=self._next_transition_at_s,
        )


def _boundary_at_bar(timeline: MusicalTimelineState, target_bar_index: int) -> float:
    """Estimate a target bar boundary from the current timeline position."""

    assert timeline.bar_index is not None
    assert timeline.next_bar_at is not None
    period = 60.0 / timeline.bpm
    bar_duration = timeline.beats_per_bar * period
    current_bar_start = timeline.next_bar_at - bar_duration
    return current_bar_start + (target_bar_index - timeline.bar_index) * bar_duration


def _next_future_bar_boundary(
    timeline: MusicalTimelineState,
    target_bar_index: int,
    *,
    now_s: float,
    sync_offset_s: float,
) -> tuple[int, float]:
    boundary_at = _boundary_at_bar(timeline, target_bar_index)
    due_at = boundary_at + sync_offset_s
    if due_at <= now_s:
        bar_duration = 60.0 / timeline.bpm * timeline.beats_per_bar
        skipped_bars = math.floor((now_s - due_at) / bar_duration) + 1
        target_bar_index += skipped_bars
        boundary_at += skipped_bars * bar_duration
    return target_bar_index, boundary_at


__all__ = [
    "MAX_SEQUENCE_STEPS",
    "AnimationSequence",
    "AnimationSequenceCoordinator",
    "AnimationSequenceStep",
    "SequencePlaybackState",
]
