from __future__ import annotations

from dataclasses import dataclass, field
from math import sin

import numpy as np

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .club_utils import strip_ratio
from .reactive import AudioReactive


@dataclass(slots=True)
class Twinkle(Animation):
    _bloom: float = 0.0
    levels: np.ndarray = field(init=False, repr=False)
    hues: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.levels = np.zeros((0,), dtype=np.float32)
        self.hues = np.zeros((0,), dtype=np.int16)

    @property
    def name(self) -> str:
        return "twinkle"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(
            AudioFrame(rms=0.12, bands=(0.08, 0.09, 0.1, 0.11, 0.12, 0.16, 0.2, 0.22))
        )
        self._render(frame, controller, reactive)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._bloom = min(1.0, max(self._bloom, 0.2 + reactive.accent * 0.75))
        else:
            self._bloom *= 0.92

        self._render(frame, controller, reactive)

    def _render(self, frame: int, controller: Controller, reactive: AudioReactive) -> None:
        length = controller.length
        if len(self.levels) != length:
            self.levels = np.zeros(length, dtype=np.float32)
            self.hues = np.zeros(length, dtype=np.int16)
        self.levels *= 0.92 - reactive.high * 0.025
        spawn_threshold = int(
            (0.004 + reactive.drive() * 0.012 + reactive.shimmer() * 0.018)
            * 10_000.0
        )
        for index in range(length):
            seed = (frame * 1103515245 + index * 2654435761) & 0xFFFFFFFF
            if seed % 10_000 < spawn_threshold:
                self.levels[index] = min(1.0, self.levels[index] + 0.45 + reactive.high * 0.2)
                self.hues[index] = (154 + seed % 24 + int(reactive.low * 12.0)) % 360

            pos = strip_ratio(index, length)
            drift = sin(frame * 0.018 + pos * 4.2) * 0.5 + 0.5
            level = float(self.levels[index])
            hue = int(self.hues[index]) if level > 0.01 else 154 + int(drift * 8.0)
            alpha = min(0.05 + drift * 0.03 + level * 0.38 + self._bloom * 0.08, 0.5)
            lightness = 28 + int(drift * 5.0 + level * 34.0)
            controller.set_pixel(index, Hsla(hue, 65, lightness, alpha))
