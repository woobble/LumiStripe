from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Confetti(Animation):
    intensities: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.intensities = np.zeros((0,), dtype=np.float32)

    @property
    def name(self) -> str:
        return "confetti"

    def tick(self, frame: int, controller: Controller) -> None:
        if len(self.intensities) != controller.length:
            self.intensities = np.zeros(controller.length, dtype=np.float32)
        self.intensities *= 0.88
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            if (seed % 100) < 18:
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
        decay = 0.84 - reactive.high * 0.04
        self.intensities *= max(decay, 0.72)
        density = int(12.0 + reactive.drive() * 40.0 + reactive.shimmer() * 26.0)
        bloom = reactive.beat_pulse(0.0, 0.45)
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            trigger = reactive.accent > 0.55 or (seed % 100) < density
            if trigger:
                self.intensities[i] = min(1.0, self.intensities[i] + 0.4 + reactive.high * 0.15)
            if self.intensities[i] > 0.03:
                hue = ((seed // 100) + reactive.hue_shift(frame, 0.25) + int(reactive.band_window(audio, i, controller.length, 1) * 18.0)) % 256
                light = 46 + int(self.intensities[i] * 20.0 + reactive.high * 8.0)
                alpha = min(1.0, 0.12 + self.intensities[i] * 0.78 + bloom * 0.08)
                controller.set_pixel(i, Hsla(hue, 100, light, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
