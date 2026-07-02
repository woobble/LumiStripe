from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Bpm(Animation):
    _flash: float = 0.0
    _hue: int = 0

    @property
    def name(self) -> str:
        return "bpm"

    def tick(self, frame: int, controller: Controller) -> None:
        beat = (sin(frame * 0.1) * 0.5 + 0.5) ** 3.0
        hue = (frame * 5) % 256
        for i in range(controller.length):
            controller.set_pixel(i, Rgba(hue, 255, 255, beat))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._flash = 1.0
            self._hue = (self._hue + 31) % 256

        decay = 0.90 + reactive.rms * 0.05
        self._flash *= decay

        alpha = min(self._flash + reactive.rms * 0.15, 1.0)
        for i in range(controller.length):
            controller.set_pixel(i, Rgba(self._hue, 255, 255, alpha))
