from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Rgb
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive

RED = Rgb(255, 0, 0)


@dataclass(slots=True)
class RedRaveSweep(Animation):
    """A hard-edged red beam that travels back and forth along the strip."""

    position: float = 0.0
    direction: float = 1.0

    @property
    def name(self) -> str:
        return "red_rave_sweep"

    def tick(self, frame: int, controller: Controller) -> None:
        self._step(controller, drive=0.35, low=0.25, accent=0.0)

    def tick_audio(
        self, frame: int, controller: Controller, audio: AudioFrame
    ) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(
            controller,
            drive=reactive.drive(),
            low=reactive.low,
            accent=reactive.accent if audio.beat else 0.0,
        )

    def _step(
        self,
        controller: Controller,
        *,
        drive: float,
        low: float,
        accent: float,
    ) -> None:
        length = controller.length
        controller.clear()
        if length <= 0:
            return

        width = max(1, round(length * (0.12 + low * 0.10)))
        limit = max(length - width, 0)
        speed = 0.35 + drive * 0.85 + accent * 0.55
        self.position += self.direction * speed

        if self.position >= limit:
            overshoot = self.position - limit
            self.position = max(0.0, limit - overshoot)
            self.direction = -1.0
        elif self.position <= 0.0:
            self.position = min(float(limit), -self.position)
            self.direction = 1.0

        start = round(self.position)
        for index in range(start, min(start + width, length)):
            controller.set_pixel(index, RED)


@dataclass(slots=True)
class RedRaveChase(Animation):
    """Mirrored red blocks chase from both ends and reverse on beats."""

    phase: float = 0.0
    direction: float = 1.0

    @property
    def name(self) -> str:
        return "red_rave_chase"

    def tick(self, frame: int, controller: Controller) -> None:
        self._step(controller, drive=0.4, beat=frame > 0 and frame % 18 == 0)

    def tick_audio(
        self, frame: int, controller: Controller, audio: AudioFrame
    ) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(controller, drive=reactive.drive(), beat=audio.beat)

    def _step(self, controller: Controller, *, drive: float, beat: bool) -> None:
        length = controller.length
        controller.clear()
        if length <= 0:
            return

        block = max(1, round(length / 8))
        period = block * 2
        if beat:
            self.direction *= -1.0
        self.phase = (
            self.phase + self.direction * (0.45 + drive * 0.9)
        ) % period

        midpoint = (length + 1) // 2
        for index in range(length):
            mirrored_index = index if index < midpoint else length - 1 - index
            if (mirrored_index + self.phase) % period < block:
                controller.set_pixel(index, RED)


@dataclass(slots=True)
class RedBlackoutStrobe(Animation):
    """Short, pure-red flashes separated by complete blackouts."""

    flash_frames: int = 0

    @property
    def name(self) -> str:
        return "red_blackout_strobe"

    def tick(self, frame: int, controller: Controller) -> None:
        self._paint(controller, on=frame % 10 < 3)

    def tick_audio(
        self, frame: int, controller: Controller, audio: AudioFrame
    ) -> None:
        if audio.beat:
            self.flash_frames = 2
        on = self.flash_frames > 0
        self.flash_frames = max(self.flash_frames - 1, 0)
        self._paint(controller, on=on)

    @staticmethod
    def _paint(controller: Controller, *, on: bool) -> None:
        if on:
            controller.fill(RED)
        else:
            controller.clear()
