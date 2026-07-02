from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, sin

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
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
            pos = i / max(controller.length, 1)
            phase = frame * 0.045 + pos * 8.0
            intensity = (sin(phase) * 0.5 + 0.5) * 0.7 + 0.15
            hue = int(135.0 + cos(phase) * 28.0)
            controller.set_pixel(i, Hsla(hue, 100, 58, min(intensity, 1.0)))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        beat_glow = self.glow.step(reactive.accent, 0.045)
        speed = reactive.speed(0.03, 0.08)
        hue_offset = reactive.hue_shift(frame, 0.35)
        for i in range(controller.length):
            pos = i / max(controller.length, 1)
            local = reactive.band_at(audio, i, controller.length)
            low_wave = sin(frame * speed + pos * 6.0 + reactive.low * 4.0)
            high_wave = cos(frame * (speed * 1.8) - pos * 10.0 + reactive.high * 8.0)
            curtain = (low_wave * 0.55 + high_wave * 0.45) * 0.5 + 0.5
            intensity = min(curtain * 0.7 + 0.15, 1.0)
            alpha = intensity
            hue = (hue_offset + 120 + int(pos * 36.0) + int(reactive.mid * 18.0)) % 256
            light = min(int(52.0 + reactive.high * 10.0 + local * 6.0 + beat_glow * 6.0), 68)
            controller.set_pixel(i, reactive.accent_color(hue, 90, light, alpha))
