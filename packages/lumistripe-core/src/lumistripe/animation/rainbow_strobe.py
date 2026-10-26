from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla, Rgba
from ..controller import Controller
from ..render import native_rainbow_pattern_available, render_rainbow_pattern_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class RainbowStrobe(Animation):
    native_render_enabled = True

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

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_rainbow_pattern_available():
            return False
        period = max(2, round(24 - self.setting("flash_rate", 50) * 0.2))
        duration = max(1, round(period * self.setting("duty_cycle", 30) / 100.0))
        if audio is None:
            cycle = frame % period
            hue = (frame * 17) % 256
            light = int(56 + self.setting("intensity", 100) * 0.14)
        else:
            reactive = AudioReactive.from_frame(audio)
            cycle = 0 if audio.beat else frame % period
            hue = reactive.hue_shift(frame, 2.0)
            light = min(75, int(50 + self.setting("intensity", 100) * 0.18 + reactive.high * 8))
        on = cycle < duration
        spread = 0.35 + self.setting("color_spread", 50) / 100.0 * 1.3
        return render_rainbow_pattern_into(
            destination,
            2,
            frame,
            None,
            float(spread),
            float(hue),
            100.0,
            1.0 if on else 0.0,
            0.0,
            float(light),
            0.0,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
