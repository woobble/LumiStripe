from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class _Beam:
    position: float
    direction: float
    hue: int


def _render_beam(controller: Controller, beam: _Beam, width: float, alpha: float) -> None:
    for i in range(controller.length):
        dist = abs(i - beam.position)
        if dist < width:
            intensity = 1.0 - dist / width
            controller.set_pixel(i, Hsla(beam.hue, 100, 60, intensity * alpha))


@dataclass(slots=True)
class DualLaser(Animation):
    beam_a: _Beam = field(default_factory=lambda: _Beam(position=-2.0, direction=1.0, hue=0))
    beam_b: _Beam = field(default_factory=lambda: _Beam(position=-2.0, direction=1.0, hue=200))
    burst: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "dual_laser"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0))
        for beam in (self.beam_a, self.beam_b):
            beam.position += 1.5 * beam.direction
            if beam.position > max_pos + 2.0:
                beam.position = -2.0
            elif beam.position < -2.0:
                beam.position = max_pos + 2.0
        self.beam_a.hue = (frame * 5) % 256
        self.beam_b.hue = (self.beam_a.hue + 140) % 256
        controller.clear()
        _render_beam(controller, self.beam_a, 2.5, 0.9)
        _render_beam(controller, self.beam_b, 2.5, 0.9)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        max_pos = float(max(controller.length - 1, 0))
        if audio.beat:
            self.beam_a.direction *= -1.0
            self.beam_b.direction *= -1.0
            self.burst.value = 1.0
        for beam in (self.beam_a, self.beam_b):
            beam.position += reactive.speed(0.6, 2.5) * beam.direction
            if beam.position > max_pos + 2.0:
                beam.position = -2.0
            elif beam.position < -2.0:
                beam.position = max_pos + 2.0
        self.beam_a.hue = reactive.hue_shift(frame, 0.6)
        self.beam_b.hue = (self.beam_a.hue + 140) % 256
        burst_alpha = self.burst.step(0.0, 0.06)
        width = 1.5 + reactive.high * 2.5 + burst_alpha * 3.0
        alpha = 0.7 + burst_alpha * 0.3
        controller.clear()
        _render_beam(controller, self.beam_a, width, alpha)
        _render_beam(controller, self.beam_b, width, alpha)
