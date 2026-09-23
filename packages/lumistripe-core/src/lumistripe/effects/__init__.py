"""Typed effect definitions and lazy concrete effect implementations."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .base import Effect
from .definitions import (
    ACCENT_EFFECTS,
    RHYTHMIC_EFFECTS,
    EffectCategory,
    EffectDefinition,
)
from .layers import (
    BlendMode,
    EffectLayerStatus,
    EffectScheduler,
    EffectSchedulerConfig,
    EffectSchedulerDiagnostics,
    EffectTriggerResult,
    EffectTriggerStatus,
    LayeredRenderer,
)

_LAZY_EFFECTS: dict[str, tuple[str, str]] = {
    "BassDrop": ("lumistripe.effects.bass_drop", "BassDrop"),
    "BeatExplosion": ("lumistripe.effects.beat_explosion", "BeatExplosion"),
    "BeatRipple": ("lumistripe.effects.beat_ripple", "BeatRipple"),
    "BeatTunnel": ("lumistripe.effects.beat_tunnel", "BeatTunnel"),
    "BeatWave": ("lumistripe.effects.beat_wave", "BeatWave"),
    "CenterBurst": ("lumistripe.effects.center_burst", "CenterBurst"),
    "ClubFlash": ("lumistripe.effects.club_flash", "ClubFlash"),
    "ColorBurst": ("lumistripe.effects.color_burst", "ColorBurst"),
    "Confetti": ("lumistripe.effects.confetti", "Confetti"),
    "DropExplosion": ("lumistripe.effects.drop_explosion", "DropExplosion"),
    "DropWave": ("lumistripe.effects.drop_wave", "DropWave"),
    "ElectricStorm": ("lumistripe.effects.electric_storm", "ElectricStorm"),
    "FireworkBurst": ("lumistripe.effects.firework_burst", "FireworkBurst"),
    "HardBeat": ("lumistripe.effects.hard_beat", "HardBeat"),
    "LightningStrike": ("lumistripe.effects.lightning_strike", "LightningStrike"),
    "MirrorFlash": ("lumistripe.effects.mirror_flash", "MirrorFlash"),
    "PixelExplosion": ("lumistripe.effects.pixel_explosion", "PixelExplosion"),
    "Shockwave": ("lumistripe.effects.shockwave", "Shockwave"),
    "SpectrumFlash": ("lumistripe.effects.spectrum_flash", "SpectrumFlash"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EFFECTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(target[0]), target[1])
    globals()[name] = value
    return value


__all__ = [
    "ACCENT_EFFECTS",
    "RHYTHMIC_EFFECTS",
    "BlendMode",
    "Effect",
    "EffectCategory",
    "EffectDefinition",
    "EffectLayerStatus",
    "EffectScheduler",
    "EffectSchedulerConfig",
    "EffectSchedulerDiagnostics",
    "EffectTriggerResult",
    "EffectTriggerStatus",
    "LayeredRenderer",
]
__all__.extend(_LAZY_EFFECTS)
