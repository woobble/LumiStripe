from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Rgb
from ..controller import Controller
from ..render import native_fire_available, render_fire_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Fire(Animation):
    native_render_enabled = True

    heat: np.ndarray = field(init=False, repr=False)
    _next_heat: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.heat = np.zeros((0,), dtype=np.float32)
        self._next_heat = np.zeros((0,), dtype=np.float32)

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

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_fire_available():
            return False
        length = len(destination)
        if len(self.heat) != length:
            self.heat = np.zeros(length, dtype=np.float32)
            self._next_heat = np.zeros(length, dtype=np.float32)
        if audio is None:
            drive, bass, shimmer, accent = 0.18, 0.08, 0.08, 0.0
        else:
            reactive = AudioReactive.from_frame(audio)
            drive, bass, shimmer, accent = (
                reactive.drive(),
                reactive.low,
                reactive.high,
                reactive.accent,
            )
        rendered = render_fire_into(
            destination,
            self.heat,
            self._next_heat,
            frame,
            self.setting("cooling", 50) / 100.0,
            self.setting("sparking", 35) / 100.0,
            drive,
            bass,
            shimmer,
            accent,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
        )
        if rendered:
            self.heat, self._next_heat = self._next_heat, self.heat
        return rendered

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
            self._next_heat = np.zeros(length, dtype=np.float32)
        if length == 0:
            return

        seed_base = frame * 2654435761
        new_heat = self._next_heat
        cooling = self.setting("cooling", 50) / 100.0
        sparking = self.setting("sparking", 35) / 100.0
        retention = 0.94 - cooling * 0.16 + drive * 0.04
        spark_threshold = 0.96 - sparking * 0.58 - shimmer * 0.10 - accent * 0.06
        spark_strength = 0.10 + sparking * 0.34 + drive * 0.18 + accent * 0.08
        fuel = 0.015 + bass * 0.025
        for i in range(length):
            seed = (seed_base + i * 314159) & 0xFFFFFFFFFFFFFFFF
            noise = ((seed >> 16) & 0xFF) / 255.0
            left = self.heat[i - 1] if i > 0 else self.heat[i]
            right = self.heat[i + 1] if i + 1 < length else self.heat[i]
            spread = self.heat[i] * 0.68 + (left + right) * 0.14
            spark = max(0.0, noise - spark_threshold) * spark_strength
            new_heat[i] = min(1.0, spread * retention + spark + fuel)
        self.heat, self._next_heat = new_heat, self.heat

        for i in range(length):
            controller.set_pixel(i, _fire_color(float(self.heat[i])))


def _fire_color(heat: float) -> Rgb:
    intensity = max(0.0, min(1.0, (heat - 0.025) / 0.7))
    if intensity <= 0.0:
        return Rgb(0, 0, 0)
    if intensity < 0.35:
        phase = intensity / 0.35
        return Rgb(int(35 + phase * 140), int(phase * 15), 0)
    if intensity < 0.75:
        phase = (intensity - 0.35) / 0.4
        return Rgb(int(175 + phase * 80), int(15 + phase * 100), int(phase * 8))
    phase = (intensity - 0.75) / 0.25
    return Rgb(255, int(115 + phase * 100), int(8 + phase * 18))
