from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsl, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class TheaterChase(Animation):
    @property
    def name(self) -> str:
        return "theater_chase"

    def tick(self, frame: int, controller: Controller) -> None:
        phase = frame % 3
        hue = (frame * 5) % 256
        for i in range(controller.length):
            controller.set_pixel(i, Hsl(hue, 100, 55) if i % 3 == phase else Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        stride = 4 if reactive.high > 0.65 else 3
        speed = max(reactive.speed(0.6, 3.5), 0.5)
        phase = int(frame / speed) % stride
        hue = reactive.hue_shift(frame, 0.5)
        for i in range(controller.length):
            if i % stride == phase:
                controller.set_pixel(i, Hsl(hue, 100, 55))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
