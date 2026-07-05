from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class BouncingBall(Animation):
    pos: float = 0.0
    speed: float = 0.85
    direction: float = 1.0

    @property
    def name(self) -> str:
        return "bouncing_ball"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0))
        if self.speed == 0.0:
            self.speed = 0.85
        self.pos += self.speed * self.direction
        if self.pos >= max_pos:
            self.pos = max_pos
            self.direction = -1.0
        if self.pos < 0.0:
            self.pos = 0.0
            self.direction = 1.0

        hue = (frame * 5) % 256
        for i in range(controller.length):
            dist = abs(i - self.pos)
            alpha = 1.0 - dist / 4.0 if dist < 4.0 else 0.0
            if alpha > 0.0:
                controller.set_pixel(i, Hsla(hue, 100, 56, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            if reactive.low > 0.3 or reactive.accent > 0.35:
                self.direction *= -1.0
            self.speed = min(3.0, self.speed + reactive.accent * 1.2 + reactive.low * 0.4)
        self.speed = max(0.45, min(3.2, self.speed * 0.985 + reactive.drive() * 0.08))

        max_pos = float(max(controller.length - 1, 0))
        self.pos += self.speed * self.direction
        if self.pos >= max_pos:
            self.pos = max_pos
            self.direction = -1.0
        if self.pos < 0.0:
            self.pos = 0.0
            self.direction = 1.0

        hue = reactive.hue_shift(frame, 0.35)
        ball_rad = 2.0 + reactive.low * 3.0
        glow = 0.1 + reactive.rms * 0.5
        for i in range(controller.length):
            dist = abs(i - self.pos)
            if dist < ball_rad:
                alpha = min(1.0 - dist / ball_rad + glow * 0.3, 1.0)
                controller.set_pixel(i, Hsla(hue, 100, 56, alpha))
            elif dist < ball_rad + 4.0:
                alpha = glow * (1.0 - (dist - ball_rad) / 4.0)
                if alpha > 0.0:
                    controller.set_pixel(i, Hsla(hue, 90, 42, alpha))
                else:
                    controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
