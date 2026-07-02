from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class PlasmaRave(Animation):
    @property
    def name(self) -> str:
        return "plasma_rave"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            p = i / max(controller.length - 1, 1)
            v1 = sin(p * 4.0 + frame * 0.05)
            v2 = sin(p * 8.0 - frame * 0.07 + 1.2)
            v3 = sin((p + frame * 0.01) * 12.0)
            plasma = v1 + v2 + v3
            hue = int((plasma * 0.5 + 0.5) * 255 + frame * 2) % 256
            alpha = 0.4 + abs(plasma) * 0.3
            controller.set_pixel(i, Hsla(hue, 100, 55, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = reactive.speed(0.03, 0.12)
        for i in range(controller.length):
            p = i / max(controller.length - 1, 1)
            v1 = sin(p * 4.0 + frame * speed * 0.8)
            v2 = sin(p * 8.0 - frame * speed * 1.2 + 1.2)
            v3 = sin((p + frame * 0.01 * speed) * 12.0)
            plasma = v1 + v2 + v3
            hue = (reactive.hue_shift(frame, 0.15) + int((plasma * 0.5 + 0.5) * 180)) % 256
            alpha = 0.4 + abs(plasma) * 0.3 + reactive.pulse(0.0, 0.3)
            controller.set_pixel(i, Hsla(hue, 100, 55, min(alpha, 1.0)))
