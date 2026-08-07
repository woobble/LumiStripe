from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from math import exp
from random import Random

from .animation.base import AnimationPlayer
from .audio import AudioFrame, AudioSnapshot, MusicFeatures, features_from_frame
from .color import Color, Rgb, Rgba
from .controller import BrightnessController, Controller
from .effects.layers import (
    EffectScheduler,
    EffectSchedulerConfig,
    EffectSchedulerDiagnostics,
    LayeredRenderer,
)
from .selector import (
    DynamicSelector,
    DynamicSelectorConfig,
    DynamicSelectorDiagnostics,
    SelectorDecision,
)


class PlaybackMode(str, Enum):
    SOLID = "solid"
    STATIC = "static"
    CYCLING = "cycling"
    DYNAMIC = "dynamic"


class AudioSource(str, Enum):
    OFF = "off"
    MIC = "mic"
    DEMO = "demo"


class MusicGateState(str, Enum):
    IDLE = "idle"
    CANDIDATE = "candidate"
    MUSIC = "music"


class CycleOrder(str, Enum):
    SEQUENTIAL = "sequential"
    SHUFFLE = "shuffle"


class CycleTiming(str, Enum):
    PER_ANIMATION = "per-animation"
    FIXED = "fixed"


@dataclass(frozen=True, slots=True)
class CyclingConfig:
    order: CycleOrder = CycleOrder.SEQUENTIAL
    timing: CycleTiming = CycleTiming.PER_ANIMATION
    interval_s: float = 30.0
    seed: int | None = None

    def __post_init__(self) -> None:
        if self.interval_s <= 0.0:
            raise ValueError("cycle interval must be greater than zero")


@dataclass(frozen=True, slots=True)
class MusicActivityConfig:
    idle_enter_frames: int = 60
    activation_delay_s: float = 0.75
    feature_attack: float = 0.28
    feature_release: float = 0.08
    energy_threshold: float = 0.03
    onset_threshold: float = 0.025
    beat_density_threshold: float = 0.05
    brightness_threshold: float = 0.08
    spectral_balance_ratio: float = 0.35

    def __post_init__(self) -> None:
        if self.idle_enter_frames <= 0:
            raise ValueError("idle_enter_frames must be greater than zero")
        if self.activation_delay_s < 0.0:
            raise ValueError("activation_delay_s must not be negative")
        if not 0.0 <= self.spectral_balance_ratio <= 1.0:
            raise ValueError("spectral_balance_ratio must be between zero and one")


@dataclass(frozen=True, slots=True)
class PlaybackConfig:
    mode: PlaybackMode = PlaybackMode.STATIC
    solid_color: Color = field(default_factory=lambda: Rgb(124, 58, 237))
    cycling: CyclingConfig = field(default_factory=CyclingConfig)
    dynamic: DynamicSelectorConfig = field(default_factory=DynamicSelectorConfig)
    activity: MusicActivityConfig = field(default_factory=MusicActivityConfig)
    idle_color: Color = field(default_factory=lambda: Rgb(32, 96, 255))
    idle_brightness: float = 0.08
    transition_duration_s: float = 0.3
    effects: EffectSchedulerConfig = field(default_factory=EffectSchedulerConfig)

    def __post_init__(self) -> None:
        if not 0.0 <= self.idle_brightness <= 1.0:
            raise ValueError("idle_brightness must be between zero and one")
        if self.transition_duration_s < 0.0:
            raise ValueError("transition_duration_s must not be negative")


@dataclass(slots=True)
class MusicActivityDetector:
    config: MusicActivityConfig = field(default_factory=MusicActivityConfig)
    active: bool = False
    inactive_frames: int = 0
    energy: float = 0.0
    onset: float = 0.0
    beat_density: float = 0.0
    brightness: float = 0.0
    candidate_since_s: float | None = None

    @property
    def state(self) -> MusicGateState:
        if self.active:
            return MusicGateState.MUSIC
        if self.candidate_since_s is not None:
            return MusicGateState.CANDIDATE
        return MusicGateState.IDLE

    def reset(self) -> None:
        self.active = False
        self.inactive_frames = 0
        self.energy = 0.0
        self.onset = 0.0
        self.beat_density = 0.0
        self.brightness = 0.0
        self.candidate_since_s = None

    def update(self, features: MusicFeatures, *, now_s: float | None = None) -> bool:
        cfg = self.config
        now = time.monotonic() if now_s is None else now_s
        self.energy = _smooth(
            self.energy, features.energy, cfg.feature_attack, cfg.feature_release
        )
        self.onset = _smooth(
            self.onset, features.onset_strength, cfg.feature_attack, cfg.feature_release
        )
        self.beat_density = _smooth(
            self.beat_density,
            1.0 if features.beat else 0.0,
            0.3,
            0.06,
        )
        self.brightness = _smooth(
            self.brightness,
            features.brightness,
            0.2,
            0.1,
        )

        signal = self.energy >= cfg.energy_threshold
        rhythmic = self.beat_density >= cfg.beat_density_threshold
        spectral_floor = features.mid_energy * cfg.spectral_balance_ratio
        broadband = (
            features.bass_energy >= cfg.energy_threshold
            and features.treble_energy >= cfg.brightness_threshold
            and min(features.bass_energy, features.treble_energy) >= spectral_floor
        )
        detected = not features.silence and signal and (rhythmic or broadband)

        if self.active:
            self.candidate_since_s = None
            if detected:
                self.inactive_frames = 0
                return True
            self.inactive_frames += 1
            if self.inactive_frames >= cfg.idle_enter_frames:
                self.active = False
            return self.active

        self.inactive_frames = 0
        if not detected:
            self.candidate_since_s = None
            return False
        if self.candidate_since_s is None:
            self.candidate_since_s = now
        if now - self.candidate_since_s >= cfg.activation_delay_s:
            self.active = True
            self.candidate_since_s = None
        return self.active


@dataclass(slots=True)
class ReactiveFrameSmoother:
    attack: float = 0.35
    release: float = 0.12
    rms: float = 0.0
    bands: list[float] = field(default_factory=lambda: [0.0] * 8)

    def reset(self) -> None:
        self.rms = 0.0
        self.bands = [0.0] * 8

    def update(self, frame: AudioFrame) -> AudioFrame:
        self.rms = _smooth(self.rms, frame.rms, self.attack, self.release)
        values = list(frame.bands[:8])
        values.extend(0.0 for _ in range(8 - len(values)))
        for index, value in enumerate(values):
            self.bands[index] = _smooth(
                self.bands[index], value, self.attack, self.release
            )
        bands = (
            self.bands[0],
            self.bands[1],
            self.bands[2],
            self.bands[3],
            self.bands[4],
            self.bands[5],
            self.bands[6],
            self.bands[7],
        )
        return replace(frame, rms=self.rms, bands=bands)


@dataclass(slots=True)
class PlaybackEngine:
    player: AnimationPlayer
    config: PlaybackConfig = field(default_factory=PlaybackConfig)
    mode: PlaybackMode = field(init=False)
    dynamic_selector: DynamicSelector = field(init=False)
    activity_detector: MusicActivityDetector = field(init=False)
    layered_renderer: LayeredRenderer = field(init=False)
    reactive_smoother: ReactiveFrameSmoother = field(init=False)
    last_decision: SelectorDecision | None = field(init=False, default=None)
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
        self.layered_renderer = LayeredRenderer(EffectScheduler(effect_config))
        self.reactive_smoother = ReactiveFrameSmoother()
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

    def effect_diagnostics(
        self, *, now_s: float | None = None
    ) -> EffectSchedulerDiagnostics:
        return self.layered_renderer.diagnostics(now_s=now_s)

    def selector_diagnostics(
        self, *, now_s: float | None = None
    ) -> DynamicSelectorDiagnostics:
        return self.dynamic_selector.diagnostics(now_s=now_s)

    def set_mode(self, mode: PlaybackMode, *, now_s: float | None = None) -> None:
        self.mode = mode
        self._cycle_started_at_s = time.monotonic() if now_s is None else now_s
        self.player.frame = 0
        self.last_decision = None
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
    ) -> float:
        now = time.monotonic() if now_s is None else now_s
        if self._cycle_started_at_s is None:
            self._cycle_started_at_s = now

        if self.mode is PlaybackMode.CYCLING and self._cycling_due(now):
            self._cycle_next()
            self._cycle_started_at_s = now

        if self.mode is PlaybackMode.SOLID:
            BrightnessController(controller, self.player.brightness).fill(
                self.solid_color
            )
            controller.flush()
            self.player.audio_enabled = False
            return 0.05

        audio_frame = None
        if self.mode is PlaybackMode.DYNAMIC:
            features = (
                snapshot.features
                if snapshot is not None
                else MusicFeatures(silence=True)
            )
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
                    self.player.name_at(self.player.current_index()),
                    False,
                    self.music_gate_state.value,
                )
                if not self._idle_rendered:
                    red, green, blue, alpha = self.config.idle_color.to_rgba()
                    controller.fill(
                        Rgba(
                            red,
                            green,
                            blue,
                            alpha * self.config.idle_brightness,
                        )
                    )
                    controller.flush()
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
            self.player.audio_enabled = True
            return self.layered_renderer.render(
                self.player,
                controller,
                active_snapshot,
                now_s=now,
            )
        elif snapshot is not None and not snapshot.silence:
            audio_frame = snapshot.frame

        self.player.audio_enabled = audio_frame is not None
        return self.player.step(controller, audio_frame=audio_frame)

    def _cycling_due(self, now_s: float) -> bool:
        if not self.player.animations:
            return False
        if self.config.cycling.timing is CycleTiming.FIXED:
            started_at = (
                now_s if self._cycle_started_at_s is None else self._cycle_started_at_s
            )
            return now_s - started_at >= self.config.cycling.interval_s
        return (
            self.player.frame
            >= self.player.animations[self.player.current_index()].frames_per_cycle
        )

    def _cycle_next(self) -> None:
        eligible = self.player.automatic_indices()
        if not eligible:
            return
        current = self.player.current_index()
        if self.config.cycling.order is CycleOrder.SHUFFLE and len(eligible) > 1:
            choices = [index for index in eligible if index != current]
            self.player.set_index(self._rng.choice(choices))
            return
        try:
            position = eligible.index(current)
        except ValueError:
            self.player.set_index(eligible[0])
            return
        self.player.set_index(eligible[(position + 1) % len(eligible)])


def _smooth(current: float, target: float, attack: float, release: float) -> float:
    rate = attack if target > current else release
    return current + (target - current) * rate


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
    audio_frame = AudioFrame(
        rms=rms,
        bands=bands,
        beat=beat,
        beat_strength=beat_strength,
        sequence=frame + 1,
        timestamp=time.monotonic() if now_s is None else now_s,
        fresh=True,
    )
    return AudioSnapshot.from_parts(audio_frame, features_from_frame(audio_frame))
