from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla
from ..controller import Controller
from ..render import native_plasma_rave_available, render_plasma_rave_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class PlasmaRave(Animation):
    native_render_enabled = True

    @property
    def name(self) -> str:
        return "plasma_rave"

    def tick(self, frame: int, controller: Controller) -> None:
        frequency = 0.5 + self.setting("frequency", 50) / 100.0
        for i in range(controller.length):
            p = i / max(controller.length - 1, 1)
            v1 = sin(p * 4.0 * frequency + frame * 0.05)
            v2 = sin(p * 8.0 * frequency - frame * 0.07 + 1.2)
            v3 = sin((p + frame * 0.01) * 12.0 * frequency)
            plasma = v1 + v2 + v3
            hue = int((plasma * 0.5 + 0.5) * 255 + frame * 2) % 256
            alpha = 0.4 + abs(plasma) * 0.3
            controller.set_pixel(i, Hsla(hue, 100, 55, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = reactive.speed(0.03, 0.12)
        frequency = 0.5 + self.setting("frequency", 50) / 100.0
        for i in range(controller.length):
            p = i / max(controller.length - 1, 1)
            v1 = sin(p * 4.0 * frequency + frame * speed * 0.8)
            v2 = sin(p * 8.0 * frequency - frame * speed * 1.2 + 1.2)
            v3 = sin((p + frame * 0.01 * speed) * 12.0 * frequency)
            plasma = v1 + v2 + v3
            hue = (reactive.hue_shift(frame, 0.15) + int((plasma * 0.5 + 0.5) * 180)) % 256
            alpha = 0.4 + abs(plasma) * 0.3 + reactive.pulse(0.0, 0.3)
            controller.set_pixel(i, Hsla(hue, 100, 55, min(alpha, 1.0)))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_plasma_rave_available():
            return False
        frequency = 0.5 + float(self.setting("frequency", 50)) / 100.0
        if audio is None:
            time_one = frame * 0.05
            time_two = -frame * 0.07 + 1.2
            time_three = frame * 0.01
            hue_scale = 255.0
            alpha_extra = 0.0
            hue_base = (frame * 2) % 256
        else:
            reactive = AudioReactive.from_frame(audio)
            speed = reactive.speed(0.03, 0.12)
            time_one = frame * speed * 0.8
            time_two = -frame * speed * 1.2 + 1.2
            time_three = frame * 0.01 * speed
            hue_base = reactive.hue_shift(frame, 0.15)
            hue_scale = 180.0
            alpha_extra = reactive.pulse(0.0, 0.3)
        return render_plasma_rave_into(
            destination,
            frequency,
            time_one,
            time_two,
            time_three,
            hue_base,
            hue_scale,
            alpha_extra,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            audio is not None,
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
