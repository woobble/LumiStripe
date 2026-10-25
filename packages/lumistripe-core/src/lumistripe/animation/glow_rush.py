from __future__ import annotations

from dataclasses import dataclass
from math import sin

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
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.0333)
        speed = 1.5 * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        self.position = (self.position + speed) % (max_pos + width * 2.0)
        self.hue = (frame * 2) % 256
        pulse = 0.88 + 0.12 * sin(frame * (0.02 + self.setting("frequency", 50) / 1000.0))
        for i in range(controller.length):
            dist = (i - self.position) / width
            tail = max(0.0, 1.0 - abs(dist))
            tail *= tail
            if tail > 0.01:
                alpha = min(tail * pulse, 1.0)
                controller.set_pixel(i, Hsla(self.hue, 100, 60, alpha))
            else:
                glow = max(0.0, 1.0 - abs(i - self.position) / (width * 3.3)) * 0.08
                if glow > 0.01:
                    controller.set_pixel(i, Hsla(self.hue, 100, 30, glow))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        max_pos = float(max(controller.length - 1, 0))
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        tail_width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.0333) + reactive.high * 2.0
        self.position = (self.position + reactive.speed(0.6, 3.5) * speed_scale) % (max_pos + tail_width * 2.0)
        self.hue = reactive.hue_shift(frame, 0.3)
        glow_width = tail_width * (2.0 + self.setting("frequency", 50) / 50.0)
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
