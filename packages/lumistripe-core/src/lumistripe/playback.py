from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from math import exp
from random import Random

from .animation.base import AnimationPlayer
from .audio import AudioFrame, AudioSnapshot, MusicFeatures, features_from_frame
from .controller import Controller
from .selector import DynamicSelector, DynamicSelectorConfig, SelectorDecision


class PlaybackMode(str, Enum):
    STATIC = "static"
    CYCLING = "cycling"
    DYNAMIC = "dynamic"


class AudioSource(str, Enum):
    OFF = "off"
    MIC = "mic"
    DEMO = "demo"


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
    feature_attack: float = 0.28
    feature_release: float = 0.08
    energy_threshold: float = 0.03
    onset_threshold: float = 0.025
    beat_density_threshold: float = 0.05
    brightness_threshold: float = 0.08

    def __post_init__(self) -> None:
        if self.idle_enter_frames <= 0:
            raise ValueError("idle_enter_frames must be greater than zero")


@dataclass(frozen=True, slots=True)
class PlaybackConfig:
    mode: PlaybackMode = PlaybackMode.STATIC
    cycling: CyclingConfig = field(default_factory=CyclingConfig)
    dynamic: DynamicSelectorConfig = field(default_factory=DynamicSelectorConfig)
    activity: MusicActivityConfig = field(default_factory=MusicActivityConfig)


@dataclass(slots=True)
class MusicActivityDetector:
    config: MusicActivityConfig = field(default_factory=MusicActivityConfig)
    active: bool = False
    inactive_frames: int = 0
    energy: float = 0.0
    onset: float = 0.0
    beat_density: float = 0.0
    brightness: float = 0.0

    def reset(self) -> None:
        self.active = False
        self.inactive_frames = 0
        self.energy = 0.0
        self.onset = 0.0
        self.beat_density = 0.0
        self.brightness = 0.0

    def update(self, features: MusicFeatures) -> bool:
        cfg = self.config
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
        musical_detail = any(
            (
                self.onset >= cfg.onset_threshold,
                self.beat_density >= cfg.beat_density_threshold,
                self.brightness >= cfg.brightness_threshold,
            )
        )
        detected = not features.silence and signal and musical_detail
        if detected:
            self.active = True
            self.inactive_frames = 0
        else:
            self.inactive_frames += 1
            if self.inactive_frames >= cfg.idle_enter_frames:
                self.active = False
        return self.active


@dataclass(slots=True)
class PlaybackEngine:
    player: AnimationPlayer
    config: PlaybackConfig = field(default_factory=PlaybackConfig)
    mode: PlaybackMode = field(init=False)
    dynamic_selector: DynamicSelector = field(init=False)
    activity_detector: MusicActivityDetector = field(init=False)
    last_decision: SelectorDecision | None = field(init=False, default=None)
    _cycle_started_at_s: float | None = field(init=False, default=None)
    _music_active: bool = field(init=False, default=False)
    _rng: Random = field(init=False)

    def __post_init__(self) -> None:
        self.mode = self.config.mode
        self.dynamic_selector = DynamicSelector(self.config.dynamic)
        self.activity_detector = MusicActivityDetector(self.config.activity)
        self._rng = Random(self.config.cycling.seed)

    @property
    def music_active(self) -> bool:
        return self._music_active

    def set_mode(self, mode: PlaybackMode, *, now_s: float | None = None) -> None:
        self.mode = mode
        self._cycle_started_at_s = time.monotonic() if now_s is None else now_s
        self.player.frame = 0
        self.last_decision = None
        self._music_active = False
        self.activity_detector.reset()
        if mode is PlaybackMode.DYNAMIC:
            self.dynamic_selector.reset()

    def select_animation(self, name: str) -> None:
        index = self.player.index_of(name)
        if index is None:
            raise ValueError(f"unknown animation: {name}")
        self.player.set_index(index)
        self.set_mode(PlaybackMode.STATIC)

    def next_animation(self) -> None:
        self.player.next()
        self.set_mode(PlaybackMode.STATIC)

    def previous_animation(self) -> None:
        self.player.prev()
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

        audio_frame = None
        if self.mode is PlaybackMode.DYNAMIC:
            features = (
                snapshot.features
                if snapshot is not None
                else MusicFeatures(silence=True)
            )
            was_active = self._music_active
            self._music_active = self.activity_detector.update(features)
            self.last_decision = self.dynamic_selector.update(
                self.player,
                features,
                now_s=now,
                quiet=not self._music_active,
                force_switch=was_active != self._music_active,
            )
            if self._music_active and snapshot is not None:
                audio_frame = snapshot.frame
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
