"""Typed, renderer-independent settings metadata for animations."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

AnimationSettingValue: TypeAlias = str | int | float | bool
AnimationSettingValues: TypeAlias = dict[str, AnimationSettingValue]


class AnimationSettingType(str, Enum):
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"
    COLOR = "color"


@dataclass(frozen=True, slots=True)
class AnimationSettingDefinition:
    """One user-facing animation setting and its validation constraints."""

    id: str
    label: str
    type: AnimationSettingType
    default: AnimationSettingValue
    description: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str | None = None
    choices: tuple[str, ...] = ()
    visible_when_option: str | None = None
    visible_when_value: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.label or not self.description:
            raise ValueError("animation setting id, label, and description are required")
        if self.type is AnimationSettingType.NUMBER:
            if self.minimum is None or self.maximum is None or self.step is None:
                raise ValueError(f"numeric setting {self.id!r} needs range and step")
            if self.minimum > self.maximum or self.step <= 0:
                raise ValueError(f"numeric setting {self.id!r} has an invalid range")
        elif self.type is AnimationSettingType.ENUM and not self.choices:
            raise ValueError(f"enum setting {self.id!r} needs choices")


_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_animation_settings(
    definitions: tuple[AnimationSettingDefinition, ...],
    values: object | None,
    *,
    allow_partial: bool = False,
) -> AnimationSettingValues:
    """Validate values against a setting schema and fill omitted defaults."""

    if values is None:
        encoded: dict[str, object] = {}
    elif isinstance(values, dict):
        encoded = values
    else:
        raise ValueError("animation settings must be an object")

    by_id = {definition.id: definition for definition in definitions}
    unknown = set(encoded) - set(by_id)
    if unknown:
        raise ValueError(f"unknown animation setting: {min(unknown)}")
    missing = set(by_id) - set(encoded)
    if missing and not allow_partial:
        raise ValueError(f"missing animation setting: {min(missing)}")

    normalized: AnimationSettingValues = {}
    for definition in definitions:
        value = encoded.get(definition.id, definition.default)
        if definition.type is AnimationSettingType.NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{definition.id} must be a number")
            if not math.isfinite(float(value)):
                raise ValueError(f"{definition.id} must be finite")
            assert definition.minimum is not None and definition.maximum is not None
            if not definition.minimum <= value <= definition.maximum:
                raise ValueError(
                    f"{definition.id} must be between "
                    f"{definition.minimum:g} and {definition.maximum:g}"
                )
            normalized[definition.id] = int(value) if float(value).is_integer() else float(value)
        elif definition.type is AnimationSettingType.BOOLEAN:
            if not isinstance(value, bool):
                raise ValueError(f"{definition.id} must be a boolean")
            normalized[definition.id] = value
        elif definition.type is AnimationSettingType.ENUM:
            if not isinstance(value, str) or value not in definition.choices:
                raise ValueError(
                    f"{definition.id} must be one of {', '.join(definition.choices)}"
                )
            normalized[definition.id] = value
        elif definition.type is AnimationSettingType.COLOR:
            if not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
                raise ValueError(f"{definition.id} must be a six-digit hex color")
            normalized[definition.id] = value.upper()
        else:  # pragma: no cover - exhaustive guard for future schema types
            raise ValueError(f"unsupported animation setting type: {definition.type}")
    return normalized


__all__ = [
    "AnimationSettingDefinition",
    "AnimationSettingType",
    "AnimationSettingValue",
    "AnimationSettingValues",
    "normalize_animation_settings",
]


COMET_SETTING_DEFINITIONS = (
    AnimationSettingDefinition(
        id="speed", label="Speed", type=AnimationSettingType.NUMBER,
        default=65, minimum=0, maximum=100, step=1, unit="%",
        description="How quickly the comet moves along the strip.",
    ),
    AnimationSettingDefinition(
        id="tail_length", label="Tail length", type=AnimationSettingType.NUMBER,
        default=8, minimum=1, maximum=100, step=1, unit="%",
        description="Trail length relative to the strip.",
    ),
    AnimationSettingDefinition(
        id="intensity", label="Intensity", type=AnimationSettingType.NUMBER,
        default=100, minimum=0, maximum=100, step=1, unit="%",
        description="Maximum brightness of the comet and its trail.",
    ),
    AnimationSettingDefinition(
        id="direction", label="Direction", type=AnimationSettingType.ENUM,
        default="bounce", choices=("forward", "reverse", "bounce"),
        description="Travel toward one end, the other end, or bounce between both.",
    ),
    AnimationSettingDefinition(
        id="palette", label="Color palette", type=AnimationSettingType.ENUM,
        default="rainbow", choices=("rainbow", "neon", "fire", "custom"),
        description="Choose a built-in palette or use one custom color.",
    ),
    AnimationSettingDefinition(
        id="custom_color", label="Custom color", type=AnimationSettingType.COLOR,
        default="#7C3AED", description="Color used when the palette is Custom.",
        visible_when_option="palette", visible_when_value="custom",
    ),
    AnimationSettingDefinition(
        id="music_reactivity", label="Music reactivity",
        type=AnimationSettingType.NUMBER, default=70, minimum=0, maximum=100,
        step=1, unit="%", description="How strongly audio changes speed, trail, and shimmer.",
    ),
)

PULSE_SETTING_DEFINITIONS = (
    AnimationSettingDefinition(
        id="speed", label="Speed", type=AnimationSettingType.NUMBER,
        default=50, minimum=0, maximum=100, step=1, unit="%",
        description="How quickly the pulse breathes.",
    ),
    AnimationSettingDefinition(
        id="minimum_intensity", label="Minimum intensity",
        type=AnimationSettingType.NUMBER, default=22, minimum=0, maximum=100,
        step=1, unit="%", description="Brightness at the dimmest point of each pulse.",
    ),
    AnimationSettingDefinition(
        id="maximum_intensity", label="Maximum intensity",
        type=AnimationSettingType.NUMBER, default=100, minimum=0, maximum=100,
        step=1, unit="%", description="Brightness at the brightest point of each pulse.",
    ),
    AnimationSettingDefinition(
        id="palette", label="Color palette", type=AnimationSettingType.ENUM,
        default="rainbow", choices=("rainbow", "neon", "fire", "custom"),
        description="Choose a built-in palette or use one custom color.",
    ),
    AnimationSettingDefinition(
        id="custom_color", label="Custom color", type=AnimationSettingType.COLOR,
        default="#7C3AED", description="Color used when the palette is Custom.",
        visible_when_option="palette", visible_when_value="custom",
    ),
    AnimationSettingDefinition(
        id="pulse_curve", label="Pulse curve", type=AnimationSettingType.ENUM,
        default="sine", choices=("sine", "smoothstep", "sharp"),
        description="Shape of the breathing transition between low and high intensity.",
    ),
    AnimationSettingDefinition(
        id="music_reactivity", label="Music reactivity",
        type=AnimationSettingType.NUMBER, default=70, minimum=0, maximum=100,
        step=1, unit="%", description="How strongly beats and audio energy boost the pulse.",
    ),
)

__all__ += ["COMET_SETTING_DEFINITIONS", "PULSE_SETTING_DEFINITIONS"]
