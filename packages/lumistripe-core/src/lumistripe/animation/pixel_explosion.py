from __future__ import annotations

from dataclasses import dataclass, field
from random import Random

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class _Burst:
    x: float
    radius: float
    hue: int
    alpha: Decay


@dataclass(slots=True)
class PixelExplosion(Animation):
    bursts: list[_Burst] = field(default_factory=list)
    _rng: Random = field(default_factory=Random)

    @property
    def name(self) -> str:
        return "pixel_explosion"

    def tick(self, frame: int, controller: Controller) -> None:
        if frame % 12 < 2:
            x = self._rng.randint(0, max(controller.length - 1, 0))
            self.bursts.append(_Burst(x=float(x), radius=0.0, hue=(frame * 17) % 256, alpha=Decay(1.0)))
        self._update(controller, 0.6)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat or reactive.accent > 0.5:
            count = 1 + int(reactive.accent * 3 + reactive.drive() * 2)
            for _ in range(count):
                x = self._rng.randint(0, max(controller.length - 1, 0))
                hue = (int(reactive.low * 90) + frame * 6 + self._rng.randint(0, 120)) % 256
                self.bursts.append(_Burst(x=float(x), radius=0.0, hue=hue, alpha=Decay(0.7 + reactive.accent * 0.3)))
        self._update(controller, 0.8 + reactive.high * 1.2)

    def _update(self, controller: Controller, speed: float) -> None:
        max_pos = float(max(controller.length - 1, 0))
        for b in list(self.bursts):
            b.radius += speed
            b.alpha.step(0.0, 0.04)
            if b.alpha.value < 0.01 or b.radius > max_pos + 5.0:
                self.bursts.remove(b)
                continue
            for i in range(controller.length):
                dist = abs(i - b.x)
                ring = max(1.0 - abs(dist - b.radius) / 2.5, 0.0)
                if ring > 0.0:
                    alpha = ring * b.alpha.value
                    if alpha > 0.01:
                        controller.set_pixel(i, Hsla(b.hue, 100, 55, alpha))
