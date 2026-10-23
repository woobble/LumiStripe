"""Confidence-gated scheduling for Dynamic base-animation switches."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from ..audio.timeline import MusicalTimelineState
from ..selector.scoring import SelectorDecision
from .config import SwitchQuantization


@dataclass(frozen=True, slots=True)
class _PendingSwitch:
    decision: SelectorDecision
    target_position: int
    last_position: int
    last_beat_index: int
    last_now_s: float
    boundary_at_s: float


@dataclass(frozen=True, slots=True)
class QuantizedSwitchDiagnostics:
    """Immutable timeline and pending-switch diagnostics for clients."""

    mode: SwitchQuantization = SwitchQuantization.OFF
    beats_per_bar: int = 4
    bars_per_phrase: int = 8
    beat_index: int | None = None
    beat_in_bar: int | None = None
    bar_index: int | None = None
    bar_in_phrase: int | None = None
    phrase_index: int | None = None
    beat_in_phrase: int | None = None
    bpm: float = 0.0
    beat_clock_confidence: float = 0.0
    alignment_estimated: bool = False
    pending_animation: str | None = None
    next_switch_in_s: float | None = None
    waiting_for_clock: bool = False


class QuantizedSwitchCoordinator:
    """Defer Dynamic selector candidates to a confident musical boundary."""

    __slots__ = ("_pending", "confidence_threshold", "mode", "subdivisions_per_beat")

    def __init__(
        self,
        mode: SwitchQuantization,
        *,
        confidence_threshold: float,
        subdivisions_per_beat: int,
    ) -> None:
        self.mode = mode
        self.confidence_threshold = confidence_threshold
        self.subdivisions_per_beat = subdivisions_per_beat
        self._pending: _PendingSwitch | None = None

    @property
    def pending_decision(self) -> SelectorDecision | None:
        return None if self._pending is None else self._pending.decision

    def reset(self) -> None:
        self._pending = None

    def set_mode(self, mode: SwitchQuantization) -> None:
        if mode is self.mode:
            return
        self._pending = None
        self.mode = mode

    def diagnostics(
        self,
        timeline: MusicalTimelineState,
        *,
        now_s: float,
        sync_offset_s: float,
        candidate_waiting: bool,
    ) -> QuantizedSwitchDiagnostics:
        """Return public state without exposing the pending-switch record."""

        pending = self._pending
        next_switch_at = (
            None
            if pending is None
            else pending.boundary_at_s + sync_offset_s
        )
        position_data = self._position_data(timeline)
        waiting_for_clock = (
            self.mode is not SwitchQuantization.OFF
            and candidate_waiting
            and position_data is None
        )
        return QuantizedSwitchDiagnostics(
            mode=self.mode,
            beats_per_bar=timeline.beats_per_bar,
            bars_per_phrase=timeline.bars_per_phrase,
            beat_index=timeline.beat_index,
            beat_in_bar=timeline.beat_in_bar,
            bar_index=timeline.bar_index,
            bar_in_phrase=timeline.bar_in_phrase,
            phrase_index=timeline.phrase_index,
            beat_in_phrase=timeline.beat_in_phrase,
            bpm=timeline.bpm,
            beat_clock_confidence=timeline.confidence,
            alignment_estimated=timeline.alignment_estimated,
            pending_animation=(
                None if pending is None else pending.decision.selected_name
            ),
            next_switch_in_s=(
                None
                if next_switch_at is None
                else max(0.0, next_switch_at - now_s)
            ),
            waiting_for_clock=waiting_for_clock,
        )

    def update(
        self,
        decision: SelectorDecision,
        timeline: MusicalTimelineState,
        *,
        now_s: float,
        sync_offset_s: float,
        transition_active: bool = False,
    ) -> SelectorDecision | None:
        """Reconcile the latest selector intent and return a due candidate.

        A candidate first observed on a boundary is scheduled for the next one.
        Existing pending intent is replaced without changing its boundary.
        Clock stalls, regressions, or lost confidence discard stale work.
        """

        if self.mode is SwitchQuantization.OFF:
            self.reset()
            return None
        position_data = self._position_data(timeline)
        if position_data is None or not self._valid_intent(decision):
            self.reset()
            return None

        position, beat_index, next_boundary_at, stride_beats = position_data
        pending = self._pending
        if pending is not None:
            beat_delta = beat_index - pending.last_beat_index
            if (
                now_s < pending.last_now_s
                or position < pending.last_position
                or beat_delta < 0
                or beat_delta > 1
            ):
                pending = None

        period = 60.0 / timeline.bpm
        grid_period = period * stride_beats
        if pending is not None:
            pending = replace(
                pending,
                decision=decision,
                last_position=position,
                last_beat_index=beat_index,
                last_now_s=now_s,
            )
            if position <= pending.target_position:
                if position < pending.target_position:
                    boundary_at = next_boundary_at + (
                        pending.target_position - position - 1
                    ) * grid_period
                else:
                    boundary_at = next_boundary_at - grid_period
                pending = replace(pending, boundary_at_s=boundary_at)

            due_at = pending.boundary_at_s + sync_offset_s
            if now_s >= due_at:
                lateness_limit = self._maximum_lateness_s(timeline.bpm)
                if now_s - due_at > lateness_limit:
                    pending = None
                elif transition_active:
                    pending = self._schedule_next(
                        decision,
                        timeline,
                        position=position,
                        beat_index=beat_index,
                        now_s=now_s,
                        sync_offset_s=sync_offset_s,
                        next_boundary_at=next_boundary_at,
                        stride_beats=stride_beats,
                    )
                    self._pending = pending
                    return None
                else:
                    self._pending = None
                    return decision

        if pending is None:
            pending = self._schedule_next(
                decision,
                timeline,
                position=position,
                beat_index=beat_index,
                now_s=now_s,
                sync_offset_s=sync_offset_s,
                next_boundary_at=next_boundary_at,
                stride_beats=stride_beats,
            )
        self._pending = pending
        return None

    def _schedule_next(
        self,
        decision: SelectorDecision,
        timeline: MusicalTimelineState,
        *,
        position: int,
        beat_index: int,
        now_s: float,
        sync_offset_s: float,
        next_boundary_at: float,
        stride_beats: int,
    ) -> _PendingSwitch:
        grid_period = 60.0 / timeline.bpm * stride_beats
        target = position + 1
        boundary_at = next_boundary_at
        # Negative synchronization offsets may put the next predicted boundary
        # in the past. Advance to the first matching boundary still in future.
        while boundary_at + sync_offset_s <= now_s:
            target += 1
            boundary_at += grid_period
        return _PendingSwitch(
            decision=decision,
            target_position=target,
            last_position=position,
            last_beat_index=beat_index,
            last_now_s=now_s,
            boundary_at_s=boundary_at,
        )

    def _position_data(
        self, timeline: MusicalTimelineState
    ) -> tuple[int, int, float, int] | None:
        if (
            not timeline.aligned
            or timeline.confidence < self.confidence_threshold
            or timeline.beat_index is None
            or not math.isfinite(timeline.bpm)
            or timeline.bpm <= 0.0
        ):
            return None
        if self.mode is SwitchQuantization.BEAT:
            position = timeline.beat_index
            next_boundary_at = timeline.next_beat_at
            stride_beats = 1
        elif self.mode is SwitchQuantization.BAR:
            if timeline.bar_index is None:
                return None
            position = timeline.bar_index
            next_boundary_at = timeline.next_bar_at
            stride_beats = timeline.beats_per_bar
        elif self.mode is SwitchQuantization.PHRASE:
            if timeline.phrase_index is None:
                return None
            position = timeline.phrase_index
            next_boundary_at = timeline.next_phrase_at
            stride_beats = timeline.beats_per_bar * timeline.bars_per_phrase
        else:
            return None
        if (
            next_boundary_at is None
            or not math.isfinite(next_boundary_at)
            or stride_beats <= 0
        ):
            return None
        return position, timeline.beat_index, next_boundary_at, stride_beats

    @staticmethod
    def _valid_intent(decision: SelectorDecision) -> bool:
        return (
            decision.should_switch
            and decision.selected_name is not None
            and decision.selected_name != decision.current_name
        )

    def _maximum_lateness_s(self, bpm: float) -> float:
        # Match EffectScheduler's late-tick protection so base switches obey
        # the same per-frame timing tolerance as synchronized effects.
        subdivision_s = 60.0 / bpm / self.subdivisions_per_beat
        return min(0.05, subdivision_s / 2.0)


__all__ = ["QuantizedSwitchCoordinator", "QuantizedSwitchDiagnostics"]
