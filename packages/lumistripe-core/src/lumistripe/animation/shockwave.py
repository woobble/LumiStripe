from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .club_utils import center_distance, ring_profile
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class Shockwave(Animation):
    radius: float = 0.0
    accent: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "shockwave"

    def tick(self, frame: int, controller: Controller) -> None:
        center = max(controller.length - 1, 0) * 0.5
        self.radius = (frame * 0.32) % (center + 6.0)
        for i in range(controller.length):
            dist = center_distance(i, controller.length)
            ring = ring_profile(dist, self.radius, 4.2)
            ambient = max(1.0 - dist / max(center, 1.0), 0.0) * 0.12
            controller.set_pixel(i, Hsla(188, 100, 58, min(ambient + ring * 0.88, 1.0)))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        center = max(controller.length - 1, 0) * 0.5
        reactive = AudioReactive.from_frame(audio)
        self.accent.step(reactive.accent, 0.04 + reactive.high * 0.04)
        if audio.beat:
            self.radius = 0.0
        else:
            self.radius += reactive.speed(0.48, 1.7)
            if self.radius > center + 8.0:
                self.radius = 0.0
        for i in range(controller.length):
            dist = center_distance(i, controller.length)
            band = reactive.band_window(audio, i, controller.length, span=1)
            ring = ring_profile(dist, self.radius, 3.0 + reactive.high * 4.0)
            ambient = max(1.0 - dist / max(center, 1.0), 0.0) * 0.12
            alpha = min(ambient + ring * 0.88 + self.accent.value * 0.05, 1.0)
            hue = (170 + int(reactive.low * 18.0) - int(reactive.high * 10.0)) % 256
            light = min(int(52.0 + reactive.mid * 10.0 + band * 8.0 + self.accent.value * 4.0), 68)
            controller.set_pixel(i, reactive.accent_color(hue, 95, light, alpha))
