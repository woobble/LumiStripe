from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class HardBeat(Animation):
    flash: Decay = field(default_factory=Decay)
    hue: int = 0

    @property
    def name(self) -> str:
        return "hard_beat"

    def tick(self, frame: int, controller: Controller) -> None:
        intensity = 1.0 if frame % 8 < 2 else 0.0
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
        alpha = self.flash.step(0.0, 0.12 + reactive.high * 0.06)
        if alpha < 0.01:
            for i in range(controller.length):
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
            return
        light = int(50 + reactive.mid * 20 + alpha * 20)
        for i in range(controller.length):
            controller.set_pixel(i, Hsla(self.hue, 100, light, alpha))
