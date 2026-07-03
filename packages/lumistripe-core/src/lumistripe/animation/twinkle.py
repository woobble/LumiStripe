from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Twinkle(Animation):
    _bloom: float = 0.0

    @property
    def name(self) -> str:
        return "twinkle"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            controller.set_pixel(i, self._pixel(frame, i, controller.length, density=2.4, age_speed=1.8, bloom=0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._bloom = min(1.0, max(self._bloom, 0.2 + reactive.accent * 0.75))
        else:
            self._bloom *= 0.92

        density = 2.0 + reactive.drive() * 4.5 + reactive.shimmer() * 1.8
        age_speed = 1.4 + reactive.speed(0.0, 2.4)
        for i in range(controller.length):
            controller.set_pixel(i, self._pixel(frame, i, controller.length, density=density, age_speed=age_speed, bloom=self._bloom, warmth=reactive.low, shimmer=reactive.high))

    def _pixel(
        self,
        frame: int,
        index: int,
        length: int,
        *,
        density: float,
        age_speed: float,
        bloom: float,
        warmth: float = 0.0,
        shimmer: float = 0.0,
    ) -> Hsla:
        seed = (frame * 2862933555777941757 + index * 1234567) & 0xFFFFFFFFFFFFFFFF
        twinkle_chance = (seed >> 40) & 0x3F
        age = ((seed + int(frame * age_speed)) >> 24) & 0xFF
        pos = index / max(length - 1, 1)
        drift = sin(frame * 0.018 + pos * 4.2) * 0.5 + 0.5
        base_hue = 154 + int(warmth * 10.0) + int(drift * 8.0)
        base_alpha = 0.028 + drift * 0.028 + bloom * 0.045
        active = twinkle_chance < density

        if active:
            life = 1.0 - abs(age - 127.5) / 127.5
            fade = max(0.0, life)
            alpha = min(base_alpha + fade * fade * (0.22 + shimmer * 0.08) + bloom * 0.07, 0.42)
            hue = base_hue + int(pos * 10.0) + int(shimmer * 6.0)
            lightness = min(68, 56 + int(fade * 8.0) + int(bloom * 4.0))
            return Hsla(hue % 256, 65, lightness, alpha)

        return Hsla(base_hue % 256, 40, 24 + int(drift * 6.0), min(base_alpha, 0.12))
