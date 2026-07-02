from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class RavePulse(Animation):
    flash: Decay = field(default_factory=Decay)
    hue: int = 0

    @property
    def name(self) -> str:
        return "rave_pulse"

    def tick(self, frame: int, controller: Controller) -> None:
        intensity = (1.0 + __import__("math").sin(frame * 0.15)) * 0.5
        alpha = intensity * intensity
        hue = (frame * 4) % 256
        for i in range(controller.length):
            controller.set_pixel(i, Hsla(hue, 100, 55, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self.flash.step(reactive.accent, 0.08)
        if audio.beat:
            self.flash.value = min(self.flash.value + 0.6, 1.0)
            self.hue = (self.hue + 37) % 256
        drive = self.flash.value + reactive.drive()
        alpha = min(drive, 1.0)
        light = int(40 + reactive.mid * 20 + self.flash.value * 20)
        for i in range(controller.length):
            controller.set_pixel(i, Hsla(self.hue, 100, light, alpha))
