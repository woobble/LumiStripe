from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..animation.club_utils import BurstState, burst_profile, club_color
from ..animation.reactive import AudioReactive
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..controller import Controller
from ..render import native_color_burst_available, render_color_burst_into
from .base import Effect


@dataclass(slots=True)
class ColorBurst(Effect):
    native_render_enabled = True
    bursts: list[BurstState] = field(default_factory=list)
    _native_bursts: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._native_bursts = np.zeros((6, 5), dtype=np.float64)

    @property
    def name(self) -> str:
        return "color_burst"

    def tick(self, frame: int, controller: Controller) -> None:
        if frame % 28 == 0:
            center = float(
                (frame * 7 + controller.length * 3) % max(controller.length, 1)
            )
            self._spawn(frame, controller, center=center, strength=0.42)
        self._render(controller)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat or reactive.accent > 0.8 or reactive.rms > 0.88:
            center = float(
                (frame * 11 + int(reactive.mid * 100.0)) % max(controller.length, 1)
            )
            strength = min(1.0, 0.34 + reactive.accent * 0.66)
            self._spawn(frame, controller, center=center, strength=strength)
        elif reactive.drive() > 0.7 and frame % 9 == 0:
            center = float(
                (frame * 5 + int(reactive.high * 80.0)) % max(controller.length, 1)
            )
            self._spawn(
                frame,
                controller,
                center=center,
                strength=min(0.78, 0.22 + reactive.drive() * 0.45),
            )
        self._render(controller)

    def _spawn(
        self, frame: int, controller: Controller, *, center: float, strength: float
    ) -> None:
        self.bursts.append(
            self._make_burst(frame, max(controller.length, 1), center, strength)
        )
        self.bursts = self.bursts[-6:]

    def _make_burst(
        self, frame: int, length: int, center: float, strength: float
    ) -> BurstState:
        spread = self.setting("spread", 60) / 100.0
        strip_center = (length - 1) * 0.5
        center = strip_center + (center - strip_center) * spread
        hue_seed = frame * 17 + int(center * 13.0) + length * 5
        radius = 0.0
        speed = (0.55 + strength * 1.95) * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        width = (1.2 + strength * 4.5) * (0.5 + spread)
        return BurstState(
            center=center,
            radius=radius,
            speed=speed,
            hue_seed=hue_seed,
            strength=strength,
            width=width,
        )

    def _render(self, controller: Controller) -> None:
        controller.clear()
        active: list[BurstState] = []
        decay = self.setting("decay", 50) / 100.0
        for burst in self.bursts:
            burst.radius += burst.speed
            burst.width = min(burst.width + 0.1, 8.0)
            burst.strength = max(0.0, burst.strength - (0.01 + decay * 0.06))
            if burst.strength > 0.0:
                active.append(burst)
        self.bursts = active

        for burst in self.bursts:
            alpha = min(1.0, 0.18 + burst.strength * 0.82)
            for index in range(controller.length):
                distance = abs(index - burst.center)
                intensity = burst_profile(distance, burst.radius, burst.width)
                if intensity > 0.0:
                    controller.set_pixel(
                        index,
                        club_color(
                            burst.hue_seed + index, alpha=min(1.0, intensity * alpha)
                        ),
                    )

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_color_burst_available():
            return False
        length = int(destination.shape[0])
        if audio is None:
            if frame % 28 == 0:
                center = float((frame * 7 + length * 3) % max(length, 1))
                self.bursts.append(
                    self._make_burst(frame, max(length, 1), center, 0.42)
                )
                self.bursts = self.bursts[-6:]
        else:
            reactive = AudioReactive.from_frame(audio)
            if audio.beat or reactive.accent > 0.8 or reactive.rms > 0.88:
                center = float(
                    (frame * 11 + int(reactive.mid * 100.0)) % max(length, 1)
                )
                strength = min(1.0, 0.34 + reactive.accent * 0.66)
                self.bursts.append(
                    self._make_burst(frame, max(length, 1), center, strength)
                )
                self.bursts = self.bursts[-6:]
            elif reactive.drive() > 0.7 and frame % 9 == 0:
                center = float(
                    (frame * 5 + int(reactive.high * 80.0)) % max(length, 1)
                )
                strength = min(0.78, 0.22 + reactive.drive() * 0.45)
                self.bursts.append(
                    self._make_burst(frame, max(length, 1), center, strength)
                )
                self.bursts = self.bursts[-6:]

        decay = self.setting("decay", 50) / 100.0
        active: list[BurstState] = []
        for burst in self.bursts:
            burst.radius += burst.speed
            burst.width = min(burst.width + 0.1, 8.0)
            burst.strength = max(0.0, burst.strength - (0.01 + decay * 0.06))
            if burst.strength > 0.0:
                active.append(burst)
        self.bursts = active
        for index, burst in enumerate(self.bursts):
            self._native_bursts[index, 0] = burst.center
            self._native_bursts[index, 1] = burst.radius
            self._native_bursts[index, 2] = burst.hue_seed
            self._native_bursts[index, 3] = burst.strength
            self._native_bursts[index, 4] = burst.width
        return render_color_burst_into(
            destination,
            self._native_bursts,
            len(self.bursts),
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
