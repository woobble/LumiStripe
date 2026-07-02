from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Rgb
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Fire(Animation):
    @property
    def name(self) -> str:
        return "fire"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            seed = (frame * 2654435761 + i * 314159) & 0xFFFFFFFFFFFFFFFF
            flicker = ((seed >> 16) & 0xFF) / 255.0
            heat = 0.3 + flicker * 0.7
            controller.set_pixel(i, Rgb(255, int(64.0 + heat * 160.0), int(heat * 40.0)))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        for i in range(controller.length):
            seed = (frame * 2654435761 + i * 314159) & 0xFFFFFFFFFFFFFFFF
            flicker = ((seed >> 16) & 0xFF) / 255.0
            local = reactive.band_at(audio, i, controller.length)
            audio_heat = reactive.rms * 0.25 + local * 0.35 + reactive.accent * 0.2
            heat = min(0.3 + flicker * 0.7 + audio_heat, 1.0)
            r = 180 + int(heat * 75.0)
            g = 30 + int(heat * 180.0)
            b = int(reactive.high * 25.0 + heat * 60.0)
            controller.set_pixel(i, Rgb(r, g, b))
