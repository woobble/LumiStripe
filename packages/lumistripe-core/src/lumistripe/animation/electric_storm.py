from __future__ import annotations

from dataclasses import dataclass, field
from random import Random

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class _Streak:
    pos: float
    length: int
    speed: float
    hue: int
    alpha: Decay


@dataclass(slots=True)
class ElectricStorm(Animation):
    streaks: list[_Streak] = field(default_factory=list)
    flash: Decay = field(default_factory=Decay)
    _rng: Random = field(default_factory=Random)

    @property
    def name(self) -> str:
        return "electric_storm"

    def tick(self, frame: int, controller: Controller) -> None:
        if frame % 4 < 1 and len(self.streaks) < 8:
            self.streaks.append(_Streak(
                pos=self._rng.randint(0, max(controller.length - 1, 0)),
                length=2 + self._rng.randint(0, 4),
                speed=1.0 + self._rng.random() * 2.0,
                hue=200 + self._rng.randint(-30, 30),
                alpha=Decay(0.8 + self._rng.random() * 0.2),
            ))
        if frame % 30 < 2:
            self.flash.value = 1.0
        for i in range(controller.length):
            controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
        flash_alpha = self.flash.step(0.0, 0.04)
        if flash_alpha > 0.1:
            for i in range(controller.length):
                controller.set_pixel(i, Hsla(230, 80, 80, flash_alpha * 0.4))
        for s in list(self.streaks):
            s.pos += s.speed
            s.alpha.step(0.0, 0.05)
            if s.alpha.value < 0.01 or s.pos >= controller.length + 2.0:
                self.streaks.remove(s)
                continue
            for j in range(s.length):
                p = int(s.pos) - j
                if 0 <= p < controller.length:
                    intensity = 1.0 - j / max(s.length, 1)
                    controller.set_pixel(p, Hsla(s.hue, 90, 75, intensity * s.alpha.value))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self.flash.step(reactive.accent, 0.035 + reactive.high * 0.03)
        if audio.beat:
            self.flash.value = min(self.flash.value + 0.5, 1.0)
        max_streaks = int(3 + reactive.drive() * 8 + reactive.shimmer() * 5)
        if len(self.streaks) < max_streaks and reactive.accent > 0.3 or (self._rng.random() < 0.15):
            self.streaks.append(_Streak(
                pos=self._rng.randint(0, max(controller.length - 1, 0)),
                length=2 + int(reactive.high * 6),
                speed=0.8 + reactive.speed(0.5, 3.0),
                hue=180 + int(reactive.low * 60) + self._rng.randint(-20, 20),
                alpha=Decay(0.6 + reactive.accent * 0.4),
            ))
        for i in range(controller.length):
            controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
        flash_alpha = self.flash.step(0.0, 0.04)
        if flash_alpha > 0.05:
            for i in range(controller.length):
                controller.set_pixel(i, Hsla(230, 80, 80, flash_alpha * 0.3))
        for s in list(self.streaks):
            s.pos += s.speed
            s.alpha.step(0.0, 0.04 + reactive.high * 0.04)
            if s.alpha.value < 0.01 or s.pos >= controller.length + 2.0:
                self.streaks.remove(s)
                continue
            for j in range(s.length):
                p = int(s.pos) - j
                if 0 <= p < controller.length:
                    intensity = 1.0 - j / max(s.length, 1)
                    controller.set_pixel(p, Hsla(s.hue, 90, 75, intensity * s.alpha.value))
