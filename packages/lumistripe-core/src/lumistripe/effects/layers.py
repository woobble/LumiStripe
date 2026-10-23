from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from random import Random
from typing import TYPE_CHECKING

import numpy as np

from ..audio.structure import MusicStructurePhase, MusicStructureState
from ..audio.types import AudioFrame, AudioSnapshot, BeatClockState, BeatEvent
from ..color import Rgba
from ..controller import BrightnessController, Controller
from ..stripe import Stripe
from .base import Effect

if TYPE_CHECKING:
    from ..animation.base import AnimationPlayer


class EffectCategory(str, Enum):
    RHYTHMIC = "rhythmic"
    ACCENT = "accent"


class BlendMode(str, Enum):
    SCREEN = "screen"


class EffectTriggerResult(str, Enum):
    NOT_EVALUATED = "not-evaluated"
    BELOW_THRESHOLD = "below-threshold"
    ACTIVATED = "activated"
    COOLDOWN = "cooldown"
    CATEGORY_ACTIVE = "category-active"
    CAPACITY = "capacity"
    TRANSITION = "transition"
    NO_CANDIDATE = "no-candidate"


@dataclass(frozen=True, slots=True)
class EffectDefinition:
    name: str
    category: EffectCategory
    duration_s: float
    strength: float
    preferred_band: str = "any"
    drop_only: bool = False
    blend_mode: BlendMode = BlendMode.SCREEN

    def __post_init__(self) -> None:
        if self.duration_s <= 0.0:
            raise ValueError("effect duration must be greater than zero")
        if not 0.0 < self.strength <= 1.0:
            raise ValueError("effect strength must be between zero and one")
        if self.preferred_band not in {"any", "bass", "mid", "treble"}:
            raise ValueError("unsupported preferred effect band")


RHYTHMIC_EFFECTS: tuple[EffectDefinition, ...] = (
    EffectDefinition("beat_wave", EffectCategory.RHYTHMIC, 0.8, 0.38, "bass"),
    EffectDefinition("beat_explosion", EffectCategory.RHYTHMIC, 0.65, 0.42, "bass"),
    EffectDefinition("beat_tunnel", EffectCategory.RHYTHMIC, 0.8, 0.36, "mid"),
    EffectDefinition("beat_ripple", EffectCategory.RHYTHMIC, 0.75, 0.4, "bass"),
    EffectDefinition("hard_beat", EffectCategory.RHYTHMIC, 0.45, 0.32, "bass"),
    EffectDefinition("center_burst", EffectCategory.RHYTHMIC, 0.65, 0.4, "mid"),
    EffectDefinition("mirror_flash", EffectCategory.RHYTHMIC, 0.55, 0.34, "mid"),
    EffectDefinition("spectrum_flash", EffectCategory.RHYTHMIC, 0.55, 0.3, "treble"),
)

ACCENT_EFFECTS: tuple[EffectDefinition, ...] = (
    EffectDefinition("confetti", EffectCategory.ACCENT, 1.4, 0.42, "treble"),
    EffectDefinition("shockwave", EffectCategory.ACCENT, 1.25, 0.62, "bass", True),
    EffectDefinition("firework_burst", EffectCategory.ACCENT, 1.6, 0.52, "treble"),
    EffectDefinition("lightning_strike", EffectCategory.ACCENT, 0.7, 0.5, "treble"),
    EffectDefinition("drop_explosion", EffectCategory.ACCENT, 1.2, 0.68, "bass", True),
    EffectDefinition("bass_drop", EffectCategory.ACCENT, 1.1, 0.62, "bass", True),
    EffectDefinition("pixel_explosion", EffectCategory.ACCENT, 1.5, 0.48, "mid"),
    EffectDefinition("electric_storm", EffectCategory.ACCENT, 1.25, 0.42, "treble"),
    EffectDefinition("club_flash", EffectCategory.ACCENT, 0.8, 0.38, "mid"),
    EffectDefinition("color_burst", EffectCategory.ACCENT, 1.3, 0.46, "mid"),
    EffectDefinition("drop_wave", EffectCategory.ACCENT, 1.3, 0.58, "bass", True),
)


@dataclass(frozen=True, slots=True)
class EffectSchedulerConfig:
    beat_confidence_threshold: float = 0.55
    onset_threshold: float = 0.65
    rhythmic_cooldown_s: float = 0.2
    accent_cooldown_s: float = 1.25
    max_active_effects: int = 2
    max_overlay_strength: float = 0.75
    subdivisions_per_beat: int = 1
    clock_confidence_threshold: float = 0.65
    seed: int | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.beat_confidence_threshold <= 1.0:
            raise ValueError("beat_confidence_threshold must be between zero and one")
        if not 0.0 <= self.onset_threshold <= 1.0:
            raise ValueError("onset_threshold must be between zero and one")
        if self.rhythmic_cooldown_s < 0.0 or self.accent_cooldown_s < 0.0:
            raise ValueError("effect cooldowns must not be negative")
        if self.max_active_effects not in (1, 2):
            raise ValueError("max_active_effects must be one or two")
        if not 0.0 < self.max_overlay_strength <= 1.0:
            raise ValueError("max_overlay_strength must be between zero and one")
        if self.subdivisions_per_beat not in (1, 2, 4):
            raise ValueError("subdivisions_per_beat must be 1, 2, or 4")
        if not 0.0 <= self.clock_confidence_threshold <= 1.0:
            raise ValueError("clock_confidence_threshold must be between zero and one")


@dataclass(slots=True)
class ActiveEffect:
    definition: EffectDefinition
    animation: Effect
    started_at_s: float
    trigger_frame: AudioFrame
    intensity: float = 1.0
    frame: int = 0


@dataclass(frozen=True, slots=True)
class EffectLayerStatus:
    name: str
    category: EffectCategory
    blend_mode: BlendMode
    strength: float
    elapsed_s: float
    remaining_s: float
    progress: float


@dataclass(frozen=True, slots=True)
class EffectTriggerStatus:
    category: EffectCategory
    signal: str
    value: float
    threshold: float
    result: EffectTriggerResult


@dataclass(frozen=True, slots=True)
class EffectSchedulerDiagnostics:
    active: tuple[EffectLayerStatus, ...]
    active_limit: int
    overlay_strength: float
    overlay_limit: float
    rhythmic_cooldown_remaining_s: float
    accent_cooldown_remaining_s: float
    rhythmic: EffectTriggerStatus
    accent: EffectTriggerStatus
    late_ticks_dropped: int
    late_ticks_coalesced: int
    beat_lateness_p95_ms: float


@dataclass(frozen=True, slots=True)
class _ScheduledBeat:
    due_at_s: float
    strength: float
    event: BeatEvent | None
    grid_index: int | None = None


@dataclass(frozen=True, slots=True)
class _StructureModifiers:
    output_scale: float = 1.0
    effect_scale: float = 1.0
    threshold_scale: float = 1.0


def _structure_modifiers(
    state: MusicStructureState | None,
) -> _StructureModifiers:
    if (
        state is None
        or state.confidence < 0.6
        or state.phase in (MusicStructurePhase.IDLE, MusicStructurePhase.DROP)
    ):
        return _StructureModifiers()
    if state.phase is MusicStructurePhase.BREAKDOWN:
        return _StructureModifiers(0.65, 0.65, 1.25)
    if state.phase is MusicStructurePhase.BUILDUP:
        progress = min(1.0, max(0.0, state.buildup_progress))
        scale = 0.65 + 0.35 * progress
        return _StructureModifiers(scale, scale, 1.0 - 0.2 * progress)
    return _StructureModifiers()


@dataclass(slots=True)
class EffectScheduler:
    config: EffectSchedulerConfig = field(default_factory=EffectSchedulerConfig)
    active: list[ActiveEffect] = field(default_factory=list)
    _last_rhythmic_at_s: float = -9999.0
    _last_accent_at_s: float = -9999.0
    _recent: list[str] = field(default_factory=list)
    _rng: Random = field(init=False)
    _last_update_at_s: float = 0.0
    _rhythmic_status: EffectTriggerStatus = field(init=False)
    _accent_status: EffectTriggerStatus = field(init=False)
    _response: float = field(default=0.65, init=False)
    _last_beat_event_sequence: int = field(default=0, init=False)
    _pending_beats: deque[_ScheduledBeat] = field(
        default_factory=deque, init=False
    )
    _pending_drop_accent_at_s: float | None = field(default=None, init=False)
    _last_clock_subdivision_scheduled: int | None = field(default=None, init=False)
    _recent_grid_indices: deque[int] = field(default_factory=deque, init=False)
    _recent_grid_index_set: set[int] = field(default_factory=set, init=False)
    _sync_offset_s: float = field(default=0.0, init=False)
    _late_ticks_dropped: int = field(default=0, init=False)
    _late_ticks_coalesced: int = field(default=0, init=False)
    _beat_lateness_p95_ms: float = field(default=0.0, init=False)
    _beat_lateness_samples_ms: deque[float] = field(
        default_factory=lambda: deque(maxlen=256), init=False
    )

    def __post_init__(self) -> None:
        self._rng = Random(self.config.seed)
        self._reset_trigger_statuses()

    @property
    def active_names(self) -> tuple[str, ...]:
        return tuple(effect.definition.name for effect in self.active)

    @property
    def impact_threshold(self) -> float:
        return 0.85 - self._response * 0.25

    @property
    def quiet_floor(self) -> float:
        return 0.65 - self._response * 0.4

    def set_response(self, response: float) -> None:
        if not 0.0 <= response <= 1.0:
            raise ValueError("dynamic response must be between zero and one")
        self._response = response

    def reset(self) -> None:
        self.active.clear()
        self._last_rhythmic_at_s = -9999.0
        self._last_accent_at_s = -9999.0
        self._recent.clear()
        self._last_update_at_s = 0.0
        self._last_beat_event_sequence = 0
        self._pending_beats.clear()
        self._pending_drop_accent_at_s = None
        self._last_clock_subdivision_scheduled = None
        self._recent_grid_indices.clear()
        self._recent_grid_index_set.clear()
        self._sync_offset_s = 0.0
        self._late_ticks_dropped = 0
        self._late_ticks_coalesced = 0
        self._beat_lateness_p95_ms = 0.0
        self._beat_lateness_samples_ms.clear()
        self._reset_trigger_statuses()

    def clear_active(self) -> None:
        self.active.clear()

    def diagnostics(self, *, now_s: float | None = None) -> EffectSchedulerDiagnostics:
        now = self._last_update_at_s if now_s is None else now_s
        layers = tuple(
            EffectLayerStatus(
                name=effect.definition.name,
                category=effect.definition.category,
                blend_mode=effect.definition.blend_mode,
                strength=effect.definition.strength * effect.intensity,
                elapsed_s=max(0.0, now - effect.started_at_s),
                remaining_s=max(
                    0.0,
                    effect.definition.duration_s - max(0.0, now - effect.started_at_s),
                ),
                progress=min(
                    1.0,
                    max(0.0, now - effect.started_at_s) / effect.definition.duration_s,
                ),
            )
            for effect in self.active
        )
        return EffectSchedulerDiagnostics(
            active=layers,
            active_limit=self.config.max_active_effects,
            overlay_strength=min(
                sum(effect.strength for effect in layers),
                self.config.max_overlay_strength,
            ),
            overlay_limit=self.config.max_overlay_strength,
            rhythmic_cooldown_remaining_s=self._cooldown_remaining(
                now, self._last_rhythmic_at_s, self.config.rhythmic_cooldown_s
            ),
            accent_cooldown_remaining_s=self._cooldown_remaining(
                now, self._last_accent_at_s, self.config.accent_cooldown_s
            ),
            rhythmic=self._rhythmic_status,
            accent=self._accent_status,
            late_ticks_dropped=self._late_ticks_dropped,
            late_ticks_coalesced=self._late_ticks_coalesced,
            beat_lateness_p95_ms=self._beat_lateness_p95_ms,
        )

    def update(
        self,
        player: AnimationPlayer,
        snapshot: AudioSnapshot,
        *,
        now_s: float,
        allow_triggers: bool = True,
        sync_offset_s: float = 0.0,
        structure_state: MusicStructureState | None = None,
    ) -> None:
        self._last_update_at_s = now_s
        if sync_offset_s != self._sync_offset_s:
            offset_delta = sync_offset_s - self._sync_offset_s
            self._pending_beats = deque(
                replace(beat, due_at_s=beat.due_at_s + offset_delta)
                for beat in self._pending_beats
            )
            if self._pending_drop_accent_at_s is not None:
                self._pending_drop_accent_at_s += offset_delta
            self._sync_offset_s = sync_offset_s
        self.active = [
            effect
            for effect in self.active
            if now_s - effect.started_at_s < effect.definition.duration_s
        ]
        features = snapshot.features
        modifiers = _structure_modifiers(structure_state)
        impact_threshold = self.impact_threshold * modifiers.threshold_scale
        onset_threshold = self.config.onset_threshold * modifiers.threshold_scale
        beat_strength = max(
            snapshot.frame.beat_strength,
            features.beat_strength,
            features.beat_confidence,
        )
        new_events = tuple(
            event
            for event in snapshot.beat_events
            if event.sequence > self._last_beat_event_sequence
        )
        clock = snapshot.beat_clock
        for event in new_events:
            self._last_beat_event_sequence = max(
                self._last_beat_event_sequence, event.sequence
            )
            due_at = event.timestamp + sync_offset_s
            grid_index = self._event_grid_index(event, clock)
            if grid_index is None:
                self._enqueue_beat(due_at, event.strength, event)
                continue
            if grid_index in self._recent_grid_index_set:
                pending_prediction = next(
                    (
                        index
                        for index, pending in enumerate(self._pending_beats)
                        if pending.grid_index == grid_index and pending.event is None
                    ),
                    None,
                )
                if pending_prediction is not None:
                    pending = list(self._pending_beats)
                    pending[pending_prediction] = _ScheduledBeat(
                        due_at, event.strength, event, grid_index
                    )
                    self._pending_beats = deque(
                        sorted(pending, key=lambda item: item.due_at_s)
                    )
            else:
                self._remember_grid_index(grid_index)
                self._enqueue_beat(due_at, event.strength, event, grid_index)

        predicted_beat = False
        if (
            clock is not None
            and clock.bpm > 0.0
            and clock.last_beat_at is not None
            and clock.confidence >= self.config.clock_confidence_threshold
        ):
            division = self.config.subdivisions_per_beat
            position = min(division - 1, int(clock.phase * division))
            subdivision = clock.beat_index * division + position
            next_subdivision = subdivision + 1
            first_subdivision = (
                next_subdivision
                if self._last_clock_subdivision_scheduled is None
                else self._last_clock_subdivision_scheduled + 1
            )
            if next_subdivision - first_subdivision >= 64:
                first_subdivision = next_subdivision - 63
            for grid_index in range(first_subdivision, next_subdivision + 1):
                if grid_index in self._recent_grid_index_set:
                    continue
                target_at = self._clock_grid_time(clock, grid_index, division)
                self._remember_grid_index(grid_index)
                self._enqueue_beat(
                    target_at + sync_offset_s,
                    0.8,
                    None,
                    grid_index,
                )
            self._last_clock_subdivision_scheduled = max(
                next_subdivision,
                self._last_clock_subdivision_scheduled
                if self._last_clock_subdivision_scheduled is not None
                else next_subdivision,
            )

        lateness_limit_s = self._maximum_beat_lateness_s(clock)
        if structure_state is not None and structure_state.drop_event:
            self._schedule_drop_accent(
                structure_state,
                clock,
                now_s=now_s,
                sync_offset_s=sync_offset_s,
            )
        drop_accent_due = self._drop_accent_due(now_s, lateness_limit_s)
        due_beats: list[_ScheduledBeat] = []
        while self._pending_beats and self._pending_beats[0].due_at_s <= now_s:
            due_beats.append(self._pending_beats.popleft())
        fresh_beats = [
            due
            for due in due_beats
            if now_s - due.due_at_s <= lateness_limit_s + 1e-9
        ]
        self._late_ticks_dropped += len(due_beats) - len(fresh_beats)
        if len(fresh_beats) > 1:
            self._late_ticks_coalesced += len(fresh_beats) - 1
        due = fresh_beats[-1] if fresh_beats else None
        due_event = due.event if due is not None else None
        due_strength = due.strength if due is not None else 0.0
        due_found = due is not None
        if due is not None:
            self._beat_lateness_samples_ms.append(
                max(0.0, now_s - due.due_at_s) * 1000.0
            )
            self._beat_lateness_p95_ms = self._percentile(
                self._beat_lateness_samples_ms, 0.95
            )
        predicted_beat = due_found and due_event is None
        event_due = due_found
        legacy_beat = (
            not snapshot.beat_event_stream
            and not snapshot.beat_events
            and (snapshot.frame.beat or features.beat)
        )
        beat = event_due or legacy_beat
        beat_strength = max(beat_strength, due_strength)
        impact = snapshot.musical_impact
        trigger_snapshot = snapshot
        if event_due:
            trigger_snapshot = replace(
                snapshot,
                frame=replace(
                    snapshot.frame,
                    beat=True,
                    beat_strength=max(snapshot.frame.beat_strength, due_strength),
                ),
            )
        rhythmic_result = self._trigger_result(
            category=EffectCategory.RHYTHMIC,
            triggered=(
                beat
                and beat_strength >= self.config.beat_confidence_threshold
                and (
                    impact >= impact_threshold
                    or (
                        predicted_beat
                        and clock is not None
                        and clock.confidence >= self.config.clock_confidence_threshold
                    )
                )
            ),
            now_s=now_s,
            last_trigger_at_s=self._last_rhythmic_at_s,
            cooldown_s=self.config.rhythmic_cooldown_s,
            allow_triggers=allow_triggers,
        )
        if rhythmic_result is EffectTriggerResult.ACTIVATED:
            activated = self._activate(
                player, RHYTHMIC_EFFECTS, trigger_snapshot, now_s
            )
            rhythmic_result = (
                EffectTriggerResult.ACTIVATED
                if activated
                else EffectTriggerResult.NO_CANDIDATE
            )
            self._last_rhythmic_at_s = now_s
        self._rhythmic_status = EffectTriggerStatus(
            EffectCategory.RHYTHMIC,
            "beat",
            beat_strength,
            self.config.beat_confidence_threshold,
            rhythmic_result,
        )

        if drop_accent_due:
            accent_result = self._trigger_result(
                category=EffectCategory.ACCENT,
                triggered=True,
                now_s=now_s,
                last_trigger_at_s=self._last_accent_at_s,
                cooldown_s=self.config.accent_cooldown_s,
                allow_triggers=allow_triggers,
            )
            if accent_result is EffectTriggerResult.ACTIVATED:
                activated = self._activate_drop_accent(player, snapshot, now_s)
                accent_result = (
                    EffectTriggerResult.ACTIVATED
                    if activated
                    else EffectTriggerResult.NO_CANDIDATE
                )
                self._last_accent_at_s = now_s
            self._accent_status = EffectTriggerStatus(
                EffectCategory.ACCENT,
                "drop",
                1.0,
                1.0,
                accent_result,
            )
        else:
            drop_in_progress = structure_state is not None and (
                structure_state.phase is MusicStructurePhase.DROP
                or self._pending_drop_accent_at_s is not None
            )
            accent_trigger = (
                not drop_in_progress
                and impact >= impact_threshold
                and features.onset_strength >= onset_threshold
                and (structure_state is None or not features.drop_detected)
            )
            accent_result = self._trigger_result(
                category=EffectCategory.ACCENT,
                triggered=accent_trigger,
                now_s=now_s,
                last_trigger_at_s=self._last_accent_at_s,
                cooldown_s=self.config.accent_cooldown_s,
                allow_triggers=allow_triggers,
            )
            if accent_result is EffectTriggerResult.ACTIVATED:
                candidates = tuple(
                    definition
                    for definition in ACCENT_EFFECTS
                    if not definition.drop_only
                )
                activated = self._activate(player, candidates, snapshot, now_s)
                accent_result = (
                    EffectTriggerResult.ACTIVATED
                    if activated
                    else EffectTriggerResult.NO_CANDIDATE
                )
                self._last_accent_at_s = now_s
            self._accent_status = EffectTriggerStatus(
                EffectCategory.ACCENT,
                "onset",
                features.onset_strength,
                onset_threshold,
                accent_result,
            )

    def _schedule_drop_accent(
        self,
        state: MusicStructureState,
        clock: BeatClockState | None,
        *,
        now_s: float,
        sync_offset_s: float,
    ) -> None:
        if self._pending_drop_accent_at_s is not None:
            return
        if (
            state.confidence < 0.6
            or clock is None
            or clock.confidence < self.config.clock_confidence_threshold
            or clock.bpm <= 0.0
            or clock.next_beat_at is None
        ):
            return
        period = 60.0 / clock.bpm
        due_at = clock.next_beat_at + sync_offset_s
        while due_at <= now_s:
            due_at += period
        self._pending_drop_accent_at_s = due_at

    def _drop_accent_due(self, now_s: float, lateness_limit_s: float) -> bool:
        due_at = self._pending_drop_accent_at_s
        if due_at is None or due_at > now_s:
            return False
        self._pending_drop_accent_at_s = None
        if now_s - due_at > lateness_limit_s + 1e-9:
            self._late_ticks_dropped += 1
            return False
        return True

    def _activate_drop_accent(
        self,
        player: AnimationPlayer,
        snapshot: AudioSnapshot,
        now_s: float,
    ) -> bool:
        available = [
            definition
            for definition in ACCENT_EFFECTS
            if definition.drop_only and player.index_of(definition.name) is not None
        ]
        if not available:
            return False
        strongest = max(available, key=lambda item: item.strength)
        features = replace(snapshot.features, drop_detected=True)
        frame = replace(
            snapshot.frame,
            beat=True,
            beat_strength=max(snapshot.frame.beat_strength, 0.9),
        )
        drop_snapshot = replace(snapshot, features=features, frame=frame)
        return self._activate(
            player,
            (strongest,),
            drop_snapshot,
            now_s,
            intensity_override=1.0,
        )

    def _maximum_beat_lateness_s(self, clock: BeatClockState | None) -> float:
        maximum_s = 0.05
        if clock is None or clock.bpm <= 0.0:
            return maximum_s
        subdivision_s = 60.0 / clock.bpm / self.config.subdivisions_per_beat
        return min(maximum_s, subdivision_s / 2.0)

    @staticmethod
    def _percentile(samples: deque[float], percentile: float) -> float:
        if not samples:
            return 0.0
        ordered = sorted(samples)
        index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
        return ordered[index]

    def _enqueue_beat(
        self,
        due_at: float,
        strength: float,
        event: BeatEvent | None,
        grid_index: int | None = None,
    ) -> None:
        if len(self._pending_beats) >= 64:
            self._pending_beats.popleft()
        items = [
            *self._pending_beats,
            _ScheduledBeat(due_at, strength, event, grid_index),
        ]
        items.sort(key=lambda item: item.due_at_s)
        self._pending_beats = deque(items)

    def _remember_grid_index(self, grid_index: int) -> None:
        if grid_index in self._recent_grid_index_set:
            return
        if len(self._recent_grid_indices) >= 256:
            expired = self._recent_grid_indices.popleft()
            self._recent_grid_index_set.discard(expired)
        self._recent_grid_indices.append(grid_index)
        self._recent_grid_index_set.add(grid_index)

    def _event_grid_index(
        self, event: BeatEvent, clock: BeatClockState | None
    ) -> int | None:
        if (
            clock is None
            or clock.confidence < self.config.clock_confidence_threshold
            or clock.last_beat_at is None
            or clock.bpm <= 0.0
        ):
            return None
        division = self.config.subdivisions_per_beat
        subdivision_period = 60.0 / clock.bpm / division
        position = (event.timestamp - clock.last_beat_at) / subdivision_period
        grid_position = round(position)
        if abs(position - grid_position) > 0.35:
            return None
        return clock.beat_index * division + grid_position

    @staticmethod
    def _clock_grid_time(
        clock: BeatClockState,
        grid_index: int,
        division: int,
    ) -> float:
        assert clock.last_beat_at is not None
        period = 60.0 / clock.bpm
        beat_delta, subdivision = divmod(grid_index, division)
        return (
            clock.last_beat_at
            + (beat_delta - clock.beat_index) * period
            + subdivision * period / division
        )

    def _trigger_result(
        self,
        *,
        category: EffectCategory,
        triggered: bool,
        now_s: float,
        last_trigger_at_s: float,
        cooldown_s: float,
        allow_triggers: bool,
    ) -> EffectTriggerResult:
        if not allow_triggers:
            return EffectTriggerResult.TRANSITION
        if not triggered:
            return EffectTriggerResult.BELOW_THRESHOLD
        if self._has_category(category):
            return EffectTriggerResult.CATEGORY_ACTIVE
        if len(self.active) >= self.config.max_active_effects:
            return EffectTriggerResult.CAPACITY
        if now_s - last_trigger_at_s < cooldown_s:
            return EffectTriggerResult.COOLDOWN
        return EffectTriggerResult.ACTIVATED

    def _has_category(self, category: EffectCategory) -> bool:
        return any(effect.definition.category is category for effect in self.active)

    def _activate(
        self,
        player: AnimationPlayer,
        definitions: tuple[EffectDefinition, ...],
        snapshot: AudioSnapshot,
        now_s: float,
        *,
        intensity_override: float | None = None,
    ) -> bool:
        available = [
            item for item in definitions if player.index_of(item.name) is not None
        ]
        if not available:
            return False
        definition = max(available, key=lambda item: self._score(item, snapshot))
        animation = player.fresh_animation(definition.name)
        if not isinstance(animation, Effect):
            return False
        trigger_strength = max(
            snapshot.frame.beat_strength,
            snapshot.features.beat_strength,
            snapshot.features.onset_strength,
        )
        if snapshot.features.drop_detected:
            trigger_strength = max(trigger_strength, 0.9)
        intensity = (
            min(1.0, snapshot.musical_impact * trigger_strength)
            if intensity_override is None
            else min(1.0, max(0.0, intensity_override))
        )
        trigger = replace(
            snapshot.frame, beat=True, beat_strength=min(trigger_strength, 1.0)
        )
        self.active.append(
            ActiveEffect(definition, animation, now_s, trigger, intensity=intensity)
        )
        self._recent = (self._recent + [definition.name])[-4:]
        return True

    def _reset_trigger_statuses(self) -> None:
        self._rhythmic_status = EffectTriggerStatus(
            EffectCategory.RHYTHMIC,
            "beat",
            0.0,
            self.config.beat_confidence_threshold,
            EffectTriggerResult.NOT_EVALUATED,
        )
        self._accent_status = EffectTriggerStatus(
            EffectCategory.ACCENT,
            "onset",
            0.0,
            self.config.onset_threshold,
            EffectTriggerResult.NOT_EVALUATED,
        )

    @staticmethod
    def _cooldown_remaining(now_s: float, last_at_s: float, duration_s: float) -> float:
        return max(0.0, duration_s - (now_s - last_at_s))

    def _score(self, definition: EffectDefinition, snapshot: AudioSnapshot) -> float:
        preferred = {
            "bass": snapshot.low,
            "mid": snapshot.mid,
            "treble": snapshot.high,
            "any": snapshot.activity,
        }[definition.preferred_band]
        recent_penalty = 0.4 if definition.name in self._recent else 0.0
        return preferred + self._rng.uniform(0.0, 0.3) - recent_penalty


@dataclass(slots=True)
class LayeredRenderer:
    scheduler: EffectScheduler = field(default_factory=EffectScheduler)
    _base_buffer: Stripe | None = None
    _effect_buffers: list[Stripe] = field(default_factory=list)

    @property
    def active_effect_names(self) -> tuple[str, ...]:
        return self.scheduler.active_names

    def diagnostics(self, *, now_s: float | None = None) -> EffectSchedulerDiagnostics:
        return self.scheduler.diagnostics(now_s=now_s)

    def reset(self) -> None:
        self.scheduler.reset()
        self._base_buffer = None
        self._effect_buffers.clear()

    def render(
        self,
        player: AnimationPlayer,
        controller: Controller,
        snapshot: AudioSnapshot,
        *,
        now_s: float,
        flush: bool = True,
        sync_offset_s: float = 0.0,
        structure_state: MusicStructureState | None = None,
    ) -> float:
        if self._base_buffer is None or self._base_buffer.length != controller.length:
            self._base_buffer = Stripe(controller.length)
            self._base_buffer.set_pixels(controller.pixels())
            self._effect_buffers.clear()
            self.scheduler.clear_active()

        transitioning = player.transition_active
        if transitioning:
            self.scheduler.clear_active()
        delay = player.step(self._base_buffer, audio_frame=snapshot.frame, flush=False)
        self.scheduler.update(
            player,
            snapshot,
            now_s=now_s,
            allow_triggers=not player.transition_active and not transitioning,
            sync_offset_s=sync_offset_s,
            structure_state=structure_state,
        )

        while len(self._effect_buffers) < len(self.scheduler.active):
            self._effect_buffers.append(Stripe(controller.length))

        modifiers = _structure_modifiers(structure_state)
        layers: list[tuple[np.ndarray, float]] = []
        for index, active in enumerate(self.scheduler.active):
            buffer = self._effect_buffers[index]
            buffer.fill(_TRANSPARENT)
            audio = active.trigger_frame if active.frame == 0 else snapshot.frame
            active.animation.tick_audio(
                active.frame,
                BrightnessController(buffer, player.brightness),
                audio,
            )
            active.frame += 1
            layers.append(
                (
                    buffer.pixels(),
                    active.definition.strength
                    * active.intensity
                    * modifiers.effect_scale,
                )
            )

        base_intensity = (
            self.scheduler.quiet_floor
            + (1.0 - self.scheduler.quiet_floor) * snapshot.musical_impact
        )
        base_intensity *= modifiers.output_scale
        composed = _visible_rgb(self._base_buffer.pixels()) * base_intensity
        budget = self.scheduler.config.max_overlay_strength
        used = 0.0
        for pixels, requested_strength in layers:
            strength = min(requested_strength, max(budget - used, 0.0))
            if strength <= 0.0:
                break
            overlay = _visible_rgb(pixels) * strength
            composed = 1.0 - (1.0 - composed) * (1.0 - overlay)
            used += strength

        output = np.empty((controller.length, 4), dtype=np.uint8)
        output[:, :3] = np.clip(composed * 255.0, 0.0, 255.0).astype(np.uint8)
        output[:, 3] = 255
        controller.set_pixels(output)
        if flush:
            controller.flush()
        return delay


def _visible_rgb(pixels: np.ndarray) -> np.ndarray:
    normalized = pixels.astype(np.float32) / 255.0
    return normalized[:, :3] * normalized[:, 3:4]


_TRANSPARENT = Rgba(0, 0, 0, 0.0)
