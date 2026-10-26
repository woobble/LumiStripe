from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Rgba
from ..controller import Controller
from ..render import native_solid_render_available, render_solid_rgba_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Strobe(Animation):
    native_render_enabled = True

    @property
    def name(self) -> str:
        return "strobe"

    def tick(self, frame: int, controller: Controller) -> None:
        color = self._color(frame, None)
        for i in range(controller.length):
            controller.set_pixel(i, color)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        color = self._color(frame, audio)
        for i in range(controller.length):
            controller.set_pixel(i, color)

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_solid_render_available():
            return False
        rgba = self._native_rgba(self._color(frame, audio), brightness)
        return render_solid_rgba_into(destination, rgba)

    def _color(self, frame: int, audio: AudioFrame | None) -> Rgba:
        period = max(2, round(28 - self.setting("flash_rate", 50) * 0.24))
        duration = max(1, round(period * self.setting("duty_cycle", 30) / 160.0))
        if audio is None:
            cycle = frame % period
            hue = (frame * 11) % 256
        else:
            reactive = AudioReactive.from_frame(audio)
            cycle = 0 if audio.beat else frame % period
            hue = reactive.hue_shift(frame, 1.2)
        alpha = 1.0 if cycle < duration else 0.0
        return Rgba(255, hue, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0)
