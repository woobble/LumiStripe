from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Rgb
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Police(Animation):
    @property
    def name(self) -> str:
        return "police"

    def tick(self, frame: int, controller: Controller) -> None:
        half = controller.length // 2
        flash = (frame // 8) % 2 == 0
        for i in range(controller.length):
            if flash:
                controller.set_pixel(i, Rgb(255, 0, 0) if i < half else Rgb(0, 0, 255))
            else:
                controller.set_pixel(i, Rgb(0, 0, 255) if i < half else Rgb(255, 0, 0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        half = controller.length // 2
        reactive = AudioReactive.from_frame(audio)
        rate = max(int(reactive.speed(2.0, 10.0)), 1)
        flash = audio.beat or ((frame // rate) % 2 == 0)
        for i in range(controller.length):
            if flash:
                if audio.beat:
                    r, g, b = 255, 255, 255
                elif i < half:
                    r, g, b = 255, 0, 0
                else:
                    r, g, b = 0, 0, 255
            else:
                r, g, b = (0, 0, 255) if i < half else (255, 0, 0)
            controller.set_pixel(i, Rgb(r, g, b))
