from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla, Rgba
from ..controller import Controller
from ..render import (
    native_hsl_solid_available,
    native_solid_render_available,
    render_hsl_solid_into,
    render_solid_rgba_into,
)
from .base import Effect


@dataclass(slots=True)
class HardBeat(Effect):
    native_render_enabled = True

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

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_solid_render_available():
            return False
        if audio is None:
            period = 8
            on_frames = max(1, round(period * self.setting("duty_cycle", 30) / 100.0))
            intensity = 1.0 if frame % period < on_frames else 0.0
            hue = (frame * 10) % 256
            light = 70
        else:
            if not native_hsl_solid_available():
                return False
            reactive = AudioReactive.from_frame(audio)
            if audio.beat:
                self.flash.value = min(self.flash.value + 0.9, 1.0)
                self.hue = (self.hue + 43) % 256
            decay = self.setting("decay", 50) / 100.0
            release = (
                0.005
                + decay * 0.08
                + (1.0 - self.setting("duty_cycle", 30) / 100.0) * 0.04
            )
            intensity = self.flash.step(0.0, release + reactive.high * 0.04)
            hue = self.hue
            light = int(50 + reactive.mid * 20 + intensity * 20)
        if audio is not None and intensity < 0.01:
            return render_solid_rgba_into(destination, (0, 0, 0, 0))
        if audio is None and intensity == 0.0:
            return render_solid_rgba_into(destination, (0, 0, 0, 0))
        return render_hsl_solid_into(
            destination,
            hue,
            100.0,
            float(light),
            intensity,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
        )
