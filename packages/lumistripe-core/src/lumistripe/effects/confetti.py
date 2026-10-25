from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..animation.reactive import AudioReactive
from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Effect


@dataclass(slots=True)
class Confetti(Effect):
    intensities: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.intensities = np.zeros((0,), dtype=np.float32)

    @property
    def name(self) -> str:
        return "confetti"

    def tick(self, frame: int, controller: Controller) -> None:
        if len(self.intensities) != controller.length:
            self.intensities = np.zeros(controller.length, dtype=np.float32)
        fade = self.setting("fade_time", 50) / 100.0
        count = self.setting("count", 4)
        spread = self.setting("spread", 60) / 100.0
        self.intensities *= 0.72 + fade * 0.32
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            center = (frame * 37) % max(controller.length, 1)
            distance = min(abs(i - center), controller.length - abs(i - center))
            spread_weight = (1.0 - spread) + spread * max(
                0.0, 1.0 - distance / max(controller.length * 0.5, 1.0)
            )
            if (seed % 100) < (4 + count * 3) * spread_weight:
                self.intensities[i] = min(1.0, self.intensities[i] + 0.45)
            if self.intensities[i] > 0.03:
                hue = ((seed // 100) + frame * 2) % 256
                light = 48 + int(self.intensities[i] * 16.0)
                alpha = min(1.0, 0.12 + self.intensities[i] * 0.88)
                controller.set_pixel(i, Hsla(hue, 100, light, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if len(self.intensities) != controller.length:
            self.intensities = np.zeros(controller.length, dtype=np.float32)
        fade = self.setting("fade_time", 50) / 100.0
        count_setting = self.setting("count", 4)
        spread = self.setting("spread", 60) / 100.0
        decay = 0.68 + fade * 0.30 - reactive.high * 0.03
        self.intensities *= max(decay, 0.68)
        density = int(4.0 + count_setting * 2.0 + reactive.drive() * 36.0 + reactive.shimmer() * 22.0)
        bloom = reactive.beat_pulse(0.0, 0.45)
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            center = (frame * 37) % max(controller.length, 1)
            distance = min(abs(i - center), controller.length - abs(i - center))
            spread_weight = (1.0 - spread) + spread * max(
                0.0, 1.0 - distance / max(controller.length * 0.5, 1.0)
            )
            trigger = reactive.accent > 0.55 or (seed % 100) < density * spread_weight
            if trigger:
                self.intensities[i] = min(
                    1.0, self.intensities[i] + 0.4 + reactive.high * 0.15
                )
            if self.intensities[i] > 0.03:
                hue = (
                    (seed // 100)
                    + reactive.hue_shift(frame, 0.25)
                    + int(reactive.band_window(audio, i, controller.length, 1) * 18.0)
                ) % 256
                light = 46 + int(self.intensities[i] * 20.0 + reactive.high * 8.0)
                alpha = min(1.0, 0.12 + self.intensities[i] * 0.78 + bloom * 0.08)
                controller.set_pixel(i, Hsla(hue, 100, light, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
