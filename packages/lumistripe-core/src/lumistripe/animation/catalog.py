"""Validated animation catalog derived from the party player registry."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..selector import AnimationMetadata, animation_metadata
from .base import Animation, AnimationPlayer


@dataclass(frozen=True, slots=True)
class AnimationDefinition:
    """All runtime metadata needed to expose an animation to an application."""

    name: str
    factory: Callable[[], Animation]
    frame_ms: int
    frames_per_cycle: int
    automatic: bool
    metadata: AnimationMetadata

    def create(self) -> Animation:
        return self.factory()


def animation_catalog(player: AnimationPlayer | None = None) -> tuple[AnimationDefinition, ...]:
    """Return the registered animation definitions in playback order."""

    source = player or AnimationPlayer.party()
    definitions = tuple(
        AnimationDefinition(
            name=entry.animation.name,
            factory=type(entry.animation),
            frame_ms=entry.frame_ms,
            frames_per_cycle=entry.frames_per_cycle,
            automatic=entry.automatic,
            metadata=animation_metadata(entry.animation),
        )
        for entry in source.animations
    )
    validate_animation_catalog(definitions)
    return definitions


def validate_animation_catalog(
    definitions: Iterable[AnimationDefinition],
) -> tuple[AnimationDefinition, ...]:
    """Validate names and timing once at an application boundary."""

    normalized = tuple(definitions)
    names = [definition.name for definition in normalized]
    if any(not name for name in names):
        raise ValueError("animation names must not be empty")
    if len(names) != len(set(names)):
        raise ValueError("animation names must be unique")
    for definition in normalized:
        if definition.frame_ms <= 0:
            raise ValueError(f"{definition.name} frame duration must be positive")
        if definition.frames_per_cycle <= 0:
            raise ValueError(f"{definition.name} cycle length must be positive")
    return normalized


__all__ = [
    "AnimationDefinition",
    "animation_catalog",
    "validate_animation_catalog",
]
