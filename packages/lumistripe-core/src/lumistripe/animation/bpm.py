from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Rgba
from ..controller import Controller
from ..render import native_solid_render_available, render_solid_rgba_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Bpm(Animation):
    native_render_enabled = True

    _flash: float = 0.0
    _hue: int = 0

    @property
    def name(self) -> str:
        return "bpm"

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
        return render_solid_rgba_into(
            destination, self._native_rgba(self._color(frame, audio), brightness)
        )

    def _color(self, frame: int, audio: AudioFrame | None) -> Rgba:
        if audio is None:
            beat = (sin(frame * 0.1) * 0.5 + 0.5) ** 3.0
            hue = (frame * 5) % 256
            return Rgba(hue, 255, 255, beat)
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._flash = 1.0
            self._hue = (self._hue + 31) % 256

        decay = 0.98 - self.setting("decay", 50) * 0.0016 + reactive.rms * 0.04
        self._flash *= decay

        alpha = min(self._flash + reactive.rms * 0.15, 1.0)
        return Rgba(self._hue, 255, 255, alpha)
