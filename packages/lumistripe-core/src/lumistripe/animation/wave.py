from __future__ import annotations

from dataclasses import dataclass
from math import sin

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Rgba
from ..controller import Controller
from ..render import native_wave_available, render_wave_into
from .base import Animation
from .club_utils import strip_ratio
from .reactive import AudioReactive


@dataclass(slots=True)
class Wave(Animation):
    native_render_enabled = True

    @property
    def name(self) -> str:
        return "wave"

    def tick(self, frame: int, controller: Controller) -> None:
        frequency = 0.5 + self.setting("frequency", 50) / 100.0
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            phase = pos * 5.5 * frequency + frame * 0.28
            crest = sin(phase) * 0.5 + 0.5
            hue = ((frame + i) * 2) % 256
            alpha = 0.22 + crest * 0.78
            controller.set_pixel(i, Rgba(0, hue, 255, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = reactive.speed(0.16, 0.62)
        frequency = 0.5 + self.setting("frequency", 50) / 100.0
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            amplitude = 0.24 + reactive.band_window(audio, i, controller.length, span=1) * 0.76
            phase = pos * 5.5 * frequency + frame * speed + reactive.low * 2.0
            crest = (sin(phase) * 0.5 + 0.5) * amplitude
            hue = reactive.hue_shift(frame + i, 0.08)
            alpha = 0.22 + crest * 0.78
            light = min(56 + int(reactive.mid * 8.0 + crest * 6.0), 68)
            controller.set_pixel(i, reactive.accent_color((160 + hue // 6) % 256, 100, light, alpha))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_wave_available():
            return False
        frequency = 0.5 + self.setting("frequency", 50) / 100.0
        if audio is None:
            bands = None
            speed = low = mid = high = accent = 0.0
        else:
            reactive = AudioReactive.from_frame(audio)
            bands = audio.bands
            speed = reactive.speed(0.16, 0.62)
            low, mid, high, accent = reactive.low, reactive.mid, reactive.high, reactive.accent
        return render_wave_into(
            destination,
            frame,
            bands,
            float(frequency),
            float(speed),
            float(low),
            float(mid),
            float(high),
            float(accent),
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
