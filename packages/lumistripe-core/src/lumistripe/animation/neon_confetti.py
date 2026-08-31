from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import neon_color
from .reactive import AudioReactive


@dataclass(slots=True)
class NeonConfetti(Animation):
    levels: np.ndarray = field(init=False, repr=False)
    hues: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.levels = np.zeros((0,), dtype=np.float32)
        self.hues = np.zeros((0,), dtype=np.int16)

    @property
    def name(self) -> str:
        return "neon_confetti"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(AudioFrame(rms=0.16, bands=(0.1, 0.12, 0.12, 0.14, 0.12, 0.16, 0.18, 0.2)))
        self._render(frame, controller, reactive)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._render(frame, controller, reactive)

    def _render(self, frame: int, controller: Controller, reactive: AudioReactive) -> None:
        if len(self.levels) != controller.length:
            self.levels = np.zeros(controller.length, dtype=np.float32)
            self.hues = np.zeros(controller.length, dtype=np.int16)
        self.levels *= 0.84 - reactive.high * 0.04
        spawn_threshold = int(
            (0.008 + reactive.drive() * 0.025 + reactive.shimmer() * 0.018 + reactive.accent * 0.04)
            * 10_000.0
        )
        controller.clear()
        for index in range(controller.length):
            seed = (frame * 48271 + index * 69621) & 0xFFFFFFFF
            if seed % 10_000 < spawn_threshold:
                self.levels[index] = min(
                    1.0, self.levels[index] + 0.5 + reactive.high * 0.25
                )
                self.hues[index] = (seed // 100 + index * 13) % 360
            level = float(self.levels[index])
            if level > 0.015:
                alpha = min(1.0, 0.12 + level * 0.78)
                controller.set_pixel(
                    index,
                    neon_color(int(self.hues[index]), alpha=alpha, lightness=64),
                )
