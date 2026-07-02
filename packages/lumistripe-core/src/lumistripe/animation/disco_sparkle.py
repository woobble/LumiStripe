from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..audio import AudioFrame
from ..color import Hsl, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class DiscoSparkle(Animation):
    _intensities: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self._intensities = np.zeros((0,), dtype=np.float32)

    @property
    def name(self) -> str:
        return "disco_sparkle"

    def tick(self, frame: int, controller: Controller) -> None:
        if len(self._intensities) != controller.length:
            self._intensities = np.zeros(controller.length, dtype=np.float32)
        self._intensities *= 0.85
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            if (seed % 100) < 30:
                self._intensities[i] = min(self._intensities[i] + 0.7, 1.0)
            if self._intensities[i] > 0.05:
                hue = ((seed // 100) + frame * 3) % 256
                controller.set_pixel(i, Hsl(hue, 100, 50))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if len(self._intensities) != controller.length:
            self._intensities = np.zeros(controller.length, dtype=np.float32)
        decay = 0.7 + reactive.high * 0.15
        self._intensities *= decay
        density = int(15.0 + reactive.drive() * 50.0 + reactive.shimmer() * 25.0)
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            trigger = reactive.accent > 0.5 or (seed % 100) < density
            if trigger:
                self._intensities[i] = min(self._intensities[i] + 0.8, 1.0)
            if self._intensities[i] > 0.05:
                hue = ((seed // 100) + int(frame * reactive.speed(0.1, 0.6))) % 256
                lightness = int(40 + self._intensities[i] * 25 + reactive.pulse(0.0, 10))
                controller.set_pixel(i, Hsl(hue, 100, lightness))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
