from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Strobe(Animation):
    @property
    def name(self) -> str:
        return "strobe"

    def tick(self, frame: int, controller: Controller) -> None:
        period = max(2, round(28 - self.setting("flash_rate", 50) * 0.24))
        on_frames = max(1, round(period * self.setting("duty_cycle", 30) / 160.0))
        burst = frame % period
        hue = (frame * 11) % 256
        alpha = 1.0 if burst < on_frames else 0.0
        color = Rgba(255, hue, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0)
        for i in range(controller.length):
            controller.set_pixel(i, color)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        period = max(2, round(28 - self.setting("flash_rate", 50) * 0.24))
        on_duration = max(1, round(period * self.setting("duty_cycle", 30) / 160.0))
        cycle = 0 if audio.beat else frame % period
        hue = reactive.hue_shift(frame, 1.2)
        alpha = 1.0 if cycle < on_duration else 0.0
        color = Rgba(255, hue, 255, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0)
        for i in range(controller.length):
            controller.set_pixel(i, color)
