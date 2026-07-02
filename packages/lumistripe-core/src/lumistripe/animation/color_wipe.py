from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Color, Hex, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class ColorWipe(Animation):
    palette: tuple[Color, Color, Color, Color] = field(
        default_factory=lambda: (Hex(0xFF004D), Hex(0x00E5FF), Hex(0xFFE600), Hex(0x00FF6A))
    )

    @property
    def name(self) -> str:
        return "color_wipe"

    def tick(self, frame: int, controller: Controller) -> None:
        head = frame % max(controller.length, 1)
        color = self.palette[(frame // 60) % len(self.palette)]
        for i in range(controller.length):
            controller.set_pixel(i, color if i <= head else Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed = max(reactive.speed(0.9, 3.6), 0.9)
        head = int(frame / speed) % max(controller.length, 1)
        palette_idx = int(frame * (0.04 + reactive.high * 0.08)) % len(self.palette)
        color = self.palette[palette_idx]
        r, g, b, _ = color.to_rgba()
        for i in range(controller.length):
            if i <= head:
                controller.set_pixel(i, Rgba(r, g, b, 1.0))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
