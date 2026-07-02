from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class RainbowStrobe(Animation):
    @property
    def name(self) -> str:
        return "rainbow_strobe"

    def tick(self, frame: int, controller: Controller) -> None:
        burst = frame % 12
        hue = (frame * 17) % 256
        on = burst < 3
        alpha = 1.0 if on else 0.0
        light = 70 if on else 0
        for i in range(controller.length):
            if on:
                controller.set_pixel(i, Hsla(hue, 100, light, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        on_duration = 1 + int((1.0 - reactive.drive()) * 3.0)
        off_duration = int(4.0 + (1.0 - reactive.high) * 8.0)
        cycle = 0 if audio.beat else frame % (on_duration + off_duration)
        hue = reactive.hue_shift(frame, 2.0)
        on = cycle < on_duration
        alpha = 1.0 if on else 0.0
        light = 70 if on else 0
        for i in range(controller.length):
            if on:
                controller.set_pixel(i, Hsla(hue, 100, light, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
