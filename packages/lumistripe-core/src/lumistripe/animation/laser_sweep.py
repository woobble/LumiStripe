from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


def _render_laser(controller: Controller, position: float, width: float, hue: int, alpha: float) -> None:
    for i in range(controller.length):
        dist = abs(i - position)
        if dist < width:
            intensity = 1.0 - dist / width
            controller.set_pixel(i, Hsla(hue, 100, 60, intensity * alpha))
        else:
            controller.set_pixel(i, Rgba(0, 0, 0, 0.0))


@dataclass(slots=True)
class LaserSweep(Animation):
    position: float = -2.0
    direction: float = 1.0

    @property
    def name(self) -> str:
        return "laser_sweep"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0)) + 2.0
        self.position += 1.2 * self.direction
        if self.position > max_pos:
            self.position = -2.0
            self.direction = 1.0
        elif self.position < -2.0 and self.direction < 0.0:
            self.position = max_pos
            self.direction = -1.0
        hue = (frame * 8) % 256
        _render_laser(controller, self.position, 2.0, hue, 0.9)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        max_pos = float(max(controller.length - 1, 0)) + 2.0
        if audio.beat:
            self.direction *= -1.0
        self.position += reactive.speed(0.6, 2.5) * self.direction
        if self.position > max_pos:
            self.position = -2.0
        elif self.position < -2.0:
            self.position = max_pos
        hue = reactive.hue_shift(frame, 0.8)
        width = 1.2 + reactive.high * 2.0
        _render_laser(controller, self.position, width, hue, 0.9)
