from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, sin

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .club_utils import strip_ratio
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class Aurora(Animation):
    glow: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "aurora"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            phase = frame * 0.038 + pos * 7.5
            curtain = (sin(phase) * 0.5 + 0.5) * 0.58 + (sin(phase * 0.55 + 1.4) * 0.5 + 0.5) * 0.22
            intensity = min(0.12 + curtain, 1.0)
            hue = int(132.0 + cos(phase * 0.75) * 24.0 + pos * 10.0)
            controller.set_pixel(i, Hsla(hue, 100, 56, intensity))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        beat_glow = self.glow.step(reactive.accent, 0.045)
        speed = reactive.speed(0.028, 0.085)
        hue_offset = reactive.hue_shift(frame, 0.35)
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            local = reactive.band_window(audio, i, controller.length, span=1)
            low_wave = sin(frame * speed + pos * 5.8 + reactive.low * 4.0)
            mid_wave = sin(frame * (speed * 0.9) + pos * 8.5 + reactive.mid * 5.0)
            high_wave = cos(frame * (speed * 1.7) - pos * 11.5 + reactive.high * 7.0)
            curtain = (low_wave * 0.38 + mid_wave * 0.34 + high_wave * 0.28) * 0.5 + 0.5
            alpha = min(0.12 + curtain * 0.72 + beat_glow * 0.2, 1.0)
            hue = (hue_offset + 120 + int(pos * 34.0) + int(reactive.mid * 16.0) + int(local * 10.0)) % 256
            light = min(int(50.0 + reactive.high * 12.0 + local * 8.0 + beat_glow * 8.0), 68)
            controller.set_pixel(i, reactive.accent_color(hue, 90, light, alpha))
