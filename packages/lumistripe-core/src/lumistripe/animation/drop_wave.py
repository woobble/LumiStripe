from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import burst_profile, club_color, warm_flash
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class DropWave(Animation):
    charge: float = 0.0
    radius: float = 0.0
    speed: float = 0.0
    hue_seed: int = 0
    wave: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "drop_wave"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(AudioFrame(rms=0.16, bands=(0.24, 0.22, 0.18, 0.16, 0.14, 0.12, 0.1, 0.08)))
        self._step(frame, controller, reactive, beat=frame % 40 == 0)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(frame, controller, reactive, beat=audio.beat)

    def _step(self, frame: int, controller: Controller, reactive: AudioReactive, *, beat: bool) -> None:
        length = max(controller.length, 1)
        self.charge = min(1.0, self.charge + 0.006 + reactive.low * 0.018 + reactive.mid * 0.01)

        drop_hit = beat and (reactive.low > 0.34 or reactive.accent > 0.72 or reactive.rms > 0.8)
        if drop_hit:
            self.wave.value = 1.0
            self.radius = 0.0
            self.speed = 0.75 + reactive.accent * 2.4
            self.hue_seed = frame * 19 + int(reactive.low * 120.0)
            self.charge = 0.0
        elif self.charge > 0.78 and frame % 7 == 0:
            self.wave.value = max(self.wave.value, 0.45)
            self.speed = max(self.speed, 0.55 + reactive.drive() * 1.2)

        self.radius += self.speed
        flash = self.wave.step(0.0, 0.07)
        center = (length - 1) / 2.0
        controller.clear()

        for index in range(length):
            distance = abs(index - center)
            if flash > 0.0:
                intensity = burst_profile(distance, self.radius, 1.5 + self.charge * 4.0)
                if intensity > 0.0:
                    alpha = min(1.0, intensity * (0.22 + flash * 0.78))
                    controller.set_pixel(index, warm_flash(self.hue_seed + index, alpha=alpha))
            elif self.charge > 0.35:
                pre = max(0.0, 1.0 - distance / max(center, 1.0))
                alpha = min(1.0, pre * (0.08 + self.charge * 0.2))
                if alpha > 0.0:
                    controller.set_pixel(index, club_color(self.hue_seed + index, alpha=alpha, lightness=48))
