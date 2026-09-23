"""The single typed registry for animations and audio-triggered effects."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..selector import AnimationMetadata, AnimationRole, animation_metadata
from .base import Animation, AnimationPlayer

if TYPE_CHECKING:
    from ..effects.definitions import EffectDefinition


@dataclass(frozen=True, slots=True)
class AnimationDefinition:
    """All metadata required to instantiate and select one visual entry."""

    name: str
    factory: Callable[[], Animation]
    frame_ms: int
    frames_per_cycle: int
    automatic: bool
    metadata: AnimationMetadata | None = None
    display_name: str | None = None
    role: AnimationRole | None = None
    dynamic_metadata: AnimationMetadata | None = None
    effect_metadata: EffectDefinition | None = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            return
        if self.display_name is None:
            object.__setattr__(self, "display_name", self.name.replace("_", " ").title())
        if self.role is None:
            object.__setattr__(self, "role", self.metadata.role)
        if self.dynamic_metadata is None:
            object.__setattr__(self, "dynamic_metadata", self.metadata)

    def create(self) -> Animation:
        return self.factory()


def _effect_definitions() -> dict[str, EffectDefinition]:
    from ..effects.definitions import ACCENT_EFFECTS, RHYTHMIC_EFFECTS

    return {
        definition.name: definition
        for definition in (*RHYTHMIC_EFFECTS, *ACCENT_EFFECTS)
    }


def _entry(
    factory: Callable[[], Animation],
    frame_ms: int,
    frames_per_cycle: int,
    *,
    automatic: bool = True,
    effects: dict[str, EffectDefinition],
) -> AnimationDefinition:
    instance = factory()
    metadata = animation_metadata(instance.name)
    return AnimationDefinition(
        name=instance.name,
        factory=factory,
        frame_ms=frame_ms,
        frames_per_cycle=frames_per_cycle,
        automatic=automatic,
        metadata=metadata,
        display_name=instance.name.replace("_", " ").title(),
        role=metadata.role,
        dynamic_metadata=metadata,
        effect_metadata=effects.get(instance.name),
    )


def party_catalog() -> tuple[AnimationDefinition, ...]:
    """Build the canonical party registry in playback order."""

    from ..effects import (
        BassDrop,
        BeatExplosion,
        BeatRipple,
        BeatTunnel,
        BeatWave,
        CenterBurst,
        ClubFlash,
        ColorBurst,
        Confetti,
        DropExplosion,
        DropWave,
        ElectricStorm,
        FireworkBurst,
        HardBeat,
        LightningStrike,
        MirrorFlash,
        PixelExplosion,
        Shockwave,
        SpectrumFlash,
    )
    from .aurora import Aurora
    from .bouncing_ball import BouncingBall
    from .bpm import Bpm
    from .color_wipe import ColorWipe
    from .comet import Comet
    from .comet_storm import CometStorm
    from .dance_floor import DanceFloor
    from .disco_comet import DiscoComet
    from .disco_sparkle import DiscoSparkle
    from .dual_comet import DualComet
    from .dual_laser import DualLaser
    from .fire import Fire
    from .glow_rush import GlowRush
    from .juggle import Juggle
    from .laser_sweep import LaserSweep
    from .neon_confetti import NeonConfetti
    from .neon_storm import NeonStorm
    from .peak_mirror import PeakMirror
    from .plasma_rave import PlasmaRave
    from .police import Police
    from .pulse import Pulse
    from .rainbow import Rainbow
    from .rainbow_cycle import RainbowCycle
    from .rainbow_strobe import RainbowStrobe
    from .rave_pulse import RavePulse
    from .rave_scanner import RaveScanner
    from .red_rave import RedBlackoutStrobe, RedRaveChase, RedRaveSweep
    from .sinelon import Sinelon
    from .strobe import Strobe
    from .strobe_chase import StrobeChase
    from .theater_chase import TheaterChase
    from .twinkle import Twinkle
    from .wave import Wave

    effects = _effect_definitions()
    definitions = (
        _entry(RainbowCycle, 15, 300, effects=effects),
        _entry(Pulse, 20, 180, effects=effects),
        _entry(Confetti, 15, 220, effects=effects),
        _entry(Comet, 16, 240, effects=effects),
        _entry(Shockwave, 16, 180, effects=effects),
        _entry(TheaterChase, 30, 220, effects=effects),
        _entry(Aurora, 18, 260, effects=effects),
        _entry(ColorWipe, 18, 220, effects=effects),
        _entry(Fire, 20, 300, effects=effects),
        _entry(PeakMirror, 18, 220, effects=effects),
        _entry(Wave, 18, 240, effects=effects),
        _entry(Twinkle, 15, 200, effects=effects),
        _entry(BouncingBall, 20, 200, effects=effects),
        _entry(DualComet, 16, 220, effects=effects),
        _entry(Rainbow, 20, 200, effects=effects),
        _entry(Police, 25, 160, effects=effects),
        _entry(Juggle, 18, 240, effects=effects),
        _entry(Sinelon, 18, 200, effects=effects),
        _entry(Strobe, 12, 160, effects=effects),
        _entry(Bpm, 20, 120, effects=effects),
        _entry(BeatWave, 20, 200, effects=effects),
        _entry(DiscoSparkle, 12, 160, effects=effects),
        _entry(BeatExplosion, 16, 140, effects=effects),
        _entry(CometStorm, 14, 180, effects=effects),
        _entry(LaserSweep, 12, 140, effects=effects),
        _entry(PlasmaRave, 18, 220, effects=effects),
        _entry(FireworkBurst, 15, 160, effects=effects),
        _entry(LightningStrike, 10, 100, effects=effects),
        _entry(BeatTunnel, 18, 200, effects=effects),
        _entry(DropExplosion, 20, 180, effects=effects),
        _entry(BassDrop, 20, 200, effects=effects),
        _entry(RavePulse, 14, 160, effects=effects),
        _entry(NeonStorm, 12, 140, effects=effects),
        _entry(PixelExplosion, 14, 140, effects=effects),
        _entry(DualLaser, 14, 160, effects=effects),
        _entry(RainbowStrobe, 10, 120, effects=effects),
        _entry(BeatRipple, 18, 160, effects=effects),
        _entry(DanceFloor, 18, 200, effects=effects),
        _entry(ElectricStorm, 12, 120, effects=effects),
        _entry(GlowRush, 16, 200, effects=effects),
        _entry(HardBeat, 12, 100, effects=effects),
        _entry(ClubFlash, 12, 120, effects=effects),
        _entry(ColorBurst, 16, 180, effects=effects),
        _entry(DiscoComet, 14, 180, effects=effects),
        _entry(RaveScanner, 12, 140, effects=effects),
        _entry(RedRaveSweep, 16, 180, effects=effects),
        _entry(RedRaveChase, 16, 160, effects=effects),
        _entry(RedBlackoutStrobe, 24, 120, effects=effects),
        _entry(NeonConfetti, 12, 150, effects=effects),
        _entry(StrobeChase, 12, 120, effects=effects),
        _entry(CenterBurst, 16, 160, effects=effects),
        _entry(MirrorFlash, 14, 140, effects=effects),
        _entry(SpectrumFlash, 16, 180, effects=effects),
        _entry(DropWave, 18, 180, effects=effects),
    )
    return validate_animation_catalog(definitions)


def _definition_from_entry(entry: Any) -> AnimationDefinition:
    if isinstance(entry.definition, AnimationDefinition):
        return entry.definition
    metadata = animation_metadata(entry.animation)
    return AnimationDefinition(
        name=entry.animation.name,
        factory=type(entry.animation),
        frame_ms=entry.frame_ms,
        frames_per_cycle=entry.frames_per_cycle,
        automatic=entry.automatic,
        metadata=metadata,
        display_name=entry.animation.name.replace("_", " ").title(),
        role=metadata.role,
        dynamic_metadata=metadata,
        effect_metadata=_effect_definitions().get(entry.animation.name),
    )


def animation_catalog(
    player: AnimationPlayer | None = None,
) -> tuple[AnimationDefinition, ...]:
    """Return the canonical catalog or a validated custom player's entries."""

    if player is None:
        return party_catalog()
    return validate_animation_catalog(
        _definition_from_entry(entry) for entry in player.animations
    )


def validate_animation_catalog(
    definitions: Iterable[AnimationDefinition],
) -> tuple[AnimationDefinition, ...]:
    normalized = tuple(definitions)
    names = [definition.name for definition in normalized]
    display_names = [definition.display_name for definition in normalized]
    if any(not name for name in names):
        raise ValueError("animation names must not be empty")
    if len(names) != len(set(names)):
        raise ValueError("animation names must be unique")
    if any(not display_name for display_name in display_names):
        raise ValueError("animation display names are required")
    if len(display_names) != len(set(display_names)):
        raise ValueError("animation display names must be unique")
    for definition in normalized:
        if not callable(definition.factory):
            raise TypeError(f"{definition.name} factory is missing")
        if definition.metadata is None:
            raise ValueError(f"{definition.name} metadata is missing")
        if definition.metadata.name != definition.name:
            raise ValueError(f"{definition.name} metadata name does not match")
        if definition.role is None:
            raise ValueError(f"{definition.name} role is missing")
        if definition.dynamic_metadata is None:
            raise ValueError(f"{definition.name} dynamic metadata is missing")
        if definition.frame_ms <= 0:
            raise ValueError(f"{definition.name} frame duration must be positive")
        if definition.frames_per_cycle <= 0:
            raise ValueError(f"{definition.name} cycle length must be positive")
        if definition.effect_metadata is not None and definition.effect_metadata.name != definition.name:
            raise ValueError(f"{definition.name} effect metadata name does not match")
    return normalized


__all__ = [
    "AnimationDefinition",
    "animation_catalog",
    "party_catalog",
    "validate_animation_catalog",
]
