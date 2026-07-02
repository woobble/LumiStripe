from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Twinkle(Animation):
    _burst: int = 0

    @property
    def name(self) -> str:
        return "twinkle"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            seed = (frame * 2862933555777941757 + i * 1234567) & 0xFFFFFFFFFFFFFFFF
            twinkle_chance = (seed >> 40) & 0x3F
            age = ((seed + frame * 5) >> 24) & 0xFF
            if twinkle_chance < 8:
                fade = (255 - age) / 255.0
                controller.set_pixel(i, Hsla((frame * 2 + i * 17) % 256, 100, 50, fade * fade))
            else:
                controller.set_pixel(i, Hsla(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        density = 4.0 + reactive.drive() * 20.0 + reactive.shimmer() * 10.0
        age_speed = 4.0 + reactive.speed(0.0, 6.0)

        if audio.beat:
            self._burst = controller.length // 3

        for i in range(controller.length):
            seed = (frame * 2862933555777941757 + i * 1234567) & 0xFFFFFFFFFFFFFFFF
            twinkle_chance = (seed >> 40) & 0x3F
            age = ((seed + int(frame * age_speed)) >> 24) & 0xFF

            in_burst = self._burst > 0 and ((seed + frame) % 7) == 0
            active = twinkle_chance < density or in_burst

            if active:
                fade = (255 - age) / 255.0
                alpha = fade * fade
                hue = (reactive.hue_shift(frame, 0.15) + (i * 17 % 256)) % 256
                warmth = 30 + int(reactive.low * 30)
                controller.set_pixel(i, Hsla(hue, 90, warmth, alpha * 0.9))
            else:
                controller.set_pixel(i, Hsla(0, 0, 0, 0.0))

        if self._burst > 0:
            self._burst -= 1
