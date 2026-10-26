from __future__ import annotations

from dataclasses import dataclass, field
from math import sin

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla
from ..controller import Controller
from ..render import native_hsl_solid_available, render_hsl_solid_into
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class RavePulse(Animation):
    native_render_enabled = True

    flash: Decay = field(default_factory=Decay)
    hue: int = 0

    @property
    def name(self) -> str:
        return "rave_pulse"

    def tick(self, frame: int, controller: Controller) -> None:
        wave = (1.0 + sin(frame * 0.15)) * 0.5
        alpha = self._curve(wave)
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
        alpha = self._curve(min(drive, 1.0))
        light = int(40 + reactive.mid * 20 + self.flash.value * 20)
        for i in range(controller.length):
            controller.set_pixel(i, Hsla(self.hue, 100, light, alpha))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_hsl_solid_available() or self.setting("palette", "rainbow") != "rainbow":
            return False
        if audio is None:
            wave = (1.0 + sin(frame * 0.15)) * 0.5
            alpha = self._curve(wave)
            hue = (frame * 4) % 256
            light = 55
        else:
            reactive = AudioReactive.from_frame(audio)
            self.flash.step(reactive.accent, 0.08)
            if audio.beat:
                self.flash.value = min(self.flash.value + 0.6, 1.0)
                self.hue = (self.hue + 37) % 256
            drive = self.flash.value + reactive.drive()
            alpha = self._curve(min(drive, 1.0))
            hue = self.hue
            light = int(40 + reactive.mid * 20 + self.flash.value * 20)
        return render_hsl_solid_into(
            destination,
            hue,
            100.0,
            float(light),
            alpha,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
        )

    def _curve(self, value: float) -> float:
        curve = self.setting("pulse_curve", "sine")
        if curve == "sharp":
            return value**2.2
        if curve == "smoothstep":
            return value * value * (3.0 - 2.0 * value)
        return value * value
