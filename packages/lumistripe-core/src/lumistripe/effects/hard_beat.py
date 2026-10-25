from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Effect


@dataclass(slots=True)
class HardBeat(Effect):
    flash: Decay = field(default_factory=Decay)
    hue: int = 0

    @property
    def name(self) -> str:
        return "hard_beat"

    def tick(self, frame: int, controller: Controller) -> None:
        period = 8
        on_frames = max(1, round(period * self.setting("duty_cycle", 30) / 100.0))
        intensity = 1.0 if frame % period < on_frames else 0.0
        hue = (frame * 10) % 256
        for i in range(controller.length):
            if intensity > 0.0:
                controller.set_pixel(i, Hsla(hue, 100, 70, intensity))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self.flash.value = min(self.flash.value + 0.9, 1.0)
            self.hue = (self.hue + 43) % 256
        decay = self.setting("decay", 50) / 100.0
        release = 0.005 + decay * 0.08 + (1.0 - self.setting("duty_cycle", 30) / 100.0) * 0.04
        alpha = self.flash.step(0.0, release + reactive.high * 0.04)
        if alpha < 0.01:
            for i in range(controller.length):
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
            return
        light = int(50 + reactive.mid * 20 + alpha * 20)
        for i in range(controller.length):
            controller.set_pixel(i, Hsla(self.hue, 100, light, alpha))
