from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsl, Rgba
from ..controller import Controller
from ..render import native_disco_sparkle_available, render_disco_sparkle_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class DiscoSparkle(Animation):
    native_render_enabled = True
    _intensities: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self._intensities = np.zeros((0,), dtype=np.float32)

    @property
    def name(self) -> str:
        return "disco_sparkle"

    def tick(self, frame: int, controller: Controller) -> None:
        if len(self._intensities) != controller.length:
            self._intensities = np.zeros(controller.length, dtype=np.float32)
        fade = self.setting("fade_time", 50) / 100.0
        density = self.setting("density", 40) / 100.0
        variation = self.setting("variation", 50) / 100.0
        self._intensities *= 0.65 + fade * 0.4
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            if (seed % 100) < 5 + int(density * 65.0):
                self._intensities[i] = min(self._intensities[i] + 0.7, 1.0)
            if self._intensities[i] > 0.05:
                hue = ((seed // 100) + frame * 3 + int(((seed % 21) - 10) * variation)) % 256
                controller.set_pixel(i, Hsl(hue, 100, 50))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if len(self._intensities) != controller.length:
            self._intensities = np.zeros(controller.length, dtype=np.float32)
        fade = self.setting("fade_time", 50) / 100.0
        density_setting = self.setting("density", 40) / 100.0
        variation = self.setting("variation", 50) / 100.0
        decay = 0.58 + fade * 0.27 + reactive.high * 0.12
        self._intensities *= decay
        density = int(4.0 + density_setting * 35.0 + reactive.drive() * 38.0 + reactive.shimmer() * 22.0)
        for i in range(controller.length):
            seed = (frame * 1103515245 + i * 12345) & 0xFFFFFFFFFFFFFFFF
            trigger = reactive.accent > 0.5 or (seed % 100) < density
            if trigger:
                self._intensities[i] = min(self._intensities[i] + 0.8, 1.0)
            if self._intensities[i] > 0.05:
                hue = ((seed // 100) + int(frame * reactive.speed(0.1, 0.6)) + int(((seed % 21) - 10) * variation)) % 256
                lightness = int(40 + self._intensities[i] * 25 + reactive.pulse(0.0, 10))
                controller.set_pixel(i, Hsl(hue, 100, lightness))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_disco_sparkle_available():
            return False
        if len(self._intensities) != destination.shape[0]:
            self._intensities = np.zeros(destination.shape[0], dtype=np.float32)
        fade = self.setting("fade_time", 50) / 100.0
        density_setting = self.setting("density", 40) / 100.0
        variation = self.setting("variation", 50) / 100.0
        if audio is None:
            audio_mode = False
            density = 5 + int(density_setting * 65.0)
            accent_trigger = False
            decay = 0.65 + fade * 0.4
            hue_rate = 3.0
            pulse = 0.0
        else:
            reactive = AudioReactive.from_frame(audio)
            audio_mode = True
            density = int(
                4.0
                + density_setting * 35.0
                + reactive.drive() * 38.0
                + reactive.shimmer() * 22.0
            )
            accent_trigger = reactive.accent > 0.5
            decay = 0.58 + fade * 0.27 + reactive.high * 0.12
            hue_rate = reactive.speed(0.1, 0.6)
            pulse = reactive.pulse(0.0, 10.0)
        return render_disco_sparkle_into(
            destination,
            self._intensities,
            frame,
            audio_mode,
            density,
            accent_trigger,
            float(decay),
            float(variation),
            float(hue_rate),
            float(pulse),
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
