from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from ..audio import AudioFrame
from ..color import Rgb
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Fire(Animation):
    heat: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.heat = np.zeros((0,), dtype=np.float32)

    @property
    def name(self) -> str:
        return "fire"

    def tick(self, frame: int, controller: Controller) -> None:
        self._step(frame, controller, drive=0.18, bass=0.08, shimmer=0.08)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(
            frame,
            controller,
            drive=reactive.drive(),
            bass=reactive.low,
            shimmer=reactive.high,
            accent=reactive.accent,
        )

    def _step(
        self,
        frame: int,
        controller: Controller,
        *,
        drive: float,
        bass: float,
        shimmer: float,
        accent: float = 0.0,
    ) -> None:
        length = controller.length
        if len(self.heat) != length:
            self.heat = np.zeros(length, dtype=np.float32)
        if length == 0:
            return

        seed_base = frame * 2654435761
        new_heat = np.empty_like(self.heat)
        for i in range(length):
            seed = (seed_base + i * 314159) & 0xFFFFFFFFFFFFFFFF
            noise = ((seed >> 16) & 0xFF) / 255.0
            left = self.heat[i - 1] if i > 0 else self.heat[i]
            right = self.heat[i + 1] if i + 1 < length else self.heat[i]
            spread = self.heat[i] * 0.72 + (left + right) * 0.12
            spark = max(0.0, noise * (0.11 + drive * 0.15 + shimmer * 0.08) - 0.05)
            base = 0.04 + bass * 0.18 + accent * 0.12
            new_heat[i] = min(1.0, spread * 0.98 + spark + base)
        self.heat = new_heat

        for i in range(length):
            heat = self.heat[i]
            ember = max(0.0, heat - 0.08)
            if ember <= 0.0:
                controller.set_pixel(i, Rgb(0, 0, 0))
                continue
            r = min(255, 120 + int(ember * 135.0))
            g = min(255, 28 + int(ember * 190.0))
            b = min(255, 8 + int(ember * 70.0 + shimmer * 25.0))
            controller.set_pixel(i, Rgb(r, g, b))
