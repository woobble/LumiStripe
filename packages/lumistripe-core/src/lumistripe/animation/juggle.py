from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Juggle(Animation):
    positions: list[float] = field(default_factory=lambda: [0.0, 25.0, 50.0, 75.0])
    speeds: list[float] = field(default_factory=lambda: [2.5, 2.0, 1.8, 2.2])

    @property
    def name(self) -> str:
        return "juggle"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0))
        for j in range(4):
            self.positions[j] += self.speeds[j]
            if self.positions[j] >= max_pos:
                self.positions[j] = max_pos
                self.speeds[j] = -self.speeds[j]
            if self.positions[j] < 0.0:
                self.positions[j] = 0.0
                self.speeds[j] = -self.speeds[j]
        for i in range(controller.length):
            composite = 0.0
            for pos in self.positions:
                dist = abs(i - pos)
                if dist < 6.0:
                    composite += (1.0 - dist / 6.0) * 0.6
            alpha = min(composite, 1.0)
            controller.set_pixel(i, Rgba((i * 32 + 50) % 256, 255, 200, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        max_pos = float(max(controller.length - 1, 0))
        reactive = AudioReactive.from_frame(audio)
        scale = 0.45 + reactive.drive() * 1.4
        for j in range(4):
            if audio.beat:
                direction = 1.0 if self.speeds[j] >= 0.0 else -1.0
                self.speeds[j] = direction * (max(abs(self.speeds[j]), 2.0) * (0.75 + reactive.accent * 0.6))
            self.positions[j] += self.speeds[j] * scale
            if self.positions[j] >= max_pos:
                self.positions[j] = max_pos
                self.speeds[j] = -self.speeds[j]
            if self.positions[j] < 0.0:
                self.positions[j] = 0.0
                self.speeds[j] = -self.speeds[j]
        for i in range(controller.length):
            composite = 0.0
            for pos in self.positions:
                dist = abs(i - pos)
                if dist < 6.0:
                    composite += (1.0 - dist / 6.0) * 0.6
            alpha = min(composite, 1.0)
            if alpha > 0.0:
                hue = (reactive.hue_shift(frame + i, 0.18) + (i * 11 % 128)) % 256
                controller.set_pixel(i, Rgba(hue, 255, 200, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
