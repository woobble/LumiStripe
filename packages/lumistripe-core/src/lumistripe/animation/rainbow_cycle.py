from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsl
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class RainbowCycle(Animation):
    @property
    def name(self) -> str:
        return "rainbow_cycle"

    def tick(self, frame: int, controller: Controller) -> None:
        base = frame % 256
        for i in range(controller.length):
            controller.set_pixel(i, Hsl((base + (i * 256 // max(controller.length, 1))) % 256, 100, 50))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = max(reactive.speed(0.45, 2.4), 0.1)
        base = reactive.hue_shift(frame, speed)
        for i in range(controller.length):
            hue = (base + (i * 256 // max(controller.length, 1))) % 256
            controller.set_pixel(i, Hsl(hue, 100, 50))
