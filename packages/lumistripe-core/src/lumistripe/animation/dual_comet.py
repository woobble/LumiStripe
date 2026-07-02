from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


def _render_dual_comets(
    controller: Controller, phase: float, width: float, alpha: float, hue_a: int, hue_b: int
) -> None:
    max_pos = float(max(controller.length - 1, 0))
    other = max_pos - phase
    for i in range(controller.length):
        dist_a = abs(i - phase)
        dist_b = abs(i - other)
        tail_a = max(1.0 - dist_a / width, 0.0)
        tail_b = max(1.0 - dist_b / width, 0.0)
        if tail_a > tail_b and tail_a > 0.0:
            controller.set_pixel(i, Hsla(hue_a, 100, 55, min(tail_a * alpha, 1.0)))
        elif tail_b > 0.0:
            controller.set_pixel(i, Hsla(hue_b, 100, 55, min(tail_b * alpha, 1.0)))
        else:
            controller.set_pixel(i, Rgba(0, 0, 0, 0.0))


@dataclass(slots=True)
class DualComet(Animation):
    phase: float = 0.0
    direction: float = 1.0
    burst: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "dual_comet"

    def tick(self, frame: int, controller: Controller) -> None:
        self.phase += 0.75 * self.direction
        max_pos = float(max(controller.length - 1, 0))
        if self.phase >= max_pos or self.phase <= 0.0:
            self.direction *= -1.0
            self.phase = min(max(self.phase, 0.0), max_pos)
        _render_dual_comets(controller, self.phase, 6.0, 0.85, 40, 220)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self.burst.step(reactive.accent, 0.045)
        if audio.beat:
            self.direction *= -1.0
        self.phase += reactive.speed(0.45, 2.3) * self.direction
        max_pos = float(max(controller.length - 1, 0))
        if self.phase >= max_pos or self.phase <= 0.0:
            self.direction *= -1.0
            self.phase = min(max(self.phase, 0.0), max_pos)
        width = 4.0 + reactive.high * 6.0
        alpha = 0.85
        hue_a = 10 + int(reactive.low * 90.0)
        hue_b = 150 + int(reactive.high * 80.0)
        _render_dual_comets(controller, self.phase, width, alpha, hue_a, hue_b)
