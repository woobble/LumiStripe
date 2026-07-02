from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class BouncingBall(Animation):
    pos: float = 0.0
    speed: float = 0.0

    @property
    def name(self) -> str:
        return "bouncing_ball"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0))
        self.pos += self.speed
        if self.pos >= max_pos:
            self.pos = max_pos
            self.speed = -self.speed
        if self.pos < 0.0:
            self.pos = 0.0
            self.speed = -self.speed

        hue = (frame * 5) % 256
        for i in range(controller.length):
            dist = abs(i - self.pos)
            alpha = 1.0 - dist / 4.0 if dist < 4.0 else 0.0
            controller.set_pixel(i, Rgba(hue, 255, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            direction = 1.0 if self.speed >= 0.0 else -1.0
            self.speed += direction * reactive.accent * 1.5
        self.speed *= 0.998

        max_pos = float(max(controller.length - 1, 0))
        self.pos += self.speed
        if self.pos >= max_pos:
            self.pos = max_pos
            self.speed = -abs(self.speed) * 0.8
        if self.pos < 0.0:
            self.pos = 0.0
            self.speed = abs(self.speed) * 0.8

        hue = reactive.hue_shift(frame, 0.35)
        ball_rad = 2.0 + reactive.low * 3.0
        glow = 0.1 + reactive.rms * 0.5
        for i in range(controller.length):
            dist = abs(i - self.pos)
            if dist < ball_rad:
                alpha = min(1.0 - dist / ball_rad + glow * 0.3, 1.0)
                controller.set_pixel(i, Rgba(hue, 255, 255, alpha))
            elif dist < ball_rad + 4.0:
                alpha = glow * (1.0 - (dist - ball_rad) / 4.0)
                if alpha > 0.0:
                    controller.set_pixel(i, Rgba(hue, 200, 255, alpha))
                else:
                    controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
