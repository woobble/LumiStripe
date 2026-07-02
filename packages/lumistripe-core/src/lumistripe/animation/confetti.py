from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsl, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Confetti(Animation):
    @property
    def name(self) -> str:
        return "confetti"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            sparkle = (seed % 100) < 18
            controller.set_pixel(i, Hsl((seed // 100) % 256, 100, 60) if sparkle else Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        density = int(12.0 + reactive.drive() * 48.0 + reactive.shimmer() * 18.0)
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            sparkle = reactive.accent > 0.55 or (seed % 100) < density
            if sparkle:
                hue = ((seed // 100) + reactive.hue_shift(frame, 0.25)) % 256
                controller.set_pixel(i, Hsl(hue, 100, 60))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
