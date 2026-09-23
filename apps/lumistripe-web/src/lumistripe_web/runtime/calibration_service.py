"""Pure color-calibration policy helpers."""

from __future__ import annotations

from lumistripe import Rgb

CALIBRATION_COLORS: dict[str, Rgb] = {
    "white": Rgb(255, 255, 255),
    "red": Rgb(255, 0, 0),
    "green": Rgb(0, 255, 0),
    "blue": Rgb(0, 0, 255),
}


def calibration_color(pattern: str) -> Rgb:
    try:
        return CALIBRATION_COLORS[pattern]
    except KeyError as exc:
        raise ValueError(f"unknown calibration pattern: {pattern}") from exc


__all__ = ["CALIBRATION_COLORS", "calibration_color"]
