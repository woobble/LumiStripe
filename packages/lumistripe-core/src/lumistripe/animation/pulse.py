from __future__ import annotations

from dataclasses import dataclass
from math import sin, tau

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Pulse(Animation):
    _pulse: float = 0.0
    _hue: int = 0

    @property
    def name(self) -> str:
        return "pulse"

    def tick(self, frame: int, controller: Controller) -> None:
        hue = (frame * 2) % 256
        intensity = sin((frame % 60) / 60.0 * tau) * 0.5 + 0.5
        alpha = 0.2 + intensity * 0.8
        for i in range(controller.length):
            controller.set_pixel(i, Hsla(hue, 80, 50, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._pulse = 1.0
            self._hue = (self._hue + 23) % 256

        self._pulse *= 0.92

        breathe = 0.08 + reactive.rms * 0.30
        alpha = min(max(self._pulse, breathe), 1.0)
        for i in range(controller.length):
            controller.set_pixel(i, Hsla(self._hue, 80, 50, alpha))
