from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..audio.types import AudioFrame
from ..controller import BrightnessController, Controller


class Animation(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def tick(self, frame: int, controller: Controller) -> None:
        raise NotImplementedError

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        self.tick(frame, controller)

    def reset(self) -> None:
        """Reset runtime state before this animation is activated again."""

    @property
    def metadata(self):
        from ..selector import animation_metadata

        return animation_metadata(self.name)


@dataclass(slots=True)
class _AnimationEntry:
    animation: Animation
    frame_ms: int
    frames_per_cycle: int
    automatic: bool
    definition: Any = None

    def fresh_animation(self) -> Animation:
        animation_type: type[Animation] = type(self.animation)
        try:
            return animation_type()
        except TypeError:
            self.animation.reset()
            return self.animation

    def restart(self) -> None:
        self.animation = self.fresh_animation()


@dataclass(slots=True)
class AnimationPlayer:
    animations: list[_AnimationEntry] = field(default_factory=list)
    index: int = 0
    frame: int = 0
    brightness: float = 1.0
    audio_enabled: bool = True
    transition_ms: int = 0
    _audio_snapshot: Callable[[], AudioFrame] | None = None
    _transition_duration_ms: int = 0
    _transition_elapsed_ms: int = 0
    _transition_source: Any = None
    _transition_buffer: Any = None

    def add(self, animation: Animation, frame_ms: int, frames_per_cycle: int) -> None:
        self.animations.append(
            _AnimationEntry(animation, frame_ms, frames_per_cycle, True)
        )

    def add_utility(
        self, animation: Animation, frame_ms: int, frames_per_cycle: int
    ) -> None:
        self.animations.append(
            _AnimationEntry(animation, frame_ms, frames_per_cycle, False)
        )

    @classmethod
    def party(cls) -> AnimationPlayer:
        from .catalog import party_catalog

        return cls.from_catalog(party_catalog())

    @classmethod
    def from_catalog(cls, definitions: tuple[Any, ...]) -> AnimationPlayer:
        player = cls()
        for definition in definitions:
            animation = definition.create()
            player.animations.append(
                _AnimationEntry(
                    animation,
                    definition.frame_ms,
                    definition.frames_per_cycle,
                    definition.automatic,
                    definition,
                )
            )
        return player

    def set_brightness(self, brightness: float) -> None:
        self.brightness = max(0.0, min(1.0, brightness))

    def set_audio_snapshot(self, snapshot: Callable[[], AudioFrame]) -> None:
        self._audio_snapshot = snapshot

    def clear_audio_snapshot(self) -> None:
        self._audio_snapshot = None

    def step(
        self,
        controller: Controller,
        *,
        audio_frame: AudioFrame | None = None,
        flush: bool = True,
    ) -> float:
        if not self.animations:
            return 0.05

        if (
            audio_frame is None
            and self._audio_snapshot is not None
            and self.audio_enabled
        ):
            audio_frame = self._audio_snapshot()
        if not self.audio_enabled:
            audio_frame = None
        entry = self.animations[self.index]
        target = controller
        if self.transition_active:
            from ..stripe import Stripe

            if (
                self._transition_source is None
                or self._transition_source.shape != controller.pixels().shape
            ):
                self._transition_source = controller.pixels().copy()
            if (
                self._transition_buffer is None
                or self._transition_buffer.length != controller.length
            ):
                self._transition_buffer = Stripe(controller.length)
            target = self._transition_buffer
        bright = BrightnessController(target, self.brightness)
        if audio_frame is None:
            entry.animation.tick(self.frame, bright)
        else:
            entry.animation.tick_audio(self.frame, bright, audio_frame)
        if self.transition_active:
            self._write_transition(controller, entry.frame_ms, flush=flush)
        elif flush:
            controller.flush()
        self.frame += 1
        return entry.frame_ms / 1000.0

    @property
    def transition_active(self) -> bool:
        return self._transition_duration_ms > 0

    @property
    def transition_progress(self) -> float:
        if not self.transition_active:
            return 1.0
        return min(
            1.0,
            self._transition_elapsed_ms / max(self._transition_duration_ms, 1),
        )

    def set_index(
        self,
        index: int,
        *,
        transition_ms: int | None = None,
        restart: bool = True,
    ) -> None:
        next_index = min(max(index, 0), max(len(self.animations) - 1, 0))
        changed = next_index != self.index
        self.index = next_index
        self.frame = 0
        if self.animations and restart:
            self.animations[self.index].restart()
        duration = self.transition_ms if transition_ms is None else transition_ms
        if changed and duration > 0:
            self.begin_transition(duration_ms=duration)
        else:
            self.cancel_transition()

    def next(self) -> None:
        if not self.animations:
            return
        self.set_index((self.index + 1) % len(self.animations))

    def prev(self) -> None:
        if not self.animations:
            return
        self.set_index(len(self.animations) - 1 if self.index == 0 else self.index - 1)

    def begin_transition(self, *, duration_ms: int, source: Any = None) -> None:
        duration_ms = max(int(duration_ms), 0)
        if duration_ms == 0:
            self.cancel_transition()
            return
        self._transition_duration_ms = duration_ms
        self._transition_elapsed_ms = 0
        self._transition_source = (
            None if source is None else np.asarray(source, dtype=np.uint8).copy()
        )
        self._transition_buffer = None

    def cancel_transition(self) -> None:
        self._transition_duration_ms = 0
        self._transition_elapsed_ms = 0
        self._transition_source = None
        self._transition_buffer = None

    def fresh_animation(self, name: str) -> Animation | None:
        index = self.index_of(name)
        if index is None:
            return None
        return self.animations[index].fresh_animation()

    def _write_transition(
        self, controller: Controller, frame_ms: int, *, flush: bool = True
    ) -> None:
        assert self._transition_source is not None
        assert self._transition_buffer is not None
        self._transition_elapsed_ms = min(
            self._transition_elapsed_ms + frame_ms,
            self._transition_duration_ms,
        )
        amount = self._transition_elapsed_ms / max(self._transition_duration_ms, 1)
        source = self._transition_source.astype(np.float32)
        target = self._transition_buffer.pixels().astype(np.float32)
        blended = np.clip(source * (1.0 - amount) + target * amount, 0.0, 255.0).astype(
            np.uint8
        )
        controller.set_pixels(blended)
        if flush:
            controller.flush()
        if amount >= 1.0:
            self.cancel_transition()

    def current_index(self) -> int:
        return self.index

    def automatic_indices(self) -> list[int]:
        return [index for index, entry in enumerate(self.animations) if entry.automatic]

    def index_of(self, name: str) -> int | None:
        for index, entry in enumerate(self.animations):
            if entry.animation.name == name:
                return index
        return None

    def name_at(self, index: int) -> str | None:
        if 0 <= index < len(self.animations):
            return self.animations[index].animation.name
        return None
