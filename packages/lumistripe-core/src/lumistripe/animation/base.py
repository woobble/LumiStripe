from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from math import ceil, floor
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


class TransitionEffect(str, Enum):
    CROSSFADE = "crossfade"
    FADE_BLACK = "fade_black"
    WIPE = "wipe"


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
    transition_effect: TransitionEffect = field(
        default=TransitionEffect.CROSSFADE, kw_only=True
    )
    _audio_snapshot: Callable[[], AudioFrame] | None = None
    _transition_duration_ms: int = 0
    _transition_started_at_s: float | None = None
    _active_transition_effect: TransitionEffect | None = None
    _transition_source: Any = None
    _transition_buffer: Any = None
    _transition_source_float: Any = None
    _transition_target_float: Any = None
    _transition_work_float: Any = None
    _transition_output: Any = None

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
        now_s: float | None = None,
        transition_external: bool = False,
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
        if self.transition_active and not transition_external:
            from ..stripe import Stripe

            if self._transition_source is None:
                self.capture_transition_source(controller.pixels())
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
        if self.transition_active and not transition_external:
            assert self._transition_buffer is not None
            self.apply_transition(
                controller,
                now_s=now_s,
                flush=flush,
                target_pixels=self._transition_buffer.pixels(),
            )
        elif flush:
            controller.flush()
        self.frame += 1
        return entry.frame_ms / 1000.0

    @property
    def transition_active(self) -> bool:
        return self._transition_duration_ms > 0

    @property
    def transition_progress(self) -> float:
        return self.transition_progress_at()

    def transition_progress_at(self, now_s: float | None = None) -> float:
        if not self.transition_active:
            return 1.0
        started_at = self._transition_started_at_s
        if started_at is None:
            return 0.0
        now = time.monotonic() if now_s is None else now_s
        return min(
            1.0,
            max(0.0, now - started_at) * 1000.0
            / max(self._transition_duration_ms, 1),
        )

    def set_index(
        self,
        index: int,
        *,
        transition_ms: int | None = None,
        restart: bool = True,
        now_s: float | None = None,
    ) -> None:
        next_index = min(max(index, 0), max(len(self.animations) - 1, 0))
        changed = next_index != self.index
        self.index = next_index
        self.frame = 0
        if self.animations and restart:
            self.animations[self.index].restart()
        duration = self.transition_ms if transition_ms is None else transition_ms
        if changed and duration > 0:
            self.begin_transition(duration_ms=duration, now_s=now_s)
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

    def begin_transition(
        self,
        *,
        duration_ms: int,
        source: Any = None,
        now_s: float | None = None,
        effect: TransitionEffect | None = None,
    ) -> None:
        duration_ms = max(int(duration_ms), 0)
        if duration_ms == 0:
            self.cancel_transition()
            return
        self._transition_duration_ms = duration_ms
        self._transition_started_at_s = time.monotonic() if now_s is None else now_s
        self._active_transition_effect = (
            self.transition_effect if effect is None else TransitionEffect(effect)
        )
        self._transition_source = None
        if source is not None:
            self.capture_transition_source(source)

    def cancel_transition(self) -> None:
        self._transition_duration_ms = 0
        self._transition_started_at_s = None
        self._active_transition_effect = None
        self._transition_source = None

    def capture_transition_source(self, source: Any) -> None:
        pixels = np.asarray(source, dtype=np.uint8)
        if self._transition_source is None or self._transition_source.shape != pixels.shape:
            self._transition_source = np.empty_like(pixels)
        np.copyto(self._transition_source, pixels)

    def ensure_transition_source(self, source: Any) -> None:
        if self._transition_source is None:
            self.capture_transition_source(source)

    def apply_transition(
        self,
        controller: Controller,
        *,
        now_s: float | None = None,
        flush: bool = False,
        target_pixels: Any = None,
    ) -> None:
        if not self.transition_active:
            if flush:
                controller.flush()
            return
        target = controller.pixels() if target_pixels is None else target_pixels
        if self._transition_source is None or self._transition_source.shape != target.shape:
            self.capture_transition_source(target)
        assert self._transition_source is not None
        shape = target.shape
        if self._transition_output is None or self._transition_output.shape != shape:
            self._transition_output = np.empty(shape, dtype=np.uint8)
        amount = self.transition_progress_at(now_s)
        effect = self._active_transition_effect or self.transition_effect

        if effect is TransitionEffect.WIPE:
            self._apply_wipe_transition(target, amount)
            controller.set_pixels(self._transition_output)
            if flush:
                controller.flush()
            if amount >= 1.0:
                self.cancel_transition()
            return

        if (
            self._transition_source_float is None
            or self._transition_source_float.shape != shape
        ):
            self._transition_source_float = np.empty(shape, dtype=np.float32)
            self._transition_target_float = np.empty(shape, dtype=np.float32)
            self._transition_work_float = np.empty(shape, dtype=np.float32)
        np.copyto(self._transition_source_float, self._transition_source)
        np.copyto(self._transition_target_float, target)
        if effect is TransitionEffect.FADE_BLACK:
            if amount <= 0.5:
                frame = self._transition_source_float
                brightness = 1.0 - amount * 2.0
            else:
                frame = self._transition_target_float
                brightness = (amount - 0.5) * 2.0
            np.multiply(frame, brightness, out=self._transition_work_float)
            self._transition_work_float[:, 3] = frame[:, 3]
        else:
            np.multiply(self._transition_source_float, 1.0 - amount, out=self._transition_work_float)
            np.multiply(self._transition_target_float, amount, out=self._transition_target_float)
            np.add(
                self._transition_work_float,
                self._transition_target_float,
                out=self._transition_work_float,
            )
        np.clip(self._transition_work_float, 0.0, 255.0, out=self._transition_work_float)
        np.copyto(self._transition_output, self._transition_work_float, casting="unsafe")
        controller.set_pixels(self._transition_output)
        if flush:
            controller.flush()
        if amount >= 1.0:
            self.cancel_transition()

    def _apply_wipe_transition(self, target: Any, amount: float) -> None:
        assert self._transition_source is not None
        assert self._transition_output is not None
        if amount <= 0.0:
            np.copyto(self._transition_output, self._transition_source)
            return
        if amount >= 1.0:
            np.copyto(self._transition_output, target)
            return

        length = target.shape[0]
        feather = max(1, min(8, round(length * 0.02)))
        boundary = amount * length
        feather_start = boundary - feather / 2.0
        feather_end = boundary + feather / 2.0
        blend_start = max(0, min(length, floor(feather_start - 0.5) + 1))
        blend_end = max(
            blend_start,
            min(length, ceil(feather_end - 0.5)),
        )

        np.copyto(self._transition_output, self._transition_source)
        np.copyto(self._transition_output[:blend_start], target[:blend_start])
        for pixel in range(blend_start, blend_end):
            incoming_weight = min(
                1.0,
                max(0.0, (feather_end - (pixel + 0.5)) / feather),
            )
            outgoing_weight = 1.0 - incoming_weight
            for channel in range(target.shape[1]):
                self._transition_output[pixel, channel] = int(
                    self._transition_source[pixel, channel] * outgoing_weight
                    + target[pixel, channel] * incoming_weight
                )

    def fresh_animation(self, name: str) -> Animation | None:
        index = self.index_of(name)
        if index is None:
            return None
        return self.animations[index].fresh_animation()

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
