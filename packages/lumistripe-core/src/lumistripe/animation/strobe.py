from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Strobe(Animation):
    @property
    def name(self) -> str:
        return "strobe"

    def tick(self, frame: int, controller: Controller) -> None:
        burst = frame % 16
        hue = (frame * 11) % 256
        alpha = 1.0 if burst < 3 else 0.0
        color = Rgba(255, hue, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0)
        for i in range(controller.length):
            controller.set_pixel(i, color)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        on_duration = 1 + int((1.0 - reactive.drive()) * 3.0)
        off_duration = int(5.0 + (1.0 - reactive.high) * 8.0)
        cycle = 0 if audio.beat else frame % (on_duration + off_duration)
        hue = reactive.hue_shift(frame, 1.2)
        alpha = 1.0 if cycle < on_duration else 0.0
        color = Rgba(255, hue, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0)
        for i in range(controller.length):
            controller.set_pixel(i, color)
