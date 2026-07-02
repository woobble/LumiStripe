from __future__ import annotations

from dataclasses import dataclass, field
from random import Random

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


def _render_bolt(controller: Controller, start: int, length: int, hue: int, alpha: float) -> None:
    pos = start
    segments = max(length // 3, 1)
    for _ in range(segments):
        if pos < 0 or pos >= controller.length:
            break
        controller.set_pixel(pos, Hsla(hue, 80, 70, alpha))
        step = 1 if pos < start + length else -1
        pos += step
        if pos >= 0 and pos < controller.length:
            controller.set_pixel(pos, Hsla(hue, 80, 60, alpha * 0.7))


@dataclass(slots=True)
class LightningStrike(Animation):
    flash: Decay = field(default_factory=Decay)
    strike_pos: int = 0
    strike_len: int = 0
    hue: int = 200
    _rng: Random = field(default_factory=Random)

    @property
    def name(self) -> str:
        return "lightning_strike"

    def tick(self, frame: int, controller: Controller) -> None:
        if frame % 45 < 3:
            self.flash.value = 1.0
            self.strike_pos = (frame * 17) % max(controller.length, 1)
            self.strike_len = 3 + (frame % 8)
            self.hue = (frame * 50) % 256
        flash_alpha = self.flash.step(0.0, 0.06)
        all_flash = max(flash_alpha * 2.0 - 0.5, 0.0)
        for i in range(controller.length):
            dist = abs(i - self.strike_pos)
            if dist < self.strike_len and flash_alpha > 0.1:
                intensity = 1.0 - dist / max(self.strike_len, 1)
                controller.set_pixel(i, Hsla(self.hue, 80, 70, intensity * flash_alpha))
            elif all_flash > 0.0:
                controller.set_pixel(i, Hsla(self.hue, 50, 80, all_flash * 0.4))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self.flash.value = min(self.flash.value + 0.8, 1.0)
            self.strike_pos = self._rng.randint(0, max(controller.length - 1, 0))
            self.strike_len = int(3 + reactive.drive() * 8 + reactive.shimmer() * 5)
            self.hue = (190 - int(reactive.low * 40)) % 256
        flash_alpha = self.flash.step(0.0, 0.035 + reactive.high * 0.035)
        all_flash = max(flash_alpha * 2.0 - 0.5, 0.0)
        for i in range(controller.length):
            dist = abs(i - self.strike_pos)
            if dist < self.strike_len and flash_alpha > 0.05:
                intensity = 1.0 - dist / max(self.strike_len, 1)
                intensity = max(intensity, 0.0)
                controller.set_pixel(i, Hsla(self.hue, 80, 70, intensity * flash_alpha))
            elif all_flash > 0.0:
                controller.set_pixel(i, Hsla(self.hue, 50, 80, all_flash * 0.4))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
