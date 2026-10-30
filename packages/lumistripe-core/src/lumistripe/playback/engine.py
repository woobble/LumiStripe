"""Playback orchestration façade over policy, activity, and rendering."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from math import exp
from random import Random
from typing import Literal

import numpy as np

from ..animation.base import AnimationPlayer, TransitionEffect
from ..animation.catalog import animation_catalog, dynamic_sequence_eligible
from ..audio.beat import BeatClockTracker
from ..audio.dsp import features_from_frame
from ..audio.structure import MusicStructureState, MusicStructureTracker
from ..audio.timeline import (
    MusicalTimelineConfig,
    MusicalTimelineState,
    MusicalTimelineTracker,
)
from ..audio.types import AudioSnapshot, BeatClockState, MusicFeatures
from ..color import Color
from ..controller import Controller
from ..effects.layers import (
    EffectScheduler,
    EffectSchedulerDiagnostics,
    LayeredRenderer,
)
from ..selector import (
    DynamicSelector,
    DynamicSelectorDiagnostics,
    SelectorDecision,
)
from .activity import (
    MusicActivityConfig,
    MusicActivityDetector,
    MusicGateState,
    ReactiveFrameSmoother,
)
from .config import (
    AudioSource,
    CycleTiming,
    PlaybackConfig,
    PlaybackMode,
    SwitchQuantization,
)
from .quantized import QuantizedSwitchCoordinator, QuantizedSwitchDiagnostics
from .renderer import PlaybackRenderer
from .sequence import (
    AnimationSequence,
    AnimationSequenceCoordinator,
    SequencePlaybackState,
)
from .state import PlaybackDecision, RenderPlan, build_render_plan, decide_playback


@dataclass(slots=True)
class PlaybackEngine:
    """Application façade; its collaborators expose independently testable seams."""

    player: AnimationPlayer
    config: PlaybackConfig = field(default_factory=PlaybackConfig)
    music_recognition_enabled: bool = True
    mode: PlaybackMode = field(init=False)
    dynamic_selector: DynamicSelector = field(init=False)
    activity_detector: MusicActivityDetector = field(init=False)
    layered_renderer: LayeredRenderer = field(init=False)
    reactive_smoother: ReactiveFrameSmoother = field(init=False)
    render_adapter: PlaybackRenderer = field(init=False)
    last_decision: SelectorDecision | None = field(init=False, default=None)
    last_playback_decision: PlaybackDecision | None = field(init=False, default=None)
    _cycle_started_at_s: float | None = field(init=False, default=None)
    _music_active: bool = field(init=False, default=False)
    _idle_rendered: bool = field(init=False, default=False)
    _rng: Random = field(init=False)
    solid_color: Color = field(init=False)
    idle_mode: Literal["off", "solid", "animation"] = field(init=False, default="off")
    idle_color: Color = field(init=False)
    idle_animation: str = field(init=False, default="")
    _idle_player: AnimationPlayer = field(init=False, default_factory=AnimationPlayer)
    beat_clock_tracker: BeatClockTracker = field(init=False)
    _beat_clock_state: BeatClockState = field(init=False)
    _structure_tracker: MusicStructureTracker = field(init=False)
    _music_structure_state: MusicStructureState = field(
        init=False, default_factory=MusicStructureState
    )
    _confirmed_drop_count: int = field(init=False, default=0)
    musical_timeline_tracker: MusicalTimelineTracker = field(init=False)
    _musical_timeline_state: MusicalTimelineState = field(
        init=False, default_factory=MusicalTimelineState
    )
    _quantized_switcher: QuantizedSwitchCoordinator = field(init=False)
    _sequence_coordinator: AnimationSequenceCoordinator = field(init=False)
    sync_offset_s: float = field(init=False, default=0.0)
    _last_rendered_frame: np.ndarray | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.mode = self.config.mode
        self.solid_color = self.config.solid_color
        self.idle_color = self.config.idle_color
        self.dynamic_selector = DynamicSelector(self.config.dynamic)
        self.activity_detector = MusicActivityDetector(self.config.activity)
        effect_config = self.config.effects
        if effect_config.seed is None and self.config.dynamic.seed is not None:
            effect_config = replace(effect_config, seed=self.config.dynamic.seed)
        scheduler = EffectScheduler(effect_config)
        scheduler.set_response(self.config.dynamic_response)
        self.layered_renderer = LayeredRenderer(scheduler)
        self._quantized_switcher = QuantizedSwitchCoordinator(
            self.config.switch_quantization,
            confidence_threshold=effect_config.clock_confidence_threshold,
            subdivisions_per_beat=effect_config.subdivisions_per_beat,
        )
        self.validate_animation_sequence(self.config.animation_sequence)
        self._sequence_coordinator = AnimationSequenceCoordinator(
            self.config.animation_sequence,
            confidence_threshold=effect_config.clock_confidence_threshold,
            subdivisions_per_beat=effect_config.subdivisions_per_beat,
        )
        self.reactive_smoother = ReactiveFrameSmoother()
        self.render_adapter = PlaybackRenderer()
        self.beat_clock_tracker = BeatClockTracker()
        self._beat_clock_state = self.beat_clock_tracker.state(0.0)
        self.musical_timeline_tracker = MusicalTimelineTracker(
            self.config.musical_timeline
        )
        self._musical_timeline_state = self.musical_timeline_tracker.update(
            self._beat_clock_state
        )
        self._structure_tracker = MusicStructureTracker()
        self.player.transition_ms = int(self.config.transition_duration_s * 1000.0)
        self.player.transition_effect = self.config.transition_effect
        self._rng = Random(self.config.cycling.seed)

    @property
    def music_active(self) -> bool:
        return self._music_active

    @property
    def music_gate_state(self) -> MusicGateState:
        return self.activity_detector.state

    @property
    def active_effect_names(self) -> tuple[str, ...]:
        return self.layered_renderer.active_effect_names

    def effect_diagnostics(self, *, now_s: float | None = None) -> EffectSchedulerDiagnostics:
        return self.layered_renderer.diagnostics(now_s=now_s)

    def set_transition_duration(self, duration_s: float) -> None:
        """Set the duration used by transitions started from now on.

        An in-progress transition retains its captured source and duration.
        """

        self.config = replace(self.config, transition_duration_s=duration_s)
        self.player.transition_ms = int(self.config.transition_duration_s * 1000.0)

    def set_transition_effect(self, effect: TransitionEffect) -> None:
        """Set the effect used by transitions started from now on."""

        if not isinstance(effect, TransitionEffect):
            raise TypeError("effect must be a TransitionEffect")
        self.config = replace(self.config, transition_effect=effect)
        self.player.transition_effect = effect

    @property
    def beat_clock_state(self) -> BeatClockState:
        return self._beat_clock_state

    @property
    def musical_timeline_state(self) -> MusicalTimelineState:
        return self._musical_timeline_state

    @property
    def sequence_playback_state(self) -> SequencePlaybackState:
        return self._sequence_coordinator.state

    def set_animation_sequence(self, sequence: AnimationSequence | None) -> None:
        """Replace the active Dynamic sequence, cancelling its current timing session."""

        self.validate_animation_sequence(sequence)
        if sequence == self._sequence_coordinator.sequence:
            return
        # A pending automatic candidate belongs to the previous playback policy.
        # Do not let it commit after custom sequencing has taken control (or
        # after control has returned to the automatic selector).
        self.dynamic_selector.reset()
        self._quantized_switcher.reset()
        self._sequence_coordinator.set_sequence(sequence)
        self.config = replace(self.config, animation_sequence=sequence)

    def validate_animation_sequence(
        self, sequence: AnimationSequence | None
    ) -> None:
        if sequence is None:
            return
        definitions = {definition.name: definition for definition in animation_catalog(self.player)}
        for step in sequence.steps:
            definition = definitions.get(step.animation_name)
            if definition is None:
                raise ValueError(f"unknown animation in sequence: {step.animation_name}")
            if not dynamic_sequence_eligible(definition):
                raise ValueError(
                    f"animation is not eligible for Dynamic sequences: {step.animation_name}"
                )

    def quantized_switch_diagnostics(
        self, *, now_s: float | None = None
    ) -> QuantizedSwitchDiagnostics:
        now = time.monotonic() if now_s is None else now_s
        candidate_waiting = (
            self.mode is PlaybackMode.DYNAMIC
            and self._music_active
            and self.dynamic_selector.last_decision.should_switch
        )
        return self._quantized_switcher.diagnostics(
            self._musical_timeline_state,
            now_s=now,
            sync_offset_s=self.sync_offset_s,
            candidate_waiting=candidate_waiting,
        )

    def set_sequencer_config(
        self,
        switch_quantization: SwitchQuantization,
        musical_timeline: MusicalTimelineConfig,
    ) -> None:
        """Apply sequencer settings and discard any switch queued under the old grid."""

        if (
            self.config.switch_quantization is switch_quantization
            and self.config.musical_timeline == musical_timeline
        ):
            return
        self.config = replace(
            self.config,
            switch_quantization=switch_quantization,
            musical_timeline=musical_timeline,
        )
        self._quantized_switcher.set_mode(switch_quantization)
        self._quantized_switcher.reset()
        self.musical_timeline_tracker.config = musical_timeline
        self._musical_timeline_state = self.musical_timeline_tracker.update(
            self._beat_clock_state
        )

    @property
    def music_structure_state(self) -> MusicStructureState:
        return self._music_structure_state

    @property
    def confirmed_drop_count(self) -> int:
        return self._confirmed_drop_count

    def set_sync_offset(self, offset_s: float) -> None:
        if not -0.5 <= offset_s <= 0.5:
            raise ValueError("audio sync offset must be between -0.5 and 0.5 seconds")
        self.sync_offset_s = offset_s

    def selector_diagnostics(self, *, now_s: float | None = None) -> DynamicSelectorDiagnostics:
        return self.dynamic_selector.diagnostics(now_s=now_s)

    def set_mode(self, mode: PlaybackMode, *, now_s: float | None = None) -> None:
        self.mode = mode
        self._cycle_started_at_s = time.monotonic() if now_s is None else now_s
        self.player.frame = 0
        self.last_decision = None
        self.last_playback_decision = None
        self._music_active = False
        self._idle_rendered = False
        self.activity_detector.reset()
        self._structure_tracker.reset()
        self._music_structure_state = MusicStructureState()
        self._confirmed_drop_count = 0
        self._quantized_switcher.reset()
        self._sequence_coordinator.reset_session()
        self.layered_renderer.reset()
        self.reactive_smoother.reset()
        self.player.cancel_transition()
        if mode is PlaybackMode.DYNAMIC:
            self.dynamic_selector.reset()

    def set_solid_color(self, color: Color) -> None:
        self.solid_color = color

    def set_idle(self, mode: Literal["off", "solid", "animation"], color: Color, animation: str) -> None:
        if mode not in ("off", "solid", "animation"):
            raise ValueError("invalid idle mode")
        index = self.player.index_of(animation) if animation else None
        if animation and index is None:
            raise ValueError(f"unknown animation: {animation}")
        if mode == "animation" and index is None:
            raise ValueError("idle animation is required")
        self.idle_mode, self.idle_color, self.idle_animation = mode, color, animation
        self._idle_player = AnimationPlayer()
        if index is not None:
            entry = self.player.animations[index]
            self._idle_player.add(entry.fresh_animation(), entry.frame_ms, entry.frames_per_cycle)
        self._idle_player.audio_enabled = False
        self._idle_rendered = False

    def set_activity_config(self, config: MusicActivityConfig) -> None:
        self.activity_detector.config = config
        self.activity_detector.reset()
        self._music_active = False
        self._structure_tracker.reset()
        self._music_structure_state = MusicStructureState()
        self._confirmed_drop_count = 0
        self._quantized_switcher.reset()

    def set_dynamic_response(self, response: float) -> None:
        if not 0.0 <= response <= 1.0:
            raise ValueError("dynamic response must be between zero and one")
        self.config = replace(self.config, dynamic_response=response)
        self.layered_renderer.scheduler.set_response(response)

    def set_music_recognition_enabled(self, enabled: bool) -> None:
        self.music_recognition_enabled = bool(enabled)
        if not enabled:
            self.activity_detector.reset()
            self._music_active = False
            self._structure_tracker.reset()
            self._music_structure_state = MusicStructureState()
            self._confirmed_drop_count = 0
            self._quantized_switcher.reset()
            self._sequence_coordinator.reset_session()

    def select_animation(self, name: str, *, now_s: float | None = None) -> None:
        index = self.player.index_of(name)
        if index is None:
            raise ValueError(f"unknown animation: {name}")
        now = time.monotonic() if now_s is None else now_s
        self.set_mode(PlaybackMode.STATIC, now_s=now)
        self._set_animation_index(index, now_s=now)

    def next_animation(self, *, now_s: float | None = None) -> None:
        now = time.monotonic() if now_s is None else now_s
        self.set_mode(PlaybackMode.STATIC, now_s=now)
        self._set_animation_index(
            (self.player.current_index() + 1) % max(len(self.player.animations), 1),
            now_s=now,
        )

    def previous_animation(self, *, now_s: float | None = None) -> None:
        now = time.monotonic() if now_s is None else now_s
        current = self.player.current_index()
        previous = len(self.player.animations) - 1 if current == 0 else current - 1
        self.set_mode(PlaybackMode.STATIC, now_s=now)
        self._set_animation_index(previous, now_s=now)

    @property
    def transition_active(self) -> bool:
        return self.player.transition_active

    def cancel_transition(self) -> None:
        self.player.cancel_transition()

    def _set_animation_index(self, index: int, *, now_s: float) -> bool:
        previous = self.player.current_index()
        self.player.set_index(index, transition_ms=0, now_s=now_s)
        if index == previous:
            return False
        self._begin_transition(now_s=now_s)
        return True

    def _begin_transition(
        self,
        *,
        now_s: float,
        source: np.ndarray | None = None,
    ) -> None:
        if source is None:
            source = self._last_rendered_frame
        self.player.begin_transition(
            duration_ms=self.player.transition_ms,
            source=source,
            now_s=now_s,
        )

    def _remember_rendered_frame(self, controller: Controller) -> None:
        pixels = controller.pixels()
        if (
            self._last_rendered_frame is None
            or self._last_rendered_frame.shape != pixels.shape
        ):
            self._last_rendered_frame = np.empty_like(pixels)
        np.copyto(self._last_rendered_frame, pixels)

    def step(
        self,
        controller: Controller,
        *,
        snapshot: AudioSnapshot | None = None,
        now_s: float | None = None,
        flush: bool = True,
    ) -> float:
        now = time.monotonic() if now_s is None else now_s
        if snapshot is not None:
            self._beat_clock_state = self.beat_clock_tracker.update(
                snapshot.beat_events,
                now_s=now,
                fallback_bpm=snapshot.features.bpm,
            )
            snapshot = replace(snapshot, beat_clock=self._beat_clock_state)
        else:
            self._beat_clock_state = self.beat_clock_tracker.state(now)
        self._musical_timeline_state = self.musical_timeline_tracker.update(
            self._beat_clock_state
        )
        if self._cycle_started_at_s is None:
            self._cycle_started_at_s = now

        if self.mode is PlaybackMode.CYCLING and self._cycling_due(now):
            self._cycle_next(now_s=now)
            self._cycle_started_at_s = now

        current_name = self.player.name_at(self.player.current_index())
        if self.mode is PlaybackMode.SOLID:
            decision = decide_playback(
                self.mode,
                animation_name=current_name,
                solid_color=self.solid_color,
                brightness=self.player.brightness,
            )
            self.last_playback_decision = decision
            self.render_adapter.render_solid(build_render_plan(decision, flush=flush), controller)
            self.player.audio_enabled = False
            self.player.cancel_transition()
            self._remember_rendered_frame(controller)
            return 0.05

        if self.mode is PlaybackMode.DYNAMIC:
            features = snapshot.features if snapshot is not None else MusicFeatures(silence=True)
            was_active = self._music_active
            self._music_active = self.activity_detector.update(features, now_s=now)
            if not self._music_active:
                self._quantized_switcher.reset()
                self._sequence_coordinator.reset_session()
                self._structure_tracker.reset()
                self._music_structure_state = MusicStructureState()
                self._confirmed_drop_count = 0
                if was_active:
                    self.layered_renderer.reset()
                    self.reactive_smoother.reset()
                    self.player.cancel_transition()
                self.player.audio_enabled = False
                self.last_decision = SelectorDecision(
                    None,
                    0.0,
                    current_name,
                    False,
                    self.music_gate_state.value,
                )
                decision = decide_playback(
                    self.mode,
                    animation_name=current_name,
                    gate_state=self.music_gate_state,
                    brightness=self.player.brightness,
                )
                self.last_playback_decision = decision
                if self.idle_mode == "animation":
                    self._idle_player.set_brightness(self.player.brightness)
                    delay = self._idle_player.step(controller, flush=flush, now_s=now)
                    self._remember_rendered_frame(controller)
                    return delay
                if self.idle_mode == "solid":
                    solid = decide_playback(PlaybackMode.SOLID, animation_name=None, solid_color=self.idle_color, brightness=self.player.brightness)
                    self.render_adapter.render_solid(build_render_plan(solid, flush=flush), controller)
                elif not self._idle_rendered:
                    self.render_adapter.clear(
                        build_render_plan(decision, flush=flush),
                        controller,
                    )
                    self._idle_rendered = True
                self._remember_rendered_frame(controller)
                return 0.05

            if not was_active:
                self._quantized_switcher.reset()
                self._sequence_coordinator.reset_session()
                self._structure_tracker.reset()
                self._music_structure_state = MusicStructureState()
                self._confirmed_drop_count = 0
            if snapshot is not None and snapshot.fresh:
                self._music_structure_state = self._structure_tracker.update(
                    features,
                    now,
                )
                if self._music_structure_state.drop_event:
                    self._confirmed_drop_count += 1
            else:
                # Drop events are edges. Do not let a held state schedule the
                # same accent again while no new audio frame has arrived.
                self._music_structure_state = replace(
                    self._music_structure_state, drop_event=False
                )
            self._idle_rendered = False
            switched_this_step = False
            sequence_controls_playback = False
            if self.config.animation_sequence is not None:
                sequence_animation = self._sequence_coordinator.update(
                    self._musical_timeline_state,
                    now_s=now,
                    sync_offset_s=self.sync_offset_s,
                    music_active=True,
                )
                if sequence_animation is not None:
                    target_index = self.player.index_of(sequence_animation)
                    if target_index is None:
                        raise RuntimeError(
                            f"configured sequence animation disappeared: {sequence_animation}"
                        )
                    if target_index != self.player.current_index():
                        self._set_animation_index(target_index, now_s=now)
                        switched_this_step = True
                sequence_controls_playback = (
                    self._sequence_coordinator.controls_playback
                )
                if sequence_controls_playback:
                    current = self.player.name_at(self.player.current_index())
                    self.last_decision = SelectorDecision(
                        selected_name=current,
                        selected_score=0.0,
                        current_name=current,
                        should_switch=switched_this_step,
                        reason="sequence",
                    )

            if not sequence_controls_playback:
                if not was_active:
                    # Keep activation responsive when automatic Dynamic mode
                    # starts without a configured custom sequence.
                    self.last_decision = self.dynamic_selector.propose(
                        self.player,
                        features,
                        now_s=now,
                        quiet=False,
                        force_switch=True,
                    )
                    switched_this_step = self.dynamic_selector.commit(
                        self.player,
                        self.last_decision,
                        now_s=now,
                        transition_ms=0,
                    )
                    if switched_this_step:
                        self._begin_transition(now_s=now)
                elif self.config.switch_quantization is SwitchQuantization.OFF:
                    self.last_decision = self.dynamic_selector.propose(
                        self.player,
                        features,
                        now_s=now,
                        quiet=False,
                        force_switch=False,
                    )
                    switched_this_step = self.dynamic_selector.commit(
                        self.player,
                        self.last_decision,
                        now_s=now,
                        transition_ms=0,
                    )
                    if switched_this_step:
                        self._begin_transition(now_s=now)
                else:
                    self.last_decision = self.dynamic_selector.propose(
                        self.player,
                        features,
                        now_s=now,
                        quiet=False,
                        force_switch=False,
                    )
                    due_decision = self._quantized_switcher.update(
                        self.last_decision,
                        self._musical_timeline_state,
                        now_s=now,
                        sync_offset_s=self.sync_offset_s,
                        transition_active=self.transition_active,
                    )
                    if due_decision is not None:
                        switched_this_step = self.dynamic_selector.commit(
                            self.player,
                            due_decision,
                            now_s=now,
                            transition_ms=0,
                        )
                        if switched_this_step:
                            self._begin_transition(now_s=now)
            active_snapshot = snapshot or AudioSnapshot.silence()
            active_snapshot = replace(
                active_snapshot,
                frame=self.reactive_smoother.update(active_snapshot.frame),
            )
            if (
                not was_active
                and not sequence_controls_playback
                and not switched_this_step
            ):
                self.player.set_index(
                    self.player.current_index(),
                    transition_ms=0,
                    restart=True,
                )
                self._begin_transition(now_s=now)
            decision = decide_playback(
                self.mode,
                animation_name=self.player.name_at(self.player.current_index()),
                music_active=True,
                gate_state=self.music_gate_state,
                brightness=self.player.brightness,
                should_switch=switched_this_step,
            )
            plan = build_render_plan(
                decision,
                snapshot=active_snapshot,
                transition_ms=self.player.transition_ms,
                flush=flush,
            )
            self.last_playback_decision = decision
            self.player.audio_enabled = plan.decision.audio_enabled
            delay = self.layered_renderer.render(
                self.player,
                controller,
                plan.snapshot or active_snapshot,
                now_s=now,
                flush=plan.flush,
                sync_offset_s=self.sync_offset_s,
                structure_state=self._music_structure_state,
            )
            self._remember_rendered_frame(controller)
            return delay

        audio_frame = None
        if self.music_recognition_enabled and snapshot is not None and not snapshot.silence:
            audio_frame = snapshot.frame
        decision = decide_playback(
            self.mode,
            animation_name=current_name,
            audio_source=AudioSource.MIC if audio_frame is not None else AudioSource.OFF,
            brightness=self.player.brightness,
        )
        plan = build_render_plan(decision, snapshot=snapshot, flush=flush)
        self.last_playback_decision = decision
        self.player.audio_enabled = plan.decision.audio_enabled
        delay = self.player.step(
            controller,
            audio_frame=audio_frame,
            flush=plan.flush,
            now_s=now,
        )
        self._remember_rendered_frame(controller)
        return delay

    def _cycling_due(self, now_s: float) -> bool:
        if not self.player.animations:
            return False
        if self.config.cycling.timing is CycleTiming.FIXED:
            started_at = now_s if self._cycle_started_at_s is None else self._cycle_started_at_s
            return now_s - started_at >= self.config.cycling.interval_s
        return self.player.frame >= self.player.animations[self.player.current_index()].frames_per_cycle

    def _cycle_next(self, *, now_s: float) -> None:
        eligible = self.player.automatic_indices()
        if not eligible:
            return
        current = self.player.current_index()
        if self.config.cycling.order.value == "shuffle" and len(eligible) > 1:
            choices = [index for index in eligible if index != current]
            self._set_animation_index(self._rng.choice(choices), now_s=now_s)
            return
        try:
            position = eligible.index(current)
        except ValueError:
            self._set_animation_index(eligible[0], now_s=now_s)
            return
        self._set_animation_index(eligible[(position + 1) % len(eligible)], now_s=now_s)


def demo_snapshot(frame: int, *, now_s: float | None = None) -> AudioSnapshot:
    frames_per_beat = 25
    frames_per_measure = frames_per_beat * 4
    measure_pos = frame % frames_per_measure
    beat_index = measure_pos // frames_per_beat
    phase = (measure_pos % frames_per_beat) / frames_per_beat
    decay = exp(-phase * 5.0)
    fast = exp(-phase * 12.0)
    slow = exp(-phase * 2.0)
    kick = fast if beat_index in (0, 2) else 0.001
    snare = decay * 0.8 if beat_index in (1, 3) else 0.001
    bass = slow * 0.6 * (1.0, 1.0, 1.0, 0.75)[(frame // frames_per_measure) % 4]
    position = measure_pos % frames_per_beat
    hat = 0.35 if position < 2 or 12 <= position < 14 else 0.001
    accent = 0.2 if 62 <= measure_pos < 64 else 0.0
    bands = (
        min(kick, 1.0),
        min(bass, 1.0),
        min(snare * 0.3 + kick * 0.15, 1.0),
        min(snare, 1.0),
        min(snare * 0.5 + hat * 0.3, 1.0),
        min(hat + accent, 1.0),
        min((hat + accent) * 0.4, 1.0),
        min((hat + accent) * 0.15, 1.0),
    )
    rms = min((sum(bands) / len(bands)) ** 0.5, 1.0)
    beat = beat_index == 0
    beat_strength = 0.7 + kick * 0.3 if beat else 0.0
    from ..audio.types import AudioFrame

    audio_frame = AudioFrame(
        rms=rms,
        bands=bands,
        beat=beat,
        beat_strength=beat_strength,
        sequence=frame + 1,
        timestamp=time.monotonic() if now_s is None else now_s,
        fresh=True,
    )
    from dataclasses import replace as dataclass_replace

    features = dataclass_replace(
        features_from_frame(audio_frame),
        program_loudness=rms,
        musical_impact=min(1.0, rms * 1.5),
    )
    return AudioSnapshot.from_parts(audio_frame, features)


__all__ = [
    "PlaybackDecision",
    "PlaybackEngine",
    "RenderPlan",
    "build_render_plan",
    "decide_playback",
    "demo_snapshot",
]
