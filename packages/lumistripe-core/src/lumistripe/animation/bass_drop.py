from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class BassDrop(Animation):
    charge: float = 0.0
    flash: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "bass_drop"

    def tick(self, frame: int, controller: Controller) -> None:
        self.charge = min(self.charge + 0.008, 1.0)
        if frame % 60 < 2:
            self.flash.value = 1.0
            self.charge = 0.0
        tension = self.charge
        preview = (frame % 30) / 30.0 if tension > 0.6 else 0.0
        alpha = max(preview * tension * 0.4, self.flash.value)
        hue = (frame * 3) % 256
        color = Rgba(hue, int(200 - hue * 0.5), int(255 - hue * 0.3), alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0)
        for i in range(controller.length):
            controller.set_pixel(i, color)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        flash = self.flash.step(reactive.accent, 0.035)
        if reactive.drop_hit(beat=audio.beat, low_threshold=0.3, accent_threshold=0.7):
            self.flash.value = 1.0
            self.charge = 0.0
        self.charge = min(self.charge + 0.006 + reactive.low * 0.012, 1.0)
        if audio.beat and self.charge < 0.3:
            self.charge = 0.0
        tension = self.charge
        preview = reactive.rms * 0.3 + reactive.mid * 0.3 if tension > 0.6 else 0.0
        alpha = max(preview * tension * 0.4, flash)
        hue = int(20 + reactive.low * 30) % 256
        brightness = min(1.0, flash * 2.0 + tension * 0.3)
        color = Rgba(hue, int(brightness * 200), int(brightness * (255 - hue * 0.3)), min(alpha * 2.0, 1.0)) if alpha > 0.0 else Rgba(0, 0, 0, 0.0)
        for i in range(controller.length):
            controller.set_pixel(i, color)
