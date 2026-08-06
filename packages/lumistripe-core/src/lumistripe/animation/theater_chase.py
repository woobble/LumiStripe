from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class TheaterChase(Animation):
    phase: float = 0.0
    hue_phase: float = 0.0

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
        speed = 0.28 + reactive.drive() * 0.65 + reactive.accent * 0.2
        self.phase = (self.phase + speed) % stride
        self.hue_phase = (self.hue_phase + 0.45 + reactive.high * 1.2) % 360.0
        hue = int(self.hue_phase)
        for i in range(controller.length):
            slot = i % stride
            direct = abs(slot - self.phase)
            distance = min(direct, stride - direct)
            glow = max(1.0 - distance / 1.35, 0.0)
            alpha = glow * (0.72 + reactive.drive() * 0.22)
            if alpha > 0.01:
                controller.set_pixel(i, Hsla(hue, 96, 52, alpha))
                continue
            controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
