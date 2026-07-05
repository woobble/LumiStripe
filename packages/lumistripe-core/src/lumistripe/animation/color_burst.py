from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import BurstState, burst_profile, club_color
from .reactive import AudioReactive


@dataclass(slots=True)
class ColorBurst(Animation):
    bursts: list[BurstState] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "color_burst"

    def tick(self, frame: int, controller: Controller) -> None:
        if frame % 28 == 0:
            center = float((frame * 7 + controller.length * 3) % max(controller.length, 1))
            self._spawn(frame, controller, center=center, strength=0.42)
        self._render(controller)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat or reactive.accent > 0.8 or reactive.rms > 0.88:
            center = float((frame * 11 + int(reactive.mid * 100.0)) % max(controller.length, 1))
            strength = min(1.0, 0.34 + reactive.accent * 0.66)
            self._spawn(frame, controller, center=center, strength=strength)
        elif reactive.drive() > 0.7 and frame % 9 == 0:
            center = float((frame * 5 + int(reactive.high * 80.0)) % max(controller.length, 1))
            self._spawn(frame, controller, center=center, strength=min(0.78, 0.22 + reactive.drive() * 0.45))
        self._render(controller)

    def _spawn(self, frame: int, controller: Controller, *, center: float, strength: float) -> None:
        length = max(controller.length, 1)
        hue_seed = frame * 17 + int(center * 13.0) + length * 5
        radius = 0.0
        speed = 0.55 + strength * 1.95
        width = 1.2 + strength * 4.5
        self.bursts.append(BurstState(center=center, radius=radius, speed=speed, hue_seed=hue_seed, strength=strength, width=width))
        self.bursts = self.bursts[-6:]

    def _render(self, controller: Controller) -> None:
        controller.clear()
        active: list[BurstState] = []
        for burst in self.bursts:
            burst.radius += burst.speed
            burst.width = min(burst.width + 0.1, 8.0)
            burst.strength = max(0.0, burst.strength - 0.04)
            if burst.strength > 0.0:
                active.append(burst)
        self.bursts = active

        for burst in self.bursts:
            alpha = min(1.0, 0.18 + burst.strength * 0.82)
            for index in range(controller.length):
                distance = abs(index - burst.center)
                intensity = burst_profile(distance, burst.radius, burst.width)
                if intensity > 0.0:
                    controller.set_pixel(index, club_color(burst.hue_seed + index, alpha=min(1.0, intensity * alpha)))
