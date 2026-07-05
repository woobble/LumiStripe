from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .club_utils import strip_ratio
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Wave(Animation):
    @property
    def name(self) -> str:
        return "wave"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            phase = pos * 5.5 + frame * 0.28
            crest = sin(phase) * 0.5 + 0.5
            hue = ((frame + i) * 2) % 256
            alpha = 0.22 + crest * 0.78
            controller.set_pixel(i, Rgba(0, hue, 255, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = reactive.speed(0.16, 0.62)
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            amplitude = 0.24 + reactive.band_window(audio, i, controller.length, span=1) * 0.76
            phase = pos * 5.5 + frame * speed + reactive.low * 2.0
            crest = (sin(phase) * 0.5 + 0.5) * amplitude
            hue = reactive.hue_shift(frame + i, 0.08)
            alpha = 0.22 + crest * 0.78
            light = min(56 + int(reactive.mid * 8.0 + crest * 6.0), 68)
            controller.set_pixel(i, reactive.accent_color((160 + hue // 6) % 256, 100, light, alpha))
