from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from ..color import Hsla

CLUB_HUES: tuple[int, ...] = (330, 18, 48, 126, 190, 224, 286)
NEON_HUES: tuple[int, ...] = (300, 160, 52, 206, 12, 268, 132)
SPECTRUM_HUES: tuple[int, ...] = (0, 24, 48, 96, 160, 208, 258, 312)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def clamp01(value: float) -> float:
    return _clamp01(value)


def club_color(seed: int, alpha: float = 1.0, lightness: int = 58) -> Hsla:
    return Hsla(CLUB_HUES[seed % len(CLUB_HUES)], 100, lightness, _clamp01(alpha))


def neon_color(seed: int, alpha: float = 1.0, lightness: int = 62) -> Hsla:
    return Hsla(NEON_HUES[seed % len(NEON_HUES)], 100, lightness, _clamp01(alpha))


def spectrum_color(seed: int, alpha: float = 1.0, lightness: int = 56) -> Hsla:
    return Hsla(SPECTRUM_HUES[seed % len(SPECTRUM_HUES)], 100, lightness, _clamp01(alpha))


def warm_flash(seed: int, alpha: float = 1.0) -> Hsla:
    hues = (0, 20, 38, 320)
    return Hsla(hues[seed % len(hues)], 100, 60, _clamp01(alpha))


def strip_ratio(index: int, length: int) -> float:
    if length <= 1:
        return 0.0
    return index / float(length - 1)


def center_distance(index: int, length: int) -> float:
    if length <= 1:
        return 0.0
    return abs(index - (length - 1) / 2.0)


def mirrored_index(index: int, length: int) -> int:
    return max(length - 1 - index, 0)


def ring_profile(distance: float, radius: float, width: float) -> float:
    width = max(width, 0.001)
    return max(0.0, 1.0 - abs(distance - radius) / width)


def trail_profile(distance: float, width: float) -> float:
    width = max(width, 0.001)
    return max(0.0, 1.0 - distance / width)


def band_alpha(strength: float, base: float = 0.2, scale: float = 0.8) -> float:
    return _clamp01(base + strength * scale)


def burst_profile(distance: float, radius: float, width: float) -> float:
    return ring_profile(distance, radius, width)


def beam_profile(distance: float, width: float) -> float:
    return trail_profile(distance, width)


def palette_sample(palette: Sequence[int], seed: int, *, alpha: float = 1.0, lightness: int = 58) -> Hsla:
    if not palette:
        return Hsla(0, 100, lightness, _clamp01(alpha))
    return Hsla(palette[seed % len(palette)], 100, lightness, _clamp01(alpha))


@dataclass(slots=True)
class BurstState:
    center: float
    radius: float
    speed: float
    hue_seed: int
    strength: float
    width: float
