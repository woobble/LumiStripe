from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .club_utils import strip_ratio
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class RainbowCycle(Animation):
    @property
    def name(self) -> str:
        return "rainbow_cycle"

    def tick(self, frame: int, controller: Controller) -> None:
        base = frame % 256
        wave = sin(frame * 0.035) * 10.0
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            hue = (base + int(pos * 256.0) + int(wave)) % 256
            light = 48 + int((sin(frame * 0.06 + pos * 6.0) * 0.5 + 0.5) * 4.0)
            controller.set_pixel(i, Hsla(hue, 100, light, 1.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = max(reactive.speed(0.35, 2.2), 0.1)
        base = reactive.hue_shift(frame, speed)
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            local = reactive.band_window(audio, i, controller.length, span=1)
            hue = (base + int(pos * 256.0) + int(local * 22.0)) % 256
            light = 48 + int(reactive.low * 6.0 + local * 6.0 + reactive.beat_pulse(0.0, 0.35) * 8.0)
            controller.set_pixel(i, Hsla(hue, 100, min(light, 64), 1.0))
