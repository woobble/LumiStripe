from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


def _render_comet(controller: Controller, head: float, hue: int, length: float, alpha: float) -> None:
    for i in range(controller.length):
        direct = abs(head - i)
        dist = min(direct, max(controller.length - direct, 0.0))
        intensity = max(1.0 - dist / max(length, 0.001), 0.0)
        if intensity > 0.0:
            existing = controller.pixel(i)
            ea = existing.to_rgba()[3]
            strength = min(1.0, intensity * alpha)
            if strength >= ea:
                controller.set_pixel(i, Hsla(hue, 100, 55, strength))


@dataclass(slots=True)
class CometStorm(Animation):
    phase: float = 0.0
    hue_phase: float = 0.0

    @property
    def name(self) -> str:
        return "comet_storm"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgba(0, 0, 0, 0.0))
        n_comets = 5
        for c in range(n_comets):
            offset = c * 17 + frame // 60 * 11
            head = float((frame * 2 + offset) % max(controller.length, 1))
            hue = (c * 51 + frame * 3) % 256
            _render_comet(controller, head, hue, 4.0, 0.7)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        controller.fill(Rgba(0, 0, 0, 0.0))
        reactive = AudioReactive.from_frame(audio)
        n_comets = int(3.0 + reactive.drive() * 5.0 + reactive.shimmer() * 3.0)
        speed = reactive.speed(0.8, 3.5)
        self.phase = (self.phase + speed) % max(controller.length, 1)
        self.hue_phase = (self.hue_phase + 0.5 + reactive.high * 1.4) % 360.0
        for c in range(n_comets):
            seed = c * 1103515245 + (frame // 30) * 12345
            offset = (seed % 200) + seed // 100 * 7
            head = (self.phase + offset) % max(controller.length, 1)
            hue = int(self.hue_phase + c * 40) % 360
            tail = 2.0 + reactive.high * 4.0
            alph = 0.4 + reactive.pulse(0.0, 0.4)
            _render_comet(controller, head, hue, tail, alph)
