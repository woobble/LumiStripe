from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Rgba
from ..controller import Controller
from ..render import native_sinelon_available, render_sinelon_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Sinelon(Animation):
    native_render_enabled = True

    @property
    def name(self) -> str:
        return "sinelon"

    def tick(self, frame: int, controller: Controller) -> None:
        pos = sin(frame * 0.05) * 0.5 + 0.5
        center = pos * max(controller.length - 1, 0)
        hue = (frame * 4) % 256
        width = max(1.0, controller.length * self.setting("fade_time", 50) / 100.0 * 0.16)
        for i in range(controller.length):
            dist = abs(i - center)
            alpha = 1.0 - dist / width if dist < width else 0.0
            controller.set_pixel(i, Rgba(hue, 255, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = reactive.speed(0.015, 0.12)
        amplitude = 0.25 + reactive.drive() * 0.75
        pos = sin(frame * speed) * amplitude + 0.5
        center = pos * max(controller.length - 1, 0)
        hue = reactive.hue_shift(frame, 0.3)
        width = max(1.0, controller.length * self.setting("fade_time", 50) / 100.0 * 0.16)
        for i in range(controller.length):
            dist = abs(i - center)
            alpha = 1.0 - dist / width if dist < width else 0.0
            controller.set_pixel(i, Rgba(hue, 255, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_sinelon_available():
            return False
        length = destination.shape[0]
        if audio is None:
            position = sin(frame * 0.05) * 0.5 + 0.5
            hue = (frame * 4) % 256
        else:
            reactive = AudioReactive.from_frame(audio)
            position = sin(frame * reactive.speed(0.015, 0.12)) * (0.25 + reactive.drive() * 0.75) + 0.5
            hue = reactive.hue_shift(frame, 0.3)
        center = position * max(length - 1, 0)
        width = max(1.0, length * self.setting("fade_time", 50) / 100.0 * 0.16)
        return render_sinelon_into(
            destination,
            center,
            width,
            hue,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
