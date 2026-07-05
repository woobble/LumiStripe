from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import burst_profile, club_color
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class CenterBurst(Animation):
    radius: float = 0.0
    speed: float = 0.0
    width: float = 1.4
    hue_seed: int = 0
    flash: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "center_burst"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(AudioFrame(rms=0.18, bands=(0.18, 0.2, 0.22, 0.2, 0.16, 0.14, 0.12, 0.1)))
        self._step(frame, controller, reactive, beat=frame % 32 == 0)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(frame, controller, reactive, beat=audio.beat)

    def _step(self, frame: int, controller: Controller, reactive: AudioReactive, *, beat: bool) -> None:
        length = max(controller.length, 1)
        if beat and (reactive.low > 0.22 or reactive.accent > 0.46 or reactive.rms > 0.5):
            self.radius = 0.0
            self.speed = 0.9 + reactive.accent * 2.2
            self.width = 1.1 + reactive.accent * 3.6
            self.hue_seed = frame * 23 + int(reactive.low * 80.0)
            self.flash.value = min(1.0, 0.35 + reactive.accent * 0.65)
        elif self.flash.value > 0.0:
            self.speed = max(self.speed, 0.75 + reactive.drive() * 1.1)
        else:
            self.radius += 0.3 + reactive.drive() * 0.4

        self.radius += self.speed
        flash = self.flash.step(0.0, 0.08)
        center = (length - 1) / 2.0
        controller.clear()
        for index in range(length):
            distance = abs(index - center)
            intensity = burst_profile(distance, self.radius, max(self.width, 1.0))
            if intensity > 0.0:
                alpha = min(1.0, intensity * (0.28 + flash * 0.72))
                controller.set_pixel(index, club_color(self.hue_seed + index, alpha=alpha, lightness=60))
