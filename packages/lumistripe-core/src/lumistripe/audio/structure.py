"""Music structure classification from timestamped audio features."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum
from statistics import median

from .types import MusicFeatures


class MusicStructurePhase(str, Enum):
    """The current broad musical section inferred from audio features."""

    IDLE = "idle"
    BREAKDOWN = "breakdown"
    GROOVE = "groove"
    BUILDUP = "buildup"
    DROP = "drop"


@dataclass(frozen=True, slots=True)
class MusicStructureState:
    """Immutable classification returned for one tracker update.

    Confidence and build-up progress are normalized to ``[0, 1]``. The drop
    event is true only for the update that confirms the phase transition.
    """

    phase: MusicStructurePhase = MusicStructurePhase.IDLE
    confidence: float = 0.0
    buildup_progress: float = 0.0
    drop_event: bool = False


@dataclass(frozen=True, slots=True)
class _Sample:
    timestamp: float
    energy: float
    raw_energy: float
    bass: float
    onset: float


_HISTORY_SECONDS = 8.0
_HISTORY_MAX_SAMPLES = 256
_SMOOTHING_TAU_SECONDS = 0.45
_MAX_CONTIGUOUS_GAP_SECONDS = 2.0
_BUILDUP_WINDOW_SECONDS = 2.5
_BUILDUP_MIN_SPAN_SECONDS = 0.8
_BUILDUP_MIN_RISE = 0.12
_BUILDUP_MIN_SLOPE = 0.045
# Native rolling loudness spreads gradual builds across several bars and has a
# narrower scale than the legacy synthetic feature inputs.
_NATIVE_BUILDUP_WINDOW_SECONDS = 8.0
_NATIVE_BUILDUP_MIN_SPAN_SECONDS = 1.2
_NATIVE_BUILDUP_MIN_RISE = 0.008
_NATIVE_BUILDUP_MIN_SLOPE = 0.005
_NATIVE_GROOVE_BUILDUP_ARM_SECONDS = 4.0
_NATIVE_BUILDUP_CONFIRM_SECONDS = 1.2
_BUILDUP_CONFIRM_SECONDS = 0.3
_BREAKDOWN_ENERGY = 0.12
_BREAKDOWN_CONFIRM_SECONDS = 0.65
_GROOVE_ENERGY = 0.14
_GROOVE_CONFIRM_SECONDS = 0.35
_DROP_COOLDOWN_SECONDS = 4.0
_DROP_HOLD_SECONDS = 1.0
_NATIVE_DROP_HOLD_SECONDS = 2.5
# A local rolling-level jump rejects ordinary RMS kick spikes as drop evidence.
# The reference build is eight bars long; requiring half of that progression
# avoids confirming a drop during the early rising section.
_NATIVE_DROP_MIN_BUILDUP_SECONDS = 8.0
_NATIVE_DROP_ENERGY_JUMP = 0.02
_NATIVE_DROP_COMPARISON_SECONDS = 1.5
_GROOVE_REFERENCE_TAU_SECONDS = 6.0


class MusicStructureTracker:
    """Track broad musical phases from ``MusicFeatures`` and monotonic time.

    The detector uses only bounded, short-term feature history and fixed
    conservative thresholds. A drop is considered only after a confirmed
    build-up and is reported as a one-update edge in ``drop_event``.
    """

    __slots__ = (
        "_buildup_progress",
        "_buildup_start_energy",
        "_candidate_phase",
        "_candidate_since",
        "_confidence",
        "_groove_reference_energy",
        "_history",
        "_last_drop_at",
        "_last_timestamp",
        "_phase",
        "_phase_since",
        "_smoothed_energy",
        "_uses_rolling_energy",
    )

    def __init__(self) -> None:
        self._history: deque[_Sample] = deque(maxlen=_HISTORY_MAX_SAMPLES)
        self._groove_reference_energy = 0.0
        self._last_timestamp: float | None = None
        self._smoothed_energy = 0.0
        self._uses_rolling_energy = False
        self._phase = MusicStructurePhase.IDLE
        self._phase_since = 0.0
        self._candidate_phase: MusicStructurePhase | None = None
        self._candidate_since = 0.0
        self._last_drop_at: float | None = None
        self._buildup_progress = 0.0
        self._buildup_start_energy = 0.0
        self._confidence = 0.0

    def reset(self) -> None:
        """Clear all signal history and return the tracker to its initial state."""

        self._history.clear()
        self._groove_reference_energy = 0.0
        self._last_timestamp = None
        self._smoothed_energy = 0.0
        self._uses_rolling_energy = False
        self._phase = MusicStructurePhase.IDLE
        self._phase_since = 0.0
        self._candidate_phase = None
        self._candidate_since = 0.0
        self._last_drop_at = None
        self._buildup_progress = 0.0
        self._buildup_start_energy = 0.0
        self._confidence = 0.0

    def update(self, features: MusicFeatures, now_s: float) -> MusicStructureState:
        """Update the classification using a monotonic timestamp in seconds."""

        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        if self._last_timestamp is not None and now_s < self._last_timestamp:
            raise ValueError("now_s must be monotonic")

        previous_timestamp = self._last_timestamp
        elapsed = 0.0 if previous_timestamp is None else now_s - previous_timestamp
        gap_reset = elapsed > _MAX_CONTIGUOUS_GAP_SECONDS
        if gap_reset:
            self._clear_signal_state()
            elapsed = 0.0

        energy, raw_energy, bass, onset, silent = _feature_values(features)
        self._uses_rolling_energy = features.rolling_loudness > 0.003
        if silent:
            self._history.clear()
            self._groove_reference_energy = 0.0
            self._smoothed_energy = 0.0
            self._uses_rolling_energy = False
            self._phase = MusicStructurePhase.IDLE
            self._phase_since = now_s
            self._candidate_phase = None
            self._candidate_since = now_s
            self._last_drop_at = None
            self._buildup_progress = 0.0
            self._buildup_start_energy = 0.0
            self._confidence = 1.0
            self._last_timestamp = now_s
            self._history.append(_Sample(now_s, 0.0, 0.0, bass, onset))
            return self._state()

        if gap_reset or previous_timestamp is None:
            self._smoothed_energy = energy
        elif elapsed > 0.0:
            alpha = 1.0 - math.exp(-elapsed / _SMOOTHING_TAU_SECONDS)
            self._smoothed_energy += (energy - self._smoothed_energy) * alpha

        self._last_timestamp = now_s
        if self._history and self._history[-1].timestamp == now_s:
            self._history.pop()
        self._history.append(
            _Sample(now_s, self._smoothed_energy, raw_energy, bass, onset)
        )
        while self._history and now_s - self._history[0].timestamp > _HISTORY_SECONDS:
            self._history.popleft()

        if energy <= 0.008:
            self._transition(MusicStructurePhase.IDLE, now_s)
            self._buildup_progress = 0.0
            self._confidence = 0.9
            return self._state()

        if self._phase is MusicStructurePhase.GROOVE and elapsed > 0.0:
            alpha = 1.0 - math.exp(-elapsed / _GROOVE_REFERENCE_TAU_SECONDS)
            if self._groove_reference_energy <= 0.0:
                self._groove_reference_energy = self._smoothed_energy
            elif self._smoothed_energy > self._groove_reference_energy:
                self._groove_reference_energy += (
                    self._smoothed_energy - self._groove_reference_energy
                ) * alpha

        drop_confirmed = self._drop_confirmed(features, now_s)
        if drop_confirmed:
            self._transition(MusicStructurePhase.DROP, now_s)
            self._last_drop_at = now_s
            self._buildup_progress = 1.0
            self._confidence = 1.0
            return self._state(drop_event=True)

        if (
            self._phase is MusicStructurePhase.DROP
            and now_s - self._phase_since
            < (
                _NATIVE_DROP_HOLD_SECONDS
                if self._uses_rolling_energy
                else _DROP_HOLD_SECONDS
            )
        ):
            self._confidence = max(0.75, self._confidence * 0.96)
            return self._state()

        desired, rise, slope = self._desired_phase(now_s)
        confirm_seconds = {
            MusicStructurePhase.IDLE: 0.0,
            MusicStructurePhase.BREAKDOWN: _BREAKDOWN_CONFIRM_SECONDS,
            MusicStructurePhase.GROOVE: _GROOVE_CONFIRM_SECONDS,
            MusicStructurePhase.BUILDUP: (
                _NATIVE_BUILDUP_CONFIRM_SECONDS
                if self._uses_rolling_energy
                else _BUILDUP_CONFIRM_SECONDS
            ),
            MusicStructurePhase.DROP: 0.0,
        }[desired]
        changed = self._update_candidate(desired, now_s, confirm_seconds)
        if changed:
            self._transition(desired, now_s)

        if self._phase is MusicStructurePhase.BUILDUP:
            current_progress = _clamp01(
                (self._smoothed_energy - self._buildup_start_energy) / 0.45
            )
            self._buildup_progress = max(self._buildup_progress, current_progress)
            self._confidence = _clamp01(
                0.55 + min(rise / 0.5, 0.25) + min(slope / 0.4, 0.2)
            )
        elif self._phase is MusicStructurePhase.BREAKDOWN:
            duration = max(0.0, now_s - self._phase_since)
            low_energy_evidence = max(0.0, _BREAKDOWN_ENERGY - self._smoothed_energy)
            self._confidence = _clamp01(
                0.58 + min(duration / 3.0, 0.25) + low_energy_evidence
            )
            self._buildup_progress = 0.0
        elif self._phase is MusicStructurePhase.GROOVE:
            duration = max(0.0, now_s - self._phase_since)
            self._confidence = _clamp01(0.62 + min(duration / 4.0, 0.25))
            self._buildup_progress = 0.0
        elif self._phase is MusicStructurePhase.IDLE:
            self._confidence = 0.65
            self._buildup_progress = 0.0
        else:
            self._confidence = 0.8
            self._buildup_progress = 0.0

        return self._state()

    def _clear_signal_state(self) -> None:
        self._history.clear()
        self._groove_reference_energy = 0.0
        self._smoothed_energy = 0.0
        self._uses_rolling_energy = False
        self._phase = MusicStructurePhase.IDLE
        self._phase_since = self._last_timestamp or 0.0
        self._candidate_phase = None
        self._candidate_since = self._phase_since
        self._last_drop_at = None
        self._buildup_progress = 0.0
        self._buildup_start_energy = 0.0
        self._confidence = 0.0

    def _drop_confirmed(self, features: MusicFeatures, now_s: float) -> bool:
        if self._phase is not MusicStructurePhase.BUILDUP:
            return False
        if (
            self._uses_rolling_energy
            and now_s - self._phase_since < _NATIVE_DROP_MIN_BUILDUP_SECONDS
        ):
            return False
        if (
            self._last_drop_at is not None
            and now_s - self._last_drop_at < _DROP_COOLDOWN_SECONDS
        ):
            return False

        if self._uses_rolling_energy:
            recent_level = [
                sample
                for sample in self._history
                if now_s - sample.timestamp <= _NATIVE_DROP_COMPARISON_SECONDS
            ]
            if len(recent_level) < 2:
                return False
            baseline_level = median(sample.energy for sample in recent_level[:-1])
            return (
                recent_level[-1].energy - baseline_level >= _NATIVE_DROP_ENERGY_JUMP
            )

        recent = [sample for sample in self._history if now_s - sample.timestamp <= 0.8]
        if len(recent) < 2:
            return False
        baseline_energy = median(sample.raw_energy for sample in recent[:-1])
        baseline_bass = median(sample.bass for sample in recent[:-1])
        current = recent[-1]
        energy_jump = current.raw_energy - baseline_energy
        bass_jump = current.bass - baseline_bass
        explicit_evidence = (
            features.drop_detected
            and current.raw_energy >= 0.24
            and current.bass >= 0.35
            and current.onset >= 0.08
        )
        transient_evidence = (
            current.onset >= 0.16
            and current.raw_energy >= 0.3
            and (energy_jump >= 0.1 or current.bass >= 0.45 and bass_jump >= 0.1)
        )
        section_evidence = (
            features.section_change
            and current.onset >= 0.16
            and current.raw_energy >= 0.3
            and (energy_jump >= 0.12 or bass_jump >= 0.12)
        )
        return explicit_evidence or transient_evidence or section_evidence

    def _desired_phase(self, now_s: float) -> tuple[MusicStructurePhase, float, float]:
        window_seconds = (
            _NATIVE_BUILDUP_WINDOW_SECONDS
            if self._uses_rolling_energy
            else _BUILDUP_WINDOW_SECONDS
        )
        recent = [
            sample
            for sample in self._history
            if now_s - sample.timestamp <= window_seconds
            and (
                not self._uses_rolling_energy
                or sample.timestamp >= self._phase_since
            )
        ]
        rise = 0.0
        slope = 0.0
        if (
            len(recent) >= 3
            and recent[-1].timestamp - recent[0].timestamp
            >= (
                _NATIVE_BUILDUP_MIN_SPAN_SECONDS
                if self._uses_rolling_energy
                else _BUILDUP_MIN_SPAN_SECONDS
            )
        ):
            span = recent[-1].timestamp - recent[0].timestamp
            earlier_end = recent[0].timestamp + span * 0.3
            later_start = recent[0].timestamp + span * 0.7
            earlier = [sample for sample in recent if sample.timestamp <= earlier_end]
            later = [sample for sample in recent if sample.timestamp >= later_start]
            if earlier and later:
                baseline = median(sample.energy for sample in earlier)
                later_energy = median(sample.energy for sample in later)
                later_at = median(sample.timestamp for sample in later)
                earlier_at = median(sample.timestamp for sample in earlier)
                duration = max(0.001, later_at - earlier_at)
                rise = max(0.0, later_energy - baseline)
                slope = rise / duration

        breakdown_threshold = _BREAKDOWN_ENERGY
        if self._uses_rolling_energy and self._groove_reference_energy > 0.0:
            breakdown_threshold = max(
                breakdown_threshold,
                min(0.16, self._groove_reference_energy * 0.60),
            )
        if (
            self._uses_rolling_energy
            and self._phase is MusicStructurePhase.BUILDUP
            and self._smoothed_energy > 0.06
        ):
            return MusicStructurePhase.BUILDUP, rise, slope
        if (
            self._uses_rolling_energy
            and self._smoothed_energy > 0.06
            and rise >= _NATIVE_BUILDUP_MIN_RISE
            and slope >= _NATIVE_BUILDUP_MIN_SLOPE
            and (
                self._phase is MusicStructurePhase.BREAKDOWN
                or self._phase is MusicStructurePhase.GROOVE
                and now_s - self._phase_since
                >= _NATIVE_GROOVE_BUILDUP_ARM_SECONDS
            )
        ):
            return MusicStructurePhase.BUILDUP, rise, slope
        if self._smoothed_energy < breakdown_threshold:
            return MusicStructurePhase.BREAKDOWN, rise, slope
        if (
            not self._uses_rolling_energy
            and self._smoothed_energy >= _GROOVE_ENERGY
            and rise >= _BUILDUP_MIN_RISE
            and slope >= _BUILDUP_MIN_SLOPE
        ):
            return MusicStructurePhase.BUILDUP, rise, slope
        return MusicStructurePhase.GROOVE, rise, slope

    def _update_candidate(
        self,
        desired: MusicStructurePhase,
        now_s: float,
        confirm_seconds: float,
    ) -> bool:
        if desired is self._phase:
            self._candidate_phase = None
            return False
        if desired is not self._candidate_phase:
            self._candidate_phase = desired
            self._candidate_since = now_s
            return confirm_seconds == 0.0
        if now_s - self._candidate_since >= confirm_seconds:
            self._candidate_phase = None
            return True
        return False

    def _transition(self, phase: MusicStructurePhase, now_s: float) -> None:
        if phase is self._phase:
            return
        self._phase = phase
        self._phase_since = now_s
        self._candidate_phase = None
        self._candidate_since = now_s
        if phase is MusicStructurePhase.BUILDUP:
            self._buildup_start_energy = self._buildup_reference_energy(now_s)
        else:
            self._buildup_start_energy = 0.0

    def _buildup_reference_energy(self, now_s: float) -> float:
        window_seconds = (
            _NATIVE_BUILDUP_WINDOW_SECONDS
            if self._uses_rolling_energy
            else _BUILDUP_WINDOW_SECONDS
        )
        recent = [
            sample
            for sample in self._history
            if now_s - sample.timestamp <= window_seconds
        ]
        if len(recent) < 3:
            return self._smoothed_energy
        span = recent[-1].timestamp - recent[0].timestamp
        early_end = recent[0].timestamp + span * 0.3
        early = [sample.energy for sample in recent if sample.timestamp <= early_end]
        return median(early) if early else self._smoothed_energy

    def _state(self, *, drop_event: bool = False) -> MusicStructureState:
        return MusicStructureState(
            phase=self._phase,
            confidence=_clamp01(self._confidence),
            buildup_progress=_clamp01(self._buildup_progress),
            drop_event=drop_event,
        )


def _feature_values(features: MusicFeatures) -> tuple[float, float, float, float, bool]:
    instantaneous_energy = max(
        _finite_clamp(features.energy),
        _finite_clamp(features.volume),
        _finite_clamp(features.energy_level),
    )
    rolling_energy = _finite_clamp(features.rolling_loudness)
    energy = (
        max(instantaneous_energy, rolling_energy)
        if rolling_energy <= 0.003
        else rolling_energy
    )
    bass = max(_finite_clamp(features.bass), _finite_clamp(features.bass_energy))
    onset = max(
        _finite_clamp(features.onset_strength),
        _finite_clamp(features.spectral_flux),
        _finite_clamp(features.beat_strength) if features.beat else 0.0,
    )
    silent = features.silence or energy <= 0.003
    return (0.0 if silent else energy), instantaneous_energy, bass, onset, silent


def _finite_clamp(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return _clamp01(value)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


__all__ = ["MusicStructurePhase", "MusicStructureState", "MusicStructureTracker"]
