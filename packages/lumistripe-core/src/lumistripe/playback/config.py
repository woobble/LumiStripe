"""Playback policy configuration without runtime state or rendering imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite

from ..animation.base import TransitionEffect
from ..audio.timeline import MusicalTimelineConfig
from ..color import Color, Rgb
from ..effects.layers import EffectSchedulerConfig
from ..selector import DynamicSelectorConfig
from .activity import MusicActivityConfig
from .sequence import AnimationSequence


class PlaybackMode(str, Enum):
    SOLID = "solid"
    STATIC = "static"
    CYCLING = "cycling"
    DYNAMIC = "dynamic"


class SwitchQuantization(str, Enum):
    OFF = "off"
    BEAT = "beat"
    BAR = "bar"
    PHRASE = "phrase"


class AudioSource(str, Enum):
    OFF = "off"
    MIC = "mic"
    DEMO = "demo"
    BLUETOOTH = "bluetooth"
    SPOTIFY = "spotify"


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
class PlaybackConfig:
    mode: PlaybackMode = PlaybackMode.STATIC
    solid_color: Color = field(default_factory=lambda: Rgb(124, 58, 237))
    cycling: CyclingConfig = field(default_factory=CyclingConfig)
    dynamic: DynamicSelectorConfig = field(default_factory=DynamicSelectorConfig)
    switch_quantization: SwitchQuantization = SwitchQuantization.OFF
    animation_sequence: AnimationSequence | None = None
    musical_timeline: MusicalTimelineConfig = field(
        default_factory=MusicalTimelineConfig
    )
    activity: MusicActivityConfig = field(default_factory=MusicActivityConfig)
    idle_color: Color = field(default_factory=lambda: Rgb(32, 96, 255))
    idle_brightness: float = 0.08
    transition_duration_s: float = 0.3
    dynamic_response: float = 0.65
    effects: EffectSchedulerConfig = field(default_factory=EffectSchedulerConfig)
    transition_effect: TransitionEffect = field(
        default=TransitionEffect.CROSSFADE, kw_only=True
    )

    def __post_init__(self) -> None:
        if self.animation_sequence is not None and not isinstance(
            self.animation_sequence, AnimationSequence
        ):
            raise TypeError("animation_sequence must be an AnimationSequence or None")
        if not 0.0 <= self.idle_brightness <= 1.0:
            raise ValueError("idle_brightness must be between zero and one")
        if (
            isinstance(self.transition_duration_s, bool)
            or not isinstance(self.transition_duration_s, (int, float))
            or not isfinite(self.transition_duration_s)
            or self.transition_duration_s < 0.0
        ):
            raise ValueError("transition_duration_s must be a finite non-negative number")
        if not isinstance(self.transition_effect, TransitionEffect):
            raise TypeError("transition_effect must be a TransitionEffect")
        if not 0.0 <= self.dynamic_response <= 1.0:
            raise ValueError("dynamic_response must be between zero and one")


__all__ = [
    "AudioSource",
    "CycleOrder",
    "CycleTiming",
    "CyclingConfig",
    "PlaybackConfig",
    "PlaybackMode",
    "SwitchQuantization",
    "TransitionEffect",
]
