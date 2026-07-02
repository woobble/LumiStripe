from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Comet(Animation):
    @property
    def name(self) -> str:
        return "comet"

    def tick(self, frame: int, controller: Controller) -> None:
        head = frame % max(controller.length, 1)
        hue = (frame * 3) % 256
        for i in range(controller.length):
            dist = abs(head - i)
            intensity = 1.0 if dist == 0 else (1.0 - dist / 6.0 if dist < 6 else 0.0)
            controller.set_pixel(i, Rgba(hue, 255, 255, 0.15 + intensity * 0.85))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = max(reactive.speed(0.25, 1.75), 0.1)
        head = (int(frame * speed) + int(reactive.low * controller.length * 0.2)) % max(controller.length, 1)
        hue = reactive.hue_shift(frame, 0.2)
        for i in range(controller.length):
            dist = abs(head - i)
            intensity = 1.0 if dist == 0 else (1.0 - dist / 6.0 if dist < 6 else 0.0)
            controller.set_pixel(i, Rgba(hue, 255, 255, 0.15 + intensity * 0.85))
