"""Playback orchestration façade over policy, activity, and rendering."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from math import exp
from random import Random

from ..animation.base import AnimationPlayer
from ..audio.dsp import features_from_frame
from ..audio.types import AudioSnapshot, MusicFeatures
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
)
from .renderer import PlaybackRenderer
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

    def __post_init__(self) -> None:
        self.mode = self.config.mode
        self.solid_color = self.config.solid_color
        self.dynamic_selector = DynamicSelector(self.config.dynamic)
        self.activity_detector = MusicActivityDetector(self.config.activity)
        effect_config = self.config.effects
        if effect_config.seed is None and self.config.dynamic.seed is not None:
            effect_config = replace(effect_config, seed=self.config.dynamic.seed)
        scheduler = EffectScheduler(effect_config)
        scheduler.set_response(self.config.dynamic_response)
        self.layered_renderer = LayeredRenderer(scheduler)
        self.reactive_smoother = ReactiveFrameSmoother()
        self.render_adapter = PlaybackRenderer()
        self.player.transition_ms = int(self.config.transition_duration_s * 1000.0)
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
        self.layered_renderer.reset()
        self.reactive_smoother.reset()
        self.player.cancel_transition()
        if mode is PlaybackMode.DYNAMIC:
            self.dynamic_selector.reset()

    def set_solid_color(self, color: Color) -> None:
        self.solid_color = color

    def set_activity_config(self, config: MusicActivityConfig) -> None:
        self.activity_detector.config = config
        self.activity_detector.reset()
        self._music_active = False

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

    def select_animation(self, name: str) -> None:
        index = self.player.index_of(name)
        if index is None:
            raise ValueError(f"unknown animation: {name}")
        self.player.set_index(index, transition_ms=0)
        self.set_mode(PlaybackMode.STATIC)

    def next_animation(self) -> None:
        self.player.set_index(
            (self.player.current_index() + 1) % max(len(self.player.animations), 1),
            transition_ms=0,
        )
        self.set_mode(PlaybackMode.STATIC)

    def previous_animation(self) -> None:
        current = self.player.current_index()
        previous = len(self.player.animations) - 1 if current == 0 else current - 1
        self.player.set_index(previous, transition_ms=0)
        self.set_mode(PlaybackMode.STATIC)

    def step(
        self,
        controller: Controller,
        *,
        snapshot: AudioSnapshot | None = None,
        now_s: float | None = None,
        flush: bool = True,
    ) -> float:
        now = time.monotonic() if now_s is None else now_s
        if self._cycle_started_at_s is None:
            self._cycle_started_at_s = now

        if self.mode is PlaybackMode.CYCLING and self._cycling_due(now):
            self._cycle_next()
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
            return 0.05

        if self.mode is PlaybackMode.DYNAMIC:
            features = snapshot.features if snapshot is not None else MusicFeatures(silence=True)
            was_active = self._music_active
            self._music_active = self.activity_detector.update(features, now_s=now)
            if not self._music_active:
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
                if not self._idle_rendered:
                    self.render_adapter.clear(
                        build_render_plan(decision, flush=flush),
                        controller,
                    )
                    self._idle_rendered = True
                return 0.05

            self._idle_rendered = False
            self.last_decision = self.dynamic_selector.update(
                self.player,
                features,
                now_s=now,
                quiet=False,
                force_switch=was_active != self._music_active,
            )
            if was_active and self.last_decision.should_switch:
                self.player.begin_transition(
                    duration_ms=self.player.transition_ms,
                    source=controller.pixels(),
                )
            active_snapshot = snapshot or AudioSnapshot.silence()
            active_snapshot = replace(
                active_snapshot,
                frame=self.reactive_smoother.update(active_snapshot.frame),
            )
            if not was_active:
                if not self.last_decision.should_switch:
                    self.player.set_index(
                        self.player.current_index(),
                        transition_ms=0,
                        restart=True,
                    )
                self.player.begin_transition(
                    duration_ms=self.player.transition_ms,
                    source=controller.pixels(),
                )
            decision = decide_playback(
                self.mode,
                animation_name=self.player.name_at(self.player.current_index()),
                music_active=True,
                gate_state=self.music_gate_state,
                brightness=self.player.brightness,
                should_switch=self.last_decision.should_switch,
            )
            plan = build_render_plan(
                decision,
                snapshot=active_snapshot,
                transition_ms=self.player.transition_ms,
                flush=flush,
            )
            self.last_playback_decision = decision
            self.player.audio_enabled = plan.decision.audio_enabled
            return self.layered_renderer.render(
                self.player,
                controller,
                plan.snapshot or active_snapshot,
                now_s=now,
                flush=plan.flush,
            )

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
        return self.player.step(controller, audio_frame=audio_frame, flush=plan.flush)

    def _cycling_due(self, now_s: float) -> bool:
        if not self.player.animations:
            return False
        if self.config.cycling.timing is CycleTiming.FIXED:
            started_at = now_s if self._cycle_started_at_s is None else self._cycle_started_at_s
            return now_s - started_at >= self.config.cycling.interval_s
        return self.player.frame >= self.player.animations[self.player.current_index()].frames_per_cycle

    def _cycle_next(self) -> None:
        eligible = self.player.automatic_indices()
        if not eligible:
            return
        current = self.player.current_index()
        if self.config.cycling.order.value == "shuffle" and len(eligible) > 1:
            choices = [index for index in eligible if index != current]
            self.player.set_index(self._rng.choice(choices))
            return
        try:
            position = eligible.index(current)
        except ValueError:
            self.player.set_index(eligible[0])
            return
        self.player.set_index(eligible[(position + 1) % len(eligible)])


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
