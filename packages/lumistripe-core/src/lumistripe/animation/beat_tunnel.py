from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


def _render_ring(controller: Controller, center: float, radius: float, width: float, hue: int, alpha: float, max_dist: float) -> None:
    for i in range(controller.length):
        dist = abs(i - center)
        ring = max(1.0 - abs(dist - radius) / width, 0.0)
        ambient = max(1.0 - dist / max_dist, 0.0) * 0.1
        combined = min(ambient + ring * alpha, 1.0)
        if combined > 0.0:
            existing = controller.pixel(i)
            er, eg, eb, ea = existing.to_rgba()
            if ea > 0.0:
                combined = max(ea, combined)
            controller.set_pixel(i, Hsla(hue, 80, 55, combined))


@dataclass(slots=True)
class BeatTunnel(Animation):
    radius: float = 0.0
    direction: float = 1.0
    pulse: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "beat_tunnel"

    def tick(self, frame: int, controller: Controller) -> None:
        center = max(controller.length - 1, 0) * 0.5
        max_dist = max(center, 1.0)
        self.radius += 0.5 * self.direction
        if self.radius > max_dist or self.radius < 0.0:
            self.direction *= -1.0
            self.radius = min(max(self.radius, 0.0), max_dist)
        hue_a = (frame * 4) % 256
        hue_b = (frame * 4 + 120) % 256
        _render_ring(controller, center, self.radius, 4.0, hue_a, 0.85, max_dist)
        inner = (self.radius - max_dist * 0.5) % (max_dist + 4.0)
        _render_ring(controller, center, inner, 3.0, hue_b, 0.6, max_dist)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        center = max(controller.length - 1, 0) * 0.5
        max_dist = max(center, 1.0)
        hit = self.pulse.step(reactive.accent, 0.05)
        if audio.beat:
            self.radius = 0.0
            self.direction = 1.0
        else:
            self.radius += reactive.speed(0.3, 1.5) * self.direction
            if self.radius > max_dist + 2.0:
                self.direction = -1.0
            elif self.radius < -2.0:
                self.direction = 1.0
        hue = (170 + int(reactive.low * 30) - int(reactive.high * 20)) % 256
        _render_ring(controller, center, abs(self.radius), 3.0 + reactive.high * 3.0, hue, 0.7 + hit * 0.3, max_dist)
        inner = (abs(self.radius) - max_dist * 0.4) % (max_dist + 2.0)
        if inner > 1.0:
            _render_ring(controller, center, inner, 2.0, (hue + 100) % 256, 0.4 + hit * 0.3, max_dist)
