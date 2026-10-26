from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla, Rgba
from ..controller import Controller
from ..render import native_laser_sweep_available, render_laser_sweep_into
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
    native_render_enabled = True

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

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_laser_sweep_available():
            return False
        length = destination.shape[0]
        max_pos = float(max(length - 1, 0)) + 2.0
        travel = self.setting("direction", "bounce")
        if audio is None:
            speed = 1.2 * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
            direction = -1.0 if travel == "reverse" else (1.0 if travel == "forward" else self.direction)
            self.position += speed * direction
            hue = (frame * 8) % 256
            alpha = 0.9
            extra_width = 0.0
        else:
            reactive = AudioReactive.from_frame(audio)
            if audio.beat and travel == "bounce":
                self.direction *= -1.0
            direction = -1.0 if travel == "reverse" else (1.0 if travel == "forward" else self.direction)
            self.position += reactive.speed(0.6, 2.5) * (0.25 + self.setting("speed", 50) / 100.0 * 1.5) * direction
            hue = reactive.hue_shift(frame, 0.8)
            alpha = 0.9
            extra_width = reactive.high * 2.0
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
        width = max(1.0, length * self.setting("width", 30) / 100.0 * 0.14) + extra_width
        return render_laser_sweep_into(
            destination,
            self.position,
            width,
            hue,
            alpha,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
