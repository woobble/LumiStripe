from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Animation
from .club_utils import trail_profile
from .palettes import palette_color
from .reactive import AudioReactive


@dataclass(frozen=True, slots=True)
class CometSettings:
    speed: int = 65
    tail_length: int = 8
    intensity: int = 100
    direction: Literal["forward", "reverse", "bounce"] = "bounce"
    palette: Literal["rainbow", "neon", "fire", "custom"] = "rainbow"
    custom_color: str = "#7C3AED"
    music_reactivity: int = 70


@dataclass(slots=True)
class Comet(Animation):
    position: float = 0.0
    _motion_direction: float = 1.0
    settings: CometSettings = field(default_factory=CometSettings)

    @property
    def name(self) -> str:
        return "comet"

    def set_settings(self, values: Mapping[str, object]) -> None:
        self.settings = CometSettings(
            speed=cast(int, values["speed"]),
            tail_length=cast(int, values["tail_length"]),
            intensity=cast(int, values["intensity"]),
            direction=cast(
                Literal["forward", "reverse", "bounce"], values["direction"]
            ),
            palette=cast(
                Literal["rainbow", "neon", "fire", "custom"], values["palette"]
            ),
            custom_color=cast(str, values["custom_color"]),
            music_reactivity=cast(int, values["music_reactivity"]),
        )

    def reset(self) -> None:
        self.position = 0.0
        self._motion_direction = 1.0

    def tick(self, frame: int, controller: Controller) -> None:
        self._render(frame, controller, None)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        if self.settings.music_reactivity == 0:
            self.tick(frame, controller)
            return
        self._render(
            frame, controller, AudioReactive.from_frame(audio), beat=audio.beat
        )

    def _render(
        self,
        frame: int,
        controller: Controller,
        reactive: AudioReactive | None,
        *,
        beat: bool = False,
    ) -> None:
        length = controller.length
        max_position = float(max(length - 1, 0))
        reactivity = self.settings.music_reactivity / 100.0 if reactive else 0.0
        speed = 0.25 + self.settings.speed / 100.0 * 1.5
        if reactive is not None:
            audio_speed = 0.72 + reactive.drive() * 0.65 + reactive.accent * 0.45
            speed *= 1.0 + (audio_speed - 1.0) * reactivity
            if (
                self.settings.direction == "bounce"
                and beat
                and reactive.accent > 0.45
                and reactive.low > 0.3
            ):
                self._motion_direction *= -1.0
        self._advance(speed, max_position)

        tail = max(1.0, max(length - 1, 0) * self.settings.tail_length / 100.0)
        shimmer = 0.0
        hue = float((frame * 3) % 360)
        if reactive is not None:
            tail *= 1.0 + reactivity * (reactive.high * 0.7 - 0.15)
            shimmer = reactivity * (reactive.accent * 0.25 + reactive.rms * 0.1)
            hue += reactivity * reactive.hue_shift(frame, 0.2) * 360.0 / 256.0
        brightness = self.settings.intensity / 100.0

        for index in range(length):
            strength = trail_profile(abs(self.position - index), tail)
            if strength <= 0.0 or brightness <= 0.0:
                controller.set_pixel(index, Hsla(0, 0, 0, 0.0))
                continue
            alpha = min(1.0, (0.14 + strength * 0.86 + shimmer) * brightness)
            lightness = min(64, 48 + int(strength * 12.0 + shimmer * 10.0))
            controller.set_pixel(
                index,
                palette_color(
                    self.settings.palette,
                    hue,
                    alpha=alpha,
                    custom_color=self.settings.custom_color,
                    lightness=lightness,
                ),
            )

    def _advance(self, speed: float, max_position: float) -> None:
        if max_position <= 0.0:
            self.position = 0.0
            return
        direction = self.settings.direction
        if direction == "forward":
            self.position = (self.position + speed) % (max_position + 1.0)
        elif direction == "reverse":
            self.position = (self.position - speed) % (max_position + 1.0)
        else:
            self.position += speed * self._motion_direction
            if self.position >= max_position:
                self.position = max_position
                self._motion_direction = -1.0
            elif self.position <= 0.0:
                self.position = 0.0
                self._motion_direction = 1.0


__all__ = ["Comet", "CometSettings"]
