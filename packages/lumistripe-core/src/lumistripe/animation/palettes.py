"""Shared palette sampling for configurable animation examples."""

from __future__ import annotations

from ..color import Color, Hsla, Rgba

_NEON_HUES = (300, 160, 52, 206, 12, 268, 132)
_FIRE_HUES = (0, 10, 22, 35, 48)


def palette_color(
    palette: str,
    hue: float,
    *,
    alpha: float,
    custom_color: str,
    lightness: int = 56,
    saturation: int = 100,
) -> Color:
    """Return a color for a palette at the requested hue position."""

    if palette == "custom":
        value = int(custom_color[1:], 16)
        return Rgba(
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
            max(0.0, min(1.0, alpha)),
        )
    selected_hue: float
    if palette == "neon":
        selected_hue = _NEON_HUES[int(hue // 45.0) % len(_NEON_HUES)]
    elif palette == "fire":
        selected_hue = _FIRE_HUES[int(hue // 55.0) % len(_FIRE_HUES)]
    else:
        selected_hue = hue % 360.0
    return Hsla(selected_hue, saturation, lightness, max(0.0, min(1.0, alpha)))


__all__ = ["palette_color"]
