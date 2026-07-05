from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsl, Hsla
from ..controller import Controller
from .club_utils import strip_ratio
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Rainbow(Animation):
    _beat_flash: float = 0.0

    @property
    def name(self) -> str:
        return "rainbow"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            hue = int(pos * 256.0 + frame * 1.5) % 256
            controller.set_pixel(i, Hsl(hue, 100, 50))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._beat_flash = 1.0
        self._beat_flash *= 0.88

        brightness = 0.42 + reactive.rms * 0.58
        sat = int(62.0 + reactive.low * 34.0)
        offset = reactive.hue_shift(frame, 0.15)
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            local = reactive.band_window(audio, i, controller.length, span=1)
            hue = (int(pos * 256.0) + offset + int(local * 44.0)) % 256
            alpha = min(brightness + self._beat_flash * 0.4, 1.0)
            light = 48 + int(reactive.mid * 6.0 + local * 6.0)
            controller.set_pixel(i, Hsla(hue, sat, light, alpha))
