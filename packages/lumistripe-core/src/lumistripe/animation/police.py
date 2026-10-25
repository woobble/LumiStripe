from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Rgb
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Police(Animation):
    @property
    def name(self) -> str:
        return "police"

    def tick(self, frame: int, controller: Controller) -> None:
        half = controller.length // 2
        pattern = self.setting("pattern", "alternate")
        period = max(2, round(24 - self.setting("speed", 50) * 0.16))
        on_frames = max(1, round(period * self.setting("duty_cycle", 30) / 100.0))
        phase = frame % period
        flash = phase < on_frames
        for i in range(controller.length):
            if flash:
                if pattern == "solid":
                    controller.set_pixel(i, Rgb(255, 0, 0))
                elif pattern == "double" and phase < on_frames // 2:
                    controller.set_pixel(i, Rgb(255, 255, 255))
                else:
                    controller.set_pixel(i, Rgb(255, 0, 0) if i < half else Rgb(0, 0, 255))
            else:
                if pattern == "solid":
                    controller.set_pixel(i, Rgb(0, 0, 0))
                else:
                    controller.set_pixel(i, Rgb(0, 0, 255) if i < half else Rgb(255, 0, 0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        half = controller.length // 2
        reactive = AudioReactive.from_frame(audio)
        pattern = self.setting("pattern", "alternate")
        rate = max(2, int(reactive.speed(2.0, 10.0)))
        on_frames = max(1, round(rate * self.setting("duty_cycle", 30) / 100.0))
        flash = audio.beat or frame % rate < on_frames
        for i in range(controller.length):
            if flash:
                if audio.beat:
                    r, g, b = 255, 255, 255
                elif pattern == "solid":
                    r, g, b = 255, 0, 0
                elif pattern == "double" and frame % (rate * 2) < on_frames:
                    r, g, b = 255, 255, 255
                elif i < half:
                    r, g, b = 255, 0, 0
                else:
                    r, g, b = 0, 0, 255
            else:
                if pattern == "solid":
                    r, g, b = 0, 0, 0
                else:
                    r, g, b = (0, 0, 255) if i < half else (255, 0, 0)
            controller.set_pixel(i, Rgb(r, g, b))
