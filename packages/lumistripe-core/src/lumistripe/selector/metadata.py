from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AnimationMetadata:
    name: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)
    energy_min: float = 0.0
    energy_max: float = 1.0
    bpm_min: float = 0.0
    bpm_max: float = 260.0
    prefers_bass: float = 0.0
    prefers_mid: float = 0.0
    prefers_treble: float = 0.0
    supports_beats: bool = True
    supports_drops: bool = False
    supports_silence: bool = False
    mood: str = "general"
    intensity: float = 0.5
    weight: float = 1.0


def _meta(
    name: str,
    tags: set[str],
    *,
    energy_min: float = 0.0,
    energy_max: float = 1.0,
    bpm_min: float = 0.0,
    bpm_max: float = 260.0,
    prefers_bass: float = 0.0,
    prefers_mid: float = 0.0,
    prefers_treble: float = 0.0,
    supports_beats: bool = True,
    supports_drops: bool = False,
    supports_silence: bool = False,
    mood: str = "general",
    intensity: float = 0.5,
    weight: float = 1.0,
) -> AnimationMetadata:
    return AnimationMetadata(
        name=name,
        tags=frozenset(tags),
        energy_min=energy_min,
        energy_max=energy_max,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
        prefers_bass=prefers_bass,
        prefers_mid=prefers_mid,
        prefers_treble=prefers_treble,
        supports_beats=supports_beats,
        supports_drops=supports_drops,
        supports_silence=supports_silence,
        mood=mood,
        intensity=intensity,
        weight=weight,
    )


ANIMATION_METADATA: dict[str, AnimationMetadata] = {
    "rainbow_cycle": _meta(
        "rainbow_cycle",
        {"colorful", "smooth", "medium_energy"},
        energy_min=0.25,
        energy_max=0.9,
        bpm_min=90,
        bpm_max=180,
        prefers_mid=0.35,
        prefers_treble=0.35,
        mood="groovy",
        intensity=0.65,
    ),
    "pulse": _meta(
        "pulse",
        {"beat", "bass", "simple"},
        energy_min=0.18,
        energy_max=0.85,
        bpm_min=70,
        bpm_max=160,
        prefers_bass=0.8,
        supports_beats=True,
        mood="bass_heavy",
        intensity=0.55,
        weight=1.1,
    ),
    "confetti": _meta(
        "confetti",
        {"sparkle", "party", "chaotic"},
        energy_min=0.45,
        energy_max=1.0,
        bpm_min=105,
        bpm_max=190,
        prefers_treble=0.75,
        supports_beats=True,
        mood="fast_party",
        intensity=0.8,
    ),
    "comet": _meta(
        "comet",
        {"motion", "clean", "medium_energy"},
        energy_min=0.25,
        energy_max=0.85,
        bpm_min=80,
        bpm_max=170,
        prefers_mid=0.45,
        mood="groovy",
        intensity=0.55,
    ),
    "shockwave": _meta(
        "shockwave",
        {"drop", "impact", "bass"},
        energy_min=0.4,
        energy_max=1.0,
        bpm_min=70,
        bpm_max=180,
        prefers_bass=0.9,
        supports_drops=True,
        mood="hard_drop",
        intensity=0.9,
        weight=1.15,
    ),
    "theater_chase": _meta(
        "theater_chase",
        {"rhythmic", "classic", "medium_energy"},
        energy_min=0.25,
        energy_max=0.8,
        bpm_min=80,
        bpm_max=165,
        prefers_mid=0.5,
        supports_beats=True,
        mood="groovy",
        intensity=0.55,
    ),
    "aurora": _meta(
        "aurora",
        {"ambient", "smooth", "cool"},
        energy_min=0.0,
        energy_max=0.35,
        supports_beats=False,
        supports_silence=True,
        mood="ambient",
        intensity=0.2,
        weight=1.1,
    ),
    "color_wipe": _meta(
        "color_wipe",
        {"clean", "simple", "medium_energy"},
        energy_min=0.15,
        energy_max=0.75,
        bpm_min=60,
        bpm_max=150,
        prefers_mid=0.35,
        mood="vocal_pop",
        intensity=0.45,
    ),
    "fire": _meta(
        "fire",
        {"warm", "organic", "medium_energy"},
        energy_min=0.25,
        energy_max=0.9,
        bpm_min=80,
        bpm_max=150,
        prefers_bass=0.6,
        prefers_treble=0.2,
        mood="chaotic",
        intensity=0.7,
    ),
    "peak_mirror": _meta(
        "peak_mirror",
        {"mirror", "vocal", "spectrum"},
        energy_min=0.2,
        energy_max=0.85,
        bpm_min=70,
        bpm_max=170,
        prefers_mid=0.75,
        prefers_treble=0.35,
        supports_beats=True,
        mood="vocal_pop",
        intensity=0.6,
    ),
    "wave": _meta(
        "wave",
        {"smooth", "calm", "organic"},
        energy_min=0.0,
        energy_max=0.55,
        bpm_min=50,
        bpm_max=140,
        prefers_mid=0.3,
        supports_beats=False,
        supports_silence=True,
        mood="calm",
        intensity=0.3,
    ),
    "twinkle": _meta(
        "twinkle",
        {"ambient", "sparkle", "quiet"},
        energy_min=0.0,
        energy_max=0.45,
        prefers_treble=0.45,
        supports_beats=False,
        supports_silence=True,
        mood="ambient",
        intensity=0.25,
    ),
    "bouncing_ball": _meta(
        "bouncing_ball",
        {"playful", "motion", "calm"},
        energy_min=0.05,
        energy_max=0.55,
        bpm_min=55,
        bpm_max=135,
        prefers_bass=0.25,
        mood="calm",
        intensity=0.35,
    ),
    "dual_comet": _meta(
        "dual_comet",
        {"motion", "vocal", "fast"},
        energy_min=0.3,
        energy_max=0.95,
        bpm_min=90,
        bpm_max=190,
        prefers_mid=0.55,
        prefers_treble=0.35,
        mood="vocal_pop",
        intensity=0.7,
    ),
}


def default_metadata(name: str) -> AnimationMetadata:
    return AnimationMetadata(name=name, tags=frozenset({"general"}))


def animation_metadata(animation_or_name: Any) -> AnimationMetadata:
    name = animation_or_name if isinstance(animation_or_name, str) else getattr(animation_or_name, "name", "")
    declared = None if isinstance(animation_or_name, str) else getattr(animation_or_name, "metadata", None)
    if isinstance(declared, AnimationMetadata):
        return declared
    return ANIMATION_METADATA.get(str(name), default_metadata(str(name)))
