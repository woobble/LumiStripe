"""Lazy animation package surface and canonical catalog entry points."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from ..selector import AnimationMetadata, AnimationRole
from .base import Animation, AnimationPlayer
from .catalog import (
    AnimationDefinition,
    animation_catalog,
    party_catalog,
    validate_animation_catalog,
)

_LAZY_ANIMATIONS: dict[str, tuple[str, str]] = {
    "AudioReactive": ("lumistripe.animation.reactive", "AudioReactive"),
    "Decay": ("lumistripe.animation.reactive", "Decay"),
    "Aurora": ("lumistripe.animation.aurora", "Aurora"),
    "BouncingBall": ("lumistripe.animation.bouncing_ball", "BouncingBall"),
    "Bpm": ("lumistripe.animation.bpm", "Bpm"),
    "ColorWipe": ("lumistripe.animation.color_wipe", "ColorWipe"),
    "Comet": ("lumistripe.animation.comet", "Comet"),
    "CometStorm": ("lumistripe.animation.comet_storm", "CometStorm"),
    "DanceFloor": ("lumistripe.animation.dance_floor", "DanceFloor"),
    "DiscoComet": ("lumistripe.animation.disco_comet", "DiscoComet"),
    "DiscoSparkle": ("lumistripe.animation.disco_sparkle", "DiscoSparkle"),
    "DualComet": ("lumistripe.animation.dual_comet", "DualComet"),
    "DualLaser": ("lumistripe.animation.dual_laser", "DualLaser"),
    "Fire": ("lumistripe.animation.fire", "Fire"),
    "GlowRush": ("lumistripe.animation.glow_rush", "GlowRush"),
    "Juggle": ("lumistripe.animation.juggle", "Juggle"),
    "LaserSweep": ("lumistripe.animation.laser_sweep", "LaserSweep"),
    "NeonConfetti": ("lumistripe.animation.neon_confetti", "NeonConfetti"),
    "NeonStorm": ("lumistripe.animation.neon_storm", "NeonStorm"),
    "PeakMirror": ("lumistripe.animation.peak_mirror", "PeakMirror"),
    "PlasmaRave": ("lumistripe.animation.plasma_rave", "PlasmaRave"),
    "Police": ("lumistripe.animation.police", "Police"),
    "Pulse": ("lumistripe.animation.pulse", "Pulse"),
    "Rainbow": ("lumistripe.animation.rainbow", "Rainbow"),
    "RainbowCycle": ("lumistripe.animation.rainbow_cycle", "RainbowCycle"),
    "RainbowStrobe": ("lumistripe.animation.rainbow_strobe", "RainbowStrobe"),
    "RavePulse": ("lumistripe.animation.rave_pulse", "RavePulse"),
    "RaveScanner": ("lumistripe.animation.rave_scanner", "RaveScanner"),
    "RedBlackoutStrobe": ("lumistripe.animation.red_rave", "RedBlackoutStrobe"),
    "RedRaveChase": ("lumistripe.animation.red_rave", "RedRaveChase"),
    "RedRaveSweep": ("lumistripe.animation.red_rave", "RedRaveSweep"),
    "RgbwTest": ("lumistripe.animation.rgbw_test", "RgbwTest"),
    "Sinelon": ("lumistripe.animation.sinelon", "Sinelon"),
    "Strobe": ("lumistripe.animation.strobe", "Strobe"),
    "StrobeChase": ("lumistripe.animation.strobe_chase", "StrobeChase"),
    "TheaterChase": ("lumistripe.animation.theater_chase", "TheaterChase"),
    "Twinkle": ("lumistripe.animation.twinkle", "Twinkle"),
    "Wave": ("lumistripe.animation.wave", "Wave"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ANIMATIONS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(target[0]), target[1])
    globals()[name] = value
    return value


__all__ = [
    "Animation",
    "AnimationDefinition",
    "AnimationMetadata",
    "AnimationPlayer",
    "AnimationRole",
    "animation_catalog",
    "party_catalog",
    "validate_animation_catalog",
]
__all__.extend(_LAZY_ANIMATIONS)
