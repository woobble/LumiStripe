from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsl, Hsla
from ..controller import Controller
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
            controller.set_pixel(i, Hsl(i * 256 // max(controller.length, 1), 100, 50))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._beat_flash = 1.0
        self._beat_flash *= 0.88

        brightness = 0.4 + reactive.rms * 0.6
        sat = int(60.0 + reactive.low * 40.0)
        offset = reactive.hue_shift(frame, 0.15)
        for i in range(controller.length):
            hue = (i * 256 // max(controller.length, 1) + offset + int(reactive.band_at(audio, i, controller.length) * 40.0)) % 256
            alpha = min(brightness + self._beat_flash * 0.4, 1.0)
            controller.set_pixel(i, Hsla(hue, sat, 50, alpha))
