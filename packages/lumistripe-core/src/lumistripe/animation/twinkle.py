from __future__ import annotations

from dataclasses import dataclass, field
from math import sin

import numpy as np

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla
from ..controller import Controller
from ..render import native_twinkle_available, render_twinkle_into
from .base import Animation
from .club_utils import strip_ratio
from .reactive import AudioReactive


@dataclass(slots=True)
class Twinkle(Animation):
    native_render_enabled = True
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
        radius = self._prepare_frame(frame, length, reactive)
        for index in range(length):
            pos = strip_ratio(index, length)
            drift = sin(frame * 0.018 + pos * 4.2) * 0.5 + 0.5
            level = float(self.levels[index])
            if radius > 1:
                left = max(index - radius, 0)
                right = min(index + radius + 1, length)
                level = max(
                    level,
                    max(
                        float(self.levels[neighbor])
                        * (1.0 - abs(neighbor - index) / (radius + 1.0))
                        for neighbor in range(left, right)
                    ),
                )
            hue = int(self.hues[index]) if level > 0.01 else 154 + int(drift * 8.0)
            alpha = min(0.05 + drift * 0.03 + level * 0.38 + self._bloom * 0.08, 0.5)
            lightness = 28 + int(drift * 5.0 + level * 34.0)
            controller.set_pixel(index, Hsla(hue, 65, lightness, alpha))

    def _prepare_frame(
        self, frame: int, length: int, reactive: AudioReactive
    ) -> int:
        if len(self.levels) != length:
            self.levels = np.zeros(length, dtype=np.float32)
            self.hues = np.zeros(length, dtype=np.int16)
        fade = self.setting("fade_time", 50) / 100.0
        density = self.setting("density", 40) / 100.0
        particle_size = self.setting("particle_size", 35) / 100.0
        self.levels *= max(0.68, 0.80 + fade * 0.16 - reactive.high * 0.02)
        spawn_threshold = int(
            (0.001 + density * 0.025 + reactive.drive() * 0.008 + reactive.shimmer() * 0.012)
            * 10_000.0
        )
        radius = max(0, round(length * particle_size * 0.04))
        for index in range(length):
            seed = (frame * 1103515245 + index * 2654435761) & 0xFFFFFFFF
            if seed % 10_000 < spawn_threshold:
                self.levels[index] = min(1.0, self.levels[index] + 0.45 + reactive.high * 0.2)
                self.hues[index] = (154 + seed % 24 + int(reactive.low * 12.0)) % 360
        return radius

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_twinkle_available():
            return False
        if audio is None:
            reactive = AudioReactive.from_frame(
                AudioFrame(rms=0.12, bands=(0.08, 0.09, 0.1, 0.11, 0.12, 0.16, 0.2, 0.22))
            )
        else:
            reactive = AudioReactive.from_frame(audio)
            if audio.beat:
                self._bloom = min(1.0, max(self._bloom, 0.2 + reactive.accent * 0.75))
            else:
                self._bloom *= 0.92
        radius = self._prepare_frame(frame, destination.shape[0], reactive)
        configuration = getattr(self, "_animation_configuration", {})
        palette_name = str(configuration.get("palette", "rainbow"))
        palette = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette_name)
        if palette is None:
            return False
        custom = str(configuration.get("custom_color", "#7C3AED"))
        try:
            custom_rgb = int(custom[1:], 16)
        except (ValueError, IndexError):
            custom_rgb = 0x7C3AED
        output_brightness = max(0.0, min(1.0, float(self._output_brightness(brightness))))
        return render_twinkle_into(
            destination,
            self.levels,
            self.hues,
            frame,
            radius,
            self._bloom,
            output_brightness,
            palette,
            custom_rgb,
        )
