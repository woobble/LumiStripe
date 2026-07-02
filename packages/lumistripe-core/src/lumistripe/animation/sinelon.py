from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Sinelon(Animation):
    @property
    def name(self) -> str:
        return "sinelon"

    def tick(self, frame: int, controller: Controller) -> None:
        pos = sin(frame * 0.05) * 0.5 + 0.5
        center = pos * max(controller.length - 1, 0)
        hue = (frame * 4) % 256
        for i in range(controller.length):
            dist = abs(i - center)
            alpha = 1.0 - dist / 8.0 if dist < 8.0 else 0.0
            controller.set_pixel(i, Rgba(hue, 255, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = reactive.speed(0.015, 0.12)
        amplitude = 0.25 + reactive.drive() * 0.75
        pos = sin(frame * speed) * amplitude + 0.5
        center = pos * max(controller.length - 1, 0)
        hue = reactive.hue_shift(frame, 0.3)
        for i in range(controller.length):
            dist = abs(i - center)
            alpha = 1.0 - dist / 8.0 if dist < 8.0 else 0.0
            controller.set_pixel(i, Rgba(hue, 255, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0))
