from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class TheaterChase(Animation):
    @property
    def name(self) -> str:
        return "theater_chase"

    def tick(self, frame: int, controller: Controller) -> None:
        stride = 3
        phase = frame % stride
        hue = (frame * 5) % 256
        for i in range(controller.length):
            slot = i % stride
            if slot == phase:
                controller.set_pixel(i, Hsla(hue, 100, 55, 1.0))
            elif slot == (phase - 1) % stride or slot == (phase + 1) % stride:
                controller.set_pixel(i, Hsla(hue, 80, 42, 0.25))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        stride = 4 if reactive.high > 0.65 else 3
        speed = max(reactive.speed(0.45, 2.8), 0.5)
        phase = int(frame / speed) % stride
        hue = reactive.hue_shift(frame, 0.5)
        for i in range(controller.length):
            slot = i % stride
            if slot == phase:
                controller.set_pixel(i, Hsla(hue, 100, 56, min(1.0, 0.76 + reactive.drive() * 0.24)))
            elif slot == (phase - 1) % stride or slot == (phase + 1) % stride:
                controller.set_pixel(i, Hsla(hue, 88, 42, 0.22 + reactive.high * 0.12))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
