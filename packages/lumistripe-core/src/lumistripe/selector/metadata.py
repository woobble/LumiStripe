from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class AnimationRole(str, Enum):
    BASE = "base"
    EFFECT = "effect"


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
    role: AnimationRole = AnimationRole.BASE
    dynamic_safe: bool = True


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

# The original catalog predates Dynamic mode and only described its first entries.
# Keep the rest explicit so selection never silently falls back to generic metadata.
ANIMATION_METADATA.update(
    {
        "rainbow": _meta(
            "rainbow",
            {"colorful", "smooth"},
            energy_min=0.1,
            energy_max=0.8,
            prefers_mid=0.3,
            mood="groovy",
            intensity=0.45,
        ),
        "police": _meta(
            "police",
            {"flash", "utility"},
            energy_min=0.6,
            prefers_mid=0.3,
            mood="intense",
            intensity=0.95,
        ),
        "juggle": _meta(
            "juggle",
            {"motion", "playful"},
            energy_min=0.25,
            energy_max=0.85,
            prefers_mid=0.45,
            mood="groovy",
            intensity=0.55,
        ),
        "sinelon": _meta(
            "sinelon",
            {"motion", "smooth"},
            energy_min=0.1,
            energy_max=0.7,
            prefers_mid=0.35,
            mood="calm",
            intensity=0.4,
        ),
        "strobe": _meta(
            "strobe",
            {"flash", "strobe"},
            energy_min=0.75,
            prefers_treble=0.6,
            mood="intense",
            intensity=1.0,
        ),
        "bpm": _meta(
            "bpm",
            {"beat", "pulse"},
            energy_min=0.2,
            energy_max=0.8,
            prefers_bass=0.55,
            mood="groovy",
            intensity=0.5,
        ),
        "beat_wave": _meta(
            "beat_wave",
            {"beat", "wave", "overlay"},
            energy_min=0.25,
            prefers_bass=0.7,
            mood="bass_heavy",
            intensity=0.65,
        ),
        "disco_sparkle": _meta(
            "disco_sparkle",
            {"sparkle", "treble"},
            energy_min=0.35,
            prefers_treble=0.75,
            mood="fast_party",
            intensity=0.7,
        ),
        "beat_explosion": _meta(
            "beat_explosion",
            {"beat", "impact", "overlay"},
            energy_min=0.45,
            prefers_bass=0.8,
            mood="hard_drop",
            intensity=0.8,
        ),
        "comet_storm": _meta(
            "comet_storm",
            {"motion", "comet"},
            energy_min=0.4,
            prefers_mid=0.5,
            mood="fast_party",
            intensity=0.7,
        ),
        "laser_sweep": _meta(
            "laser_sweep",
            {"laser", "motion"},
            energy_min=0.35,
            prefers_treble=0.4,
            mood="club",
            intensity=0.65,
        ),
        "plasma_rave": _meta(
            "plasma_rave",
            {"plasma", "smooth"},
            energy_min=0.3,
            prefers_mid=0.55,
            mood="club",
            intensity=0.65,
        ),
        "firework_burst": _meta(
            "firework_burst",
            {"burst", "treble", "overlay"},
            energy_min=0.5,
            prefers_treble=0.7,
            mood="fast_party",
            intensity=0.8,
        ),
        "lightning_strike": _meta(
            "lightning_strike",
            {"impact", "treble", "overlay"},
            energy_min=0.55,
            prefers_treble=0.8,
            mood="intense",
            intensity=0.9,
        ),
        "beat_tunnel": _meta(
            "beat_tunnel",
            {"beat", "ring", "overlay"},
            energy_min=0.4,
            prefers_mid=0.55,
            mood="club",
            intensity=0.7,
        ),
        "drop_explosion": _meta(
            "drop_explosion",
            {"drop", "impact", "overlay"},
            energy_min=0.65,
            prefers_bass=0.9,
            supports_drops=True,
            mood="hard_drop",
            intensity=1.0,
        ),
        "bass_drop": _meta(
            "bass_drop",
            {"drop", "bass", "overlay"},
            energy_min=0.55,
            prefers_bass=1.0,
            supports_drops=True,
            mood="hard_drop",
            intensity=0.95,
        ),
        "rave_pulse": _meta(
            "rave_pulse",
            {"pulse", "bass"},
            energy_min=0.45,
            prefers_bass=0.7,
            mood="club",
            intensity=0.75,
        ),
        "neon_storm": _meta(
            "neon_storm",
            {"neon", "motion"},
            energy_min=0.45,
            prefers_treble=0.55,
            mood="fast_party",
            intensity=0.75,
        ),
        "pixel_explosion": _meta(
            "pixel_explosion",
            {"burst", "overlay"},
            energy_min=0.5,
            prefers_mid=0.5,
            supports_drops=True,
            mood="fast_party",
            intensity=0.85,
        ),
        "dual_laser": _meta(
            "dual_laser",
            {"laser", "motion"},
            energy_min=0.45,
            prefers_treble=0.5,
            mood="club",
            intensity=0.75,
        ),
        "rainbow_strobe": _meta(
            "rainbow_strobe",
            {"flash", "strobe"},
            energy_min=0.75,
            prefers_treble=0.7,
            mood="intense",
            intensity=1.0,
        ),
        "beat_ripple": _meta(
            "beat_ripple",
            {"beat", "ring", "overlay"},
            energy_min=0.3,
            prefers_bass=0.7,
            mood="bass_heavy",
            intensity=0.65,
        ),
        "dance_floor": _meta(
            "dance_floor",
            {"rhythmic", "segments"},
            energy_min=0.35,
            prefers_mid=0.6,
            mood="groovy",
            intensity=0.65,
        ),
        "electric_storm": _meta(
            "electric_storm",
            {"storm", "treble", "overlay"},
            energy_min=0.55,
            prefers_treble=0.8,
            mood="intense",
            intensity=0.85,
        ),
        "glow_rush": _meta(
            "glow_rush",
            {"motion", "glow"},
            energy_min=0.35,
            prefers_bass=0.45,
            mood="club",
            intensity=0.65,
        ),
        "hard_beat": _meta(
            "hard_beat",
            {"beat", "flash", "overlay"},
            energy_min=0.65,
            prefers_bass=0.9,
            mood="hard_drop",
            intensity=0.9,
        ),
        "club_flash": _meta(
            "club_flash",
            {"flash", "overlay"},
            energy_min=0.55,
            prefers_mid=0.65,
            mood="club",
            intensity=0.8,
        ),
        "color_burst": _meta(
            "color_burst",
            {"burst", "colorful", "overlay"},
            energy_min=0.45,
            prefers_mid=0.55,
            mood="fast_party",
            intensity=0.8,
        ),
        "disco_comet": _meta(
            "disco_comet",
            {"comet", "sparkle"},
            energy_min=0.4,
            prefers_treble=0.5,
            mood="groovy",
            intensity=0.7,
        ),
        "rave_scanner": _meta(
            "rave_scanner",
            {"scanner", "motion"},
            energy_min=0.45,
            prefers_mid=0.55,
            mood="club",
            intensity=0.7,
        ),
        "neon_confetti": _meta(
            "neon_confetti",
            {"neon", "sparkle"},
            energy_min=0.4,
            prefers_treble=0.7,
            mood="fast_party",
            intensity=0.7,
        ),
        "strobe_chase": _meta(
            "strobe_chase",
            {"chase", "flash"},
            energy_min=0.65,
            prefers_treble=0.6,
            mood="intense",
            intensity=0.9,
        ),
        "center_burst": _meta(
            "center_burst",
            {"beat", "burst", "overlay"},
            energy_min=0.4,
            prefers_bass=0.65,
            mood="club",
            intensity=0.75,
        ),
        "mirror_flash": _meta(
            "mirror_flash",
            {"mirror", "flash", "overlay"},
            energy_min=0.5,
            prefers_mid=0.65,
            mood="club",
            intensity=0.8,
        ),
        "spectrum_flash": _meta(
            "spectrum_flash",
            {"spectrum", "flash", "overlay"},
            energy_min=0.4,
            prefers_treble=0.65,
            mood="fast_party",
            intensity=0.75,
        ),
        "drop_wave": _meta(
            "drop_wave",
            {"drop", "wave", "overlay"},
            energy_min=0.55,
            prefers_bass=0.85,
            supports_drops=True,
            mood="hard_drop",
            intensity=0.9,
        ),
    }
)

EFFECT_ANIMATION_NAMES = frozenset(
    {
        "bass_drop",
        "beat_explosion",
        "beat_ripple",
        "beat_tunnel",
        "beat_wave",
        "center_burst",
        "club_flash",
        "color_burst",
        "confetti",
        "drop_explosion",
        "drop_wave",
        "electric_storm",
        "firework_burst",
        "hard_beat",
        "lightning_strike",
        "mirror_flash",
        "pixel_explosion",
        "shockwave",
        "spectrum_flash",
    }
)

DYNAMIC_UNSAFE_NAMES = frozenset({"police", "rainbow_strobe", "strobe"})


def default_metadata(name: str) -> AnimationMetadata:
    return AnimationMetadata(name=name, tags=frozenset({"general"}))


def animation_metadata(animation_or_name: Any) -> AnimationMetadata:
    name = (
        animation_or_name
        if isinstance(animation_or_name, str)
        else getattr(animation_or_name, "name", "")
    )
    declared = (
        None
        if isinstance(animation_or_name, str)
        else getattr(animation_or_name, "metadata", None)
    )
    metadata = (
        declared
        if isinstance(declared, AnimationMetadata)
        else ANIMATION_METADATA.get(str(name), default_metadata(str(name)))
    )
    role = (
        AnimationRole.EFFECT if str(name) in EFFECT_ANIMATION_NAMES else metadata.role
    )
    dynamic_safe = metadata.dynamic_safe and str(name) not in DYNAMIC_UNSAFE_NAMES
    if role is metadata.role and dynamic_safe == metadata.dynamic_safe:
        return metadata
    return replace(metadata, role=role, dynamic_safe=dynamic_safe)
