from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class GlowRush(Animation):
    position: float = 0.0
    hue: int = 0

    @property
    def name(self) -> str:
        return "glow_rush"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0))
        self.position = (self.position + 1.5) % (max_pos + 6.0)
        self.hue = (frame * 2) % 256
        for i in range(controller.length):
            dist = (i - self.position) / 6.0
            tail = max(0.0, 1.0 - abs(dist))
            tail *= tail
            if tail > 0.01:
                alpha = min(tail * 1.0, 1.0)
                controller.set_pixel(i, Hsla(self.hue, 100, 60, alpha))
            else:
                glow = max(0.0, 1.0 - abs(i - self.position) / 20.0) * 0.08
                if glow > 0.01:
                    controller.set_pixel(i, Hsla(self.hue, 100, 30, glow))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        max_pos = float(max(controller.length - 1, 0))
        self.position = (self.position + reactive.speed(0.6, 3.5)) % (max_pos + 6.0)
        self.hue = reactive.hue_shift(frame, 0.3)
        tail_width = 3.0 + reactive.high * 6.0
        glow_width = tail_width * 4.0
        for i in range(controller.length):
            dist = (i - self.position) / tail_width
            tail = max(0.0, 1.0 - abs(dist))
            tail *= tail
            if tail > 0.01:
                alpha = min(tail * reactive.pulse(0.7, 0.3), 1.0)
                light = int(40 + reactive.mid * 20)
                controller.set_pixel(i, Hsla(self.hue, 100, light, alpha))
            else:
                glow = max(0.0, 1.0 - abs(i - self.position) / glow_width) * 0.06
                if glow > 0.01:
                    controller.set_pixel(i, Hsla(self.hue, 100, 20, glow))
