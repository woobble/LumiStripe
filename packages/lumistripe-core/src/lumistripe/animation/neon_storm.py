from __future__ import annotations

from dataclasses import dataclass, field
from random import Random

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


_PALETTE = [(0, 100, 60), (200, 100, 55), (120, 100, 50), (300, 100, 60), (50, 100, 55)]


@dataclass(slots=True)
class NeonStorm(Animation):
    _rng: Random = field(default_factory=Random)

    @property
    def name(self) -> str:
        return "neon_storm"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            seed = (frame * 48271 + i * 63689) & 0xFFFFFFFF
            if seed % 100 < 15:
                hue, sat, light = _PALETTE[(seed // 100) % len(_PALETTE)]
                hue = (hue + frame * 3) % 256
                controller.set_pixel(i, Hsla(hue, sat, light, 1.0))
            else:
                fade = max(1.0 - (seed % 10) * 0.1, 0.0)
                streak = (i + frame * 3) % controller.length
                if abs(i - streak) < 2:
                    hue, sat, light = _PALETTE[(frame // 4) % len(_PALETTE)]
                    controller.set_pixel(i, Hsla(hue, sat, light, 0.6 * fade))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        density = int(6 + reactive.drive() * 30 + reactive.shimmer() * 15)
        streak_count = int(1 + reactive.low * 4)
        streak_positions = [
            (frame * int(3 + i * 7 + reactive.high * 10)) % max(controller.length, 1)
            for i in range(streak_count)
        ]
        for i in range(controller.length):
            seed = (frame * 48271 + i * 63689) & 0xFFFFFFFF
            on = (seed % 100) < density or reactive.accent > 0.6
            if on:
                hue, sat, light = _PALETTE[(seed // 100) % len(_PALETTE)]
                hue = (hue + reactive.hue_shift(frame, 0.3)) % 256
                controller.set_pixel(i, Hsla(hue, sat, light, 1.0))
            else:
                lit = 0.0
                for sp in streak_positions:
                    dist = abs(i - sp)
                    if dist < 3:
                        trail = 1.0 - dist / 3.0
                        hue, sat, light = _PALETTE[(sp + frame // 2) % len(_PALETTE)]
                        controller.set_pixel(i, Hsla(hue, sat, light, trail * 0.7))
                        lit = 1.0
                        break
                if not lit:
                    controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
