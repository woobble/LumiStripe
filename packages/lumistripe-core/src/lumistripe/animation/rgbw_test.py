from __future__ import annotations

from dataclasses import dataclass

from ..color import Rgb
from ..controller import Controller
from .base import Animation


@dataclass(slots=True)
class RgbwTest(Animation):
    last_idx: int = 255

    @property
    def name(self) -> str:
        return "rgbw_test"

    def tick(self, frame: int, controller: Controller) -> None:
        idx = (frame // 250) % 4
        self.last_idx = idx
        color = (Rgb(255, 0, 0), Rgb(0, 255, 0), Rgb(0, 0, 255), Rgb(255, 255, 255))[idx]
        for i in range(controller.length):
            controller.set_pixel(i, color)
