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
        travel = self.setting("direction", "bounce")
        speed = 1.2 * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        direction = -1.0 if travel == "reverse" else (1.0 if travel == "forward" else self.direction)
        self.position += speed * direction
        if self.position > max_pos:
            if travel == "bounce":
                self.position = max_pos
                self.direction = -1.0
            else:
                self.position = -2.0
        elif self.position < -2.0:
            if travel == "bounce":
                self.position = -2.0
                self.direction = 1.0
            else:
                self.position = max_pos
        hue = (frame * 8) % 256
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14)
        _render_laser(controller, self.position, width, hue, 0.9)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        max_pos = float(max(controller.length - 1, 0)) + 2.0
        if audio.beat and self.setting("direction", "bounce") == "bounce":
            self.direction *= -1.0
        travel = self.setting("direction", "bounce")
        direction = -1.0 if travel == "reverse" else (1.0 if travel == "forward" else self.direction)
        self.position += reactive.speed(0.6, 2.5) * (0.25 + self.setting("speed", 50) / 100.0 * 1.5) * direction
        if self.position > max_pos:
            if travel == "bounce":
                self.position = max_pos
                self.direction = -1.0
            else:
                self.position = -2.0
        elif self.position < -2.0:
            if travel == "bounce":
                self.position = -2.0
                self.direction = 1.0
            else:
                self.position = max_pos
        hue = reactive.hue_shift(frame, 0.8)
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14) + reactive.high * 2.0
        _render_laser(controller, self.position, width, hue, 0.9)
