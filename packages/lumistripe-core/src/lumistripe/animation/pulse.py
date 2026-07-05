from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sin

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
        cycle = (frame % 120) / 120.0
        intensity = (sin(cycle * pi * 2.0) * 0.5 + 0.5) ** 1.2
        alpha = 0.22 + intensity * 0.78
        hue = (frame * 2 + int(intensity * 18.0)) % 256
        for i in range(controller.length):
            light = 46 + int((cos(cycle * pi * 2.0 + i * 0.05) * 0.5 + 0.5) * 5.0)
            controller.set_pixel(i, Hsla(hue, 82, light, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._pulse = min(1.0, self._pulse + 0.55 + reactive.accent * 0.35)
            self._hue = (self._hue + 23 + int(reactive.mid * 18.0)) % 256

        self._pulse = max(self._pulse * 0.92, reactive.beat_pulse(0.0, 0.18))
        self._hue = (self._hue + int(reactive.low * 1.5)) % 256

        breathe = 0.22 + reactive.rms * 0.2 + reactive.low * 0.08
        alpha = min(max(self._pulse, breathe), 1.0)
        light = 46 + int(reactive.mid * 12.0 + self._pulse * 10.0)
        for i in range(controller.length):
            controller.set_pixel(i, Hsla(self._hue, 82, light, alpha))
