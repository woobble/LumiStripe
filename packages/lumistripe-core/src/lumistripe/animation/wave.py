from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Wave(Animation):
    @property
    def name(self) -> str:
        return "wave"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            phase = i * 0.25 + frame * 0.32
            intensity = sin(phase) * 0.5 + 0.5
            hue = ((frame + i) * 2) % 256
            controller.set_pixel(i, Rgba(0, hue, 255, 0.2 + intensity * 0.8))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = reactive.speed(0.18, 0.65)
        for i in range(controller.length):
            amplitude = 0.25 + reactive.band_at(audio, i, controller.length) * 0.75
            phase = i * 0.25 + frame * speed
            intensity = (sin(phase) * 0.5 + 0.5) * amplitude
            hue = reactive.hue_shift(frame + i, 0.08)
            alpha = 0.2 + intensity * 0.8
            controller.set_pixel(i, reactive.accent_color((160 + hue // 6) % 256, 100, 56, alpha))
