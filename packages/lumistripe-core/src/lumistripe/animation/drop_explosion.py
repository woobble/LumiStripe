from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class DropExplosion(Animation):
    charge: float = 0.0
    flash: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "drop_explosion"

    def tick(self, frame: int, controller: Controller) -> None:
        self.charge = min(self.charge + 0.006, 1.0)
        drop = frame % 80 < 3
        if drop:
            self.flash.value = 1.0
            self.charge = 0.0
        tension = self.charge
        preview = (frame % 20) / 20.0 if tension > 0.7 else 0.0
        flash_alpha = self.flash.value
        for i in range(controller.length):
            flicker = ((frame + i * 7) % 6) / 6.0
            if flash_alpha > 0.5:
                controller.set_pixel(i, Rgba(255, 255, 255, flash_alpha))
            elif tension > 0.7 and preview > 0.3:
                alpha = preview * 0.5 + flicker * 0.2
                controller.set_pixel(i, Rgba(80, 40, 20, alpha))
            elif tension > 0.3:
                pulse = ((frame * 3 + i) % 30) / 30.0
                alpha = tension * 0.15 + pulse * 0.08
                controller.set_pixel(i, Rgba(30, 10, 5, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        bass_energy = (audio.bands[0] + audio.bands[1]) * 0.5
        if audio.beat and bass_energy > 0.35:
            self.flash.value = 1.0
            self.charge = 0.0
        self.charge = min(self.charge + 0.004 + reactive.low * 0.014 + reactive.mid * 0.006, 1.0)
        tension = self.charge
        flash_alpha = self.flash.step(0.0, 0.04)
        preview = reactive.mid * 0.3 + reactive.rms * 0.2 if tension > 0.65 else 0.0
        for i in range(controller.length):
            flicker = ((frame + i * 7) % 5) / 5.0 * reactive.high * 0.3
            if flash_alpha > 0.4:
                warmth = int(reactive.low * 80 + 175)
                controller.set_pixel(i, Rgba(255, warmth, warmth // 2, flash_alpha))
            elif tension > 0.65 and preview > 0.2:
                alpha = preview * 0.6 + flicker
                warmth = int(40 + reactive.low * 60)
                controller.set_pixel(i, Rgba(warmth, warmth // 3, warmth // 6, min(alpha, 1.0)))
            elif tension > 0.3:
                pulse = ((frame * 2 + i) % 24) / 24.0
                alpha = tension * 0.12 + pulse * 0.06
                controller.set_pixel(i, Rgba(20, 8, 3, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
