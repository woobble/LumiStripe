from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Effect


@dataclass(slots=True)
class BeatRipple(Effect):
    radius: float = 0.0
    burst: Decay = field(default_factory=Decay)
    hue: int = 0

    @property
    def name(self) -> str:
        return "beat_ripple"

    def tick(self, frame: int, controller: Controller) -> None:
        center = max(controller.length - 1, 0) * 0.5
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14)
        decay = 0.4 + self.setting("decay", 50) / 100.0 * 0.6
        self.radius = (frame * 0.4 * speed) % (center + 6.0)
        self.hue = (frame * 3) % 256
        for i in range(controller.length):
            dist = abs(i - center)
            ring = max(1.0 - abs(dist - self.radius) / width, 0.0)
            mirror = max(1.0 - abs(dist - (center - self.radius + center)) / width, 0.0)
            alpha = max(ring, mirror) * (0.1 + decay * (1.0 - self.radius / (center + 6.0)))
            if alpha > 0.01:
                controller.set_pixel(i, Hsla(self.hue, 100, 50, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        max_pos = float(max(controller.length - 1, 0))
        reactive = AudioReactive.from_frame(audio)
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14)
        decay = self.setting("decay", 50) / 100.0
        hit = self.burst.step(reactive.accent, 0.02 + decay * 0.06)
        if audio.beat:
            self.radius = 0.0
            self.hue = (self.hue + 31) % 256
        else:
            self.radius += reactive.speed(0.4, 2.2) * speed

        for i in range(controller.length):
            left = abs(i - (-self.radius % (max_pos + 1)))
            right = abs(i - (max_pos - (-self.radius % (max_pos + 1))))
            ring = max(1.0 - left / (width + reactive.high * 2.0), 0.0)
            mirror = max(1.0 - right / (width + reactive.high * 2.0), 0.0)
            alpha = max(ring, mirror) * (0.15 + hit * 0.6 + reactive.low * 0.15) * (0.5 + decay)
            if alpha > 0.01:
                light = int(40 + reactive.mid * 20 + hit * 15)
                controller.set_pixel(i, Hsla(self.hue, 100, light, alpha))
