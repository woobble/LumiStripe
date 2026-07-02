from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class DanceFloor(Animation):
    @property
    def name(self) -> str:
        return "dance_floor"

    def tick(self, frame: int, controller: Controller) -> None:
        segments = max(controller.length // 8, 1)
        seg_len = max(controller.length // segments, 1)
        for seg in range(segments):
            hue = (seg * 40 + frame * 2) % 256
            light = 40 + int((1.0 + __import__("math").sin(seg * 1.5 + frame * 0.08)) * 15)
            for j in range(seg_len):
                i = seg * seg_len + j
                if i < controller.length:
                    controller.set_pixel(i, Hsla(hue, 100, light, 1.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        segments = max(controller.length // 8, 1)
        seg_len = max(controller.length // segments, 1)
        for seg in range(segments):
            hue = (seg * 40 + reactive.hue_shift(frame, 0.15)) % 256
            beat_boost = reactive.accent if audio.beat else 0.0
            light = int(30 + reactive.mid * 20 + seg / segments * reactive.high * 25 + beat_boost * 25)
            alpha = 0.6 + reactive.drive() * 0.4 + beat_boost * 0.3
            for j in range(seg_len):
                i = seg * seg_len + j
                if i < controller.length:
                    controller.set_pixel(i, Hsla(hue, 100, light, min(alpha, 1.0)))
