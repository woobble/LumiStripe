from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class BeatExplosion(Animation):
    radius: float = 0.0
    burst: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "beat_explosion"

    def tick(self, frame: int, controller: Controller) -> None:
        center = max(controller.length - 1, 0) * 0.5
        self.radius = (frame * 0.45) % (center + 6.0)
        hit = (frame % 30) < 3
        alpha = 0.9 if hit else 0.0
        for i in range(controller.length):
            dist = abs(i - center)
            ring = max(1.0 - abs(dist - self.radius) / 4.0, 0.0)
            hue = (frame * 4 + i * 2) % 256
            controller.set_pixel(i, Hsla(hue, 100, 55, ring * alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        center = max(controller.length - 1, 0) * 0.5
        reactive = AudioReactive.from_frame(audio)
        hit = self.burst.step(reactive.accent, 0.06)
        if audio.beat:
            self.radius = 0.0
        else:
            self.radius += reactive.speed(0.35, 2.0)
            if self.radius > center + 8.0:
                self.radius = 0.0
        for i in range(controller.length):
            dist = abs(i - center)
            ring = max(1.0 - abs(dist - self.radius) / (3.0 + reactive.high * 5.0), 0.0)
            ambient = max(1.0 - dist / max(center, 1.0), 0.0) * 0.08
            alpha = min(ambient + ring * (0.15 + hit * 0.75), 1.0)
            hue = (10 + int(reactive.low * 30) + frame * 2) % 256
            light = int(50 + reactive.mid * 15 + hit * 15)
            controller.set_pixel(i, reactive.accent_color(hue, 100, light, alpha))
