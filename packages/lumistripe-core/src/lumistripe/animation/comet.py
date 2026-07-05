from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .club_utils import trail_profile
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Comet(Animation):
    position: float = 0.0
    speed: float = 0.85
    direction: float = 1.0
    tail: float = 5.0
    hue: int = 0

    @property
    def name(self) -> str:
        return "comet"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0))
        self.speed = min(1.8, max(0.45, self.speed + 0.01))
        self.position += self.speed * self.direction
        if self.position >= max_pos:
            self.position = max_pos
            self.direction = -1.0
        elif self.position <= 0.0:
            self.position = 0.0
            self.direction = 1.0

        self.tail = 4.2 + (sin(frame * 0.05) * 0.5 + 0.5) * 1.8
        self.hue = (frame * 3) % 256
        for i in range(controller.length):
            dist = abs(self.position - i)
            intensity = trail_profile(dist, self.tail)
            if intensity > 0.0:
                alpha = min(1.0, 0.14 + intensity * 0.86)
                light = 48 + int(intensity * 12.0)
                controller.set_pixel(i, Hsla(self.hue, 100, light, alpha))
            else:
                controller.set_pixel(i, Hsla(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        max_pos = float(max(controller.length - 1, 0))
        if audio.beat and (reactive.low > 0.3 or reactive.accent > 0.45):
            self.direction *= -1.0
            self.speed = min(3.0, self.speed + reactive.accent * 0.8)
        self.speed = max(0.5, min(3.0, self.speed * 0.9 + reactive.speed(0.25, 1.3) * 0.1))
        self.position += self.speed * self.direction
        if self.position >= max_pos:
            self.position = max_pos
            self.direction = -1.0
        elif self.position <= 0.0:
            self.position = 0.0
            self.direction = 1.0

        self.tail = 3.8 + reactive.high * 5.0
        hue = reactive.hue_shift(frame, 0.2)
        shimmer = reactive.beat_pulse(0.0, 0.25) + reactive.rms * 0.2
        for i in range(controller.length):
            dist = abs(self.position - i)
            intensity = trail_profile(dist, self.tail)
            if intensity > 0.0:
                alpha = min(1.0, 0.14 + intensity * 0.82 + shimmer * 0.12)
                light = 48 + int(reactive.mid * 10.0 + intensity * 10.0)
                controller.set_pixel(i, Hsla(hue, 100, light, alpha))
            else:
                controller.set_pixel(i, Hsla(0, 0, 0, 0.0))
