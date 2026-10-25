from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class RainbowStrobe(Animation):
    @property
    def name(self) -> str:
        return "rainbow_strobe"

    def tick(self, frame: int, controller: Controller) -> None:
        period = max(2, round(24 - self.setting("flash_rate", 50) * 0.2))
        on_frames = max(1, round(period * self.setting("duty_cycle", 30) / 100.0))
        burst = frame % period
        hue = (frame * 17) % 256
        on = burst < on_frames
        alpha = 1.0 if on else 0.0
        light = int(56 + self.setting("intensity", 100) * 0.14) if on else 0
        spread = 0.35 + self.setting("color_spread", 50) / 100.0 * 1.3
        for i in range(controller.length):
            if on:
                controller.set_pixel(i, Hsla(int(hue + i * spread) % 256, 100, light, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        period = max(2, round(24 - self.setting("flash_rate", 50) * 0.2))
        on_duration = max(1, round(period * self.setting("duty_cycle", 30) / 100.0))
        cycle = 0 if audio.beat else frame % period
        hue = reactive.hue_shift(frame, 2.0)
        on = cycle < on_duration
        alpha = 1.0 if on else 0.0
        light = min(75, int(50 + self.setting("intensity", 100) * 0.18 + reactive.high * 8)) if on else 0
        spread = 0.35 + self.setting("color_spread", 50) / 100.0 * 1.3
        for i in range(controller.length):
            if on:
                controller.set_pixel(i, Hsla(int(hue + i * spread) % 256, 100, light, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
