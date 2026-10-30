"""Beat-clock tracking built on timestamped onset events."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .types import BeatClockState, BeatEvent

_MIN_BEAT_INTERVAL_SECONDS = 0.18
_MAX_CONTIGUOUS_BEAT_GAP_SECONDS = 2.0


@dataclass(slots=True)
class BeatClockTracker:
    """A small PLL that follows onsets without resetting phase on every transient."""

    min_bpm: float = 60.0
    max_bpm: float = 200.0
    tempo_gain: float = 0.12
    phase_gain: float = 0.35
    _period: float = field(default=0.5, init=False)
    _last_beat_at: float | None = field(default=None, init=False)
    _last_event_at: float | None = field(default=None, init=False)
    _last_sequence: int = field(default=0, init=False)
    _beat_index: int = field(default=0, init=False)
    _interval_errors: deque[float] = field(default_factory=lambda: deque(maxlen=12), init=False)
    _candidate_period: float | None = field(default=None, init=False)
    _candidate_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.min_bpm)
            or not math.isfinite(self.max_bpm)
            or self.min_bpm <= 0.0
            or self.max_bpm < self.min_bpm
        ):
            raise ValueError("BPM bounds must be finite, positive, and ordered")
        if (
            not math.isfinite(self.tempo_gain)
            or not 0.0 <= self.tempo_gain <= 1.0
            or not math.isfinite(self.phase_gain)
            or not 0.0 <= self.phase_gain <= 1.0
        ):
            raise ValueError("beat-clock gains must be finite values in [0, 1]")
        self._period = self._initial_period()

    def _initial_period(self) -> float:
        initial_bpm = min(self.max_bpm, max(self.min_bpm, 120.0))
        return 60.0 / initial_bpm

    def reset(self) -> None:
        self._period = self._initial_period()
        self._last_beat_at = None
        self._last_event_at = None
        self._last_sequence = 0
        self._beat_index = 0
        self._interval_errors.clear()
        self._candidate_period = None
        self._candidate_count = 0

    def update(
        self,
        events: tuple[BeatEvent, ...],
        *,
        now_s: float,
        fallback_bpm: float | None = None,
    ) -> BeatClockState:
        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        next_sequence = self._last_sequence
        for event in events:
            if event.sequence <= next_sequence:
                continue
            if not math.isfinite(event.timestamp):
                raise ValueError("beat event timestamps must be finite")
            next_sequence = event.sequence
        for event in events:
            if event.sequence <= self._last_sequence:
                continue
            self._last_sequence = event.sequence
            self._observe(event, fallback_bpm)
        return self.state(now_s)

    def state(self, now_s: float) -> BeatClockState:
        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        last = self._last_beat_at
        if last is None:
            return BeatClockState(bpm=60.0 / self._period)
        elapsed = max(0.0, now_s - last)
        beats_since = math.floor(elapsed / self._period)
        phase = (elapsed / self._period) % 1.0
        current_beat_at = last + beats_since * self._period
        next_beat = current_beat_at + self._period
        stability = 1.0
        if self._interval_errors:
            stability = math.exp(-4.0 * sum(self._interval_errors) / len(self._interval_errors))
        lock = min(1.0, len(self._interval_errors) / 5.0)
        last_event = self._last_event_at
        stale_seconds = (
            0.0
            if last_event is None
            else max(0.0, now_s - last_event - 2.0 * self._period)
        )
        freshness = math.exp(-stale_seconds / (3.0 * self._period))
        confidence = max(0.0, min(1.0, lock * stability * freshness))
        return BeatClockState(
            bpm=60.0 / self._period,
            phase=phase,
            confidence=confidence,
            beat_index=self._beat_index + beats_since,
            next_beat_at=next_beat,
            last_beat_at=current_beat_at,
        )

    def _observe(self, event: BeatEvent, fallback_bpm: float | None) -> None:
        if self._last_event_at is None:
            normalized_bpm = _normalize_bpm(fallback_bpm, self.min_bpm, self.max_bpm)
            if normalized_bpm is not None:
                self._period = 60.0 / normalized_bpm
            self._last_event_at = event.timestamp
            self._last_beat_at = event.timestamp
            self._beat_index += 1
            return

        if event.timestamp <= self._last_event_at:
            return
        interval = event.timestamp - self._last_event_at
        self._last_event_at = event.timestamp
        if interval > _MAX_CONTIGUOUS_BEAT_GAP_SECONDS:
            previous_beat_at = self._last_beat_at
            if previous_beat_at is not None:
                elapsed_beats = int(
                    (event.timestamp - previous_beat_at) / self._period
                )
                self._beat_index += max(1, elapsed_beats)
            self._last_beat_at = event.timestamp
            self._interval_errors.clear()
            self._candidate_period = None
            self._candidate_count = 0
            return
        if interval < _MIN_BEAT_INTERVAL_SECONDS:
            return

        current_period = self._period
        candidate = self._choose_candidate(interval, fallback_bpm)
        if not self._interval_errors:
            if candidate is not None:
                self._period = candidate
                if candidate > interval * 1.25:
                    self._interval_errors.append(0.0)
                    return
            self._last_beat_at = event.timestamp
            elapsed_beats = max(1, round(interval / self._period))
            self._beat_index += elapsed_beats
            self._interval_errors.append(0.0)
            return

        ratio = interval / current_period
        if ratio < 0.68:
            # Repeated subdivisions (for example hi-hats) do not pull the beat
            # phase forward by half a beat.
            residual = min(
                abs(ratio - round(ratio)),
                abs(ratio * 2.0 - round(ratio * 2.0)),
            )
            self._interval_errors.append(min(1.0, residual))
            return

        beat_multiple = max(1, min(4, round(ratio)))
        measured_period = interval / beat_multiple
        if candidate is not None and abs(candidate - current_period) / current_period > 0.15:
            if self._candidate_period is not None and abs(candidate - self._candidate_period) / candidate < 0.06:
                self._candidate_count += 1
            else:
                self._candidate_period = candidate
                self._candidate_count = 1
            if self._candidate_count >= 3:
                self._period = candidate
                self._candidate_period = None
                self._candidate_count = 0
        else:
            self._candidate_period = None
            self._candidate_count = 0
            target_period = candidate if candidate is not None else measured_period
            self._period += (target_period - self._period) * self.tempo_gain

        predicted = (self._last_beat_at or event.timestamp) + beat_multiple * self._period
        phase_error = event.timestamp - predicted
        self._interval_errors.append(min(1.0, abs(phase_error) / self._period))
        self._last_beat_at = predicted + phase_error * self.phase_gain
        self._beat_index += beat_multiple

    def _choose_candidate(self, interval: float, fallback_bpm: float | None) -> float | None:
        low_period = 60.0 / self.max_bpm
        high_period = 60.0 / self.min_bpm
        candidates = {
            interval * multiple
            for multiple in (0.5, 1.0, 2.0, 3.0, 4.0)
            if low_period <= interval * multiple <= high_period
        }
        candidates.update(
            interval / multiple
            for multiple in (2.0, 3.0, 4.0)
            if low_period <= interval / multiple <= high_period
        )
        if not candidates:
            return None
        reference = self._period
        normalized = _normalize_bpm(fallback_bpm, self.min_bpm, self.max_bpm)
        if normalized is not None and self._beat_index < 4:
            reference = 60.0 / normalized
        return min(candidates, key=lambda value: abs(math.log(value / reference)))


def _normalize_bpm(value: float | None, minimum: float, maximum: float) -> float | None:
    if value is None or not math.isfinite(value) or value <= 0.0:
        return None
    normalized = value
    while normalized < minimum:
        normalized *= 2.0
    while normalized > maximum:
        normalized *= 0.5
    return normalized


__all__ = ["BeatClockTracker"]
