from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsl
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


def _render_comet(controller: Controller, head: int, hue: int, length: float, alpha: float) -> None:
    for i in range(controller.length):
        dist = abs(head - i)
        intensity = 1.0 if dist == 0 else (1.0 - dist / length if dist < length else 0.0)
        if intensity > 0.0:
            existing = controller.pixel(i)
            er, eg, eb, ea = existing.to_rgba()
            if ea > 0.0:
                controller.set_pixel(i, Hsl(hue, 100, 50))
            else:
                controller.set_pixel(i, Hsl(hue, 100, int(30 + intensity * 30)))


@dataclass(slots=True)
class CometStorm(Animation):
    @property
    def name(self) -> str:
        return "comet_storm"

    def tick(self, frame: int, controller: Controller) -> None:
        n_comets = 5
        for c in range(n_comets):
            offset = c * 17 + frame // 60 * 11
            head = (frame * 2 + offset) % max(controller.length, 1)
            hue = (c * 51 + frame * 3) % 256
            _render_comet(controller, head, hue, 4.0, 0.7)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        n_comets = int(3.0 + reactive.drive() * 5.0 + reactive.shimmer() * 3.0)
        speed = reactive.speed(0.8, 3.5)
        for c in range(n_comets):
            seed = c * 1103515245 + (frame // 30) * 12345
            offset = (seed % 200) + seed // 100 * 7
            head = (int(frame * speed) + offset) % max(controller.length, 1)
            hue = (reactive.hue_shift(frame + c * 30, 0.2) + c * 40) % 256
            tail = 2.0 + reactive.high * 4.0
            alph = 0.4 + reactive.pulse(0.0, 0.4)
            _render_comet(controller, head, hue, tail, alph)
