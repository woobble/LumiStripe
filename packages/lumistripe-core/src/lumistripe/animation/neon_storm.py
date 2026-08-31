from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive

_PALETTE = [(0, 100, 60), (200, 100, 55), (120, 100, 50), (300, 100, 60), (50, 100, 55)]


@dataclass(slots=True)
class NeonStorm(Animation):
    levels: np.ndarray = field(init=False, repr=False)
    hues: np.ndarray = field(init=False, repr=False)
    streak_positions: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.levels = np.zeros((0,), dtype=np.float32)
        self.hues = np.zeros((0,), dtype=np.int16)

    @property
    def name(self) -> str:
        return "neon_storm"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(
            AudioFrame(rms=0.22, bands=(0.65, 0.6, 0.2, 0.18, 0.16, 0.18, 0.22, 0.24))
        )
        self._render(frame, controller, reactive)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._render(frame, controller, reactive)

    def _render(self, frame: int, controller: Controller, reactive: AudioReactive) -> None:
        length = controller.length
        if len(self.levels) != length:
            self.levels = np.zeros(length, dtype=np.float32)
            self.hues = np.zeros(length, dtype=np.int16)
            self.streak_positions.clear()
        self.levels *= 0.82
        streak_count = min(1 + int(reactive.low * 3.0), 4)
        while len(self.streak_positions) < streak_count:
            self.streak_positions.append(
                (len(self.streak_positions) + 1) * length / (streak_count + 1)
            )
        self.streak_positions = self.streak_positions[:streak_count]
        for index in range(streak_count):
            speed = 0.35 + index * 0.18 + reactive.drive() * 1.1 + reactive.high * 0.5
            self.streak_positions[index] = (self.streak_positions[index] + speed) % max(length, 1)

        spawn_threshold = int(
            (0.006 + reactive.drive() * 0.018 + reactive.shimmer() * 0.016 + reactive.accent * 0.035)
            * 10_000.0
        )
        for index in range(length):
            seed = (frame * 48271 + index * 63689) & 0xFFFFFFFF
            if seed % 10_000 < spawn_threshold:
                self.levels[index] = min(1.0, self.levels[index] + 0.55)
                self.hues[index] = (seed // 100) % len(_PALETTE)

        controller.fill(Rgba(0, 0, 0, 0.0))
        for index in range(length):
            particle = float(self.levels[index])
            streak = 0.0
            streak_seed = 0
            for streak_index, position in enumerate(self.streak_positions):
                distance = min(abs(index - position), max(length - abs(index - position), 0))
                if distance < 3.5:
                    strength = 1.0 - distance / 3.5
                    if strength > streak:
                        streak = strength
                        streak_seed = streak_index
            alpha = min(1.0, particle * 0.8 + streak * (0.35 + reactive.drive() * 0.3))
            if alpha <= 0.01:
                continue
            palette_index = int(self.hues[index]) if particle >= streak else streak_seed
            hue, sat, light = _PALETTE[palette_index % len(_PALETTE)]
            controller.set_pixel(index, Hsla(hue, sat, light, alpha))
