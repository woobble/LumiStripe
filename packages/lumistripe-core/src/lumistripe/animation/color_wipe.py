from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Color, Hex, Hsla
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class ColorWipe(Animation):
    head: float = 0.0
    speed: float = 0.85
    palette_index: int = 0
    palette: tuple[Color, Color, Color, Color] = field(
        default_factory=lambda: (Hex(0xFF004D), Hex(0x00E5FF), Hex(0xFFE600), Hex(0x00FF6A))
    )

    @property
    def name(self) -> str:
        return "color_wipe"

    def tick(self, frame: int, controller: Controller) -> None:
        length = max(controller.length, 1)
        self.head += self.speed
        if self.head >= length:
            self.head = 0.0
            self.palette_index = (self.palette_index + 1) % len(self.palette)
        color = self.palette[self.palette_index]
        hue, sat, light, _ = color.to_rgba()
        for i in range(controller.length):
            distance = self.head - i
            if distance >= 0.0:
                fade = max(0.0, 1.0 - min(distance / 5.0, 1.0))
                controller.set_pixel(i, Hsla(hue, sat, min(46 + int(fade * 18.0), 64), 1.0))
            else:
                controller.set_pixel(i, Hsla(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        length = max(controller.length, 1)
        self.speed = max(0.6, reactive.speed(0.7, 3.2))
        self.head += self.speed
        if self.head >= length:
            self.head = 0.0
            self.palette_index = (self.palette_index + 1) % len(self.palette)
        if audio.beat or reactive.accent > 0.65:
            self.palette_index = (self.palette_index + 1) % len(self.palette)
        hue, sat, light, _ = self.palette[self.palette_index].to_rgba()
        boost = reactive.beat_pulse(0.0, 0.4)
        local = reactive.band_window(audio, int(self.head), length, 1)
        for i in range(controller.length):
            distance = self.head - i
            if distance >= 0.0:
                fade = max(0.0, 1.0 - min(distance / (3.0 + reactive.low * 4.0), 1.0))
                lightness = min(44 + int((fade * 16.0) + local * 12.0 + boost * 10.0), 66)
                controller.set_pixel(i, Hsla(hue, sat, lightness, 1.0))
            else:
                controller.set_pixel(i, Hsla(0, 0, 0, 0.0))
