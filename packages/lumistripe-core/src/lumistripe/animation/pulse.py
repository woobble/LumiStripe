from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import cos, pi, sin
from typing import Literal, cast

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .palettes import palette_color
from .reactive import AudioReactive


@dataclass(frozen=True, slots=True)
class PulseSettings:
    speed: int = 50
    minimum_intensity: int = 22
    maximum_intensity: int = 100
    palette: Literal["rainbow", "neon", "fire", "custom"] = "rainbow"
    custom_color: str = "#7C3AED"
    pulse_curve: Literal["sine", "smoothstep", "sharp"] = "sine"
    music_reactivity: int = 70


@dataclass(slots=True)
class Pulse(Animation):
    _pulse: float = 0.0
    _hue: int = 0
    settings: PulseSettings = PulseSettings()

    @property
    def name(self) -> str:
        return "pulse"

    def set_settings(self, values: Mapping[str, object]) -> None:
        settings = PulseSettings(
            speed=cast(int, values["speed"]),
            minimum_intensity=cast(int, values["minimum_intensity"]),
            maximum_intensity=cast(int, values["maximum_intensity"]),
            palette=cast(
                Literal["rainbow", "neon", "fire", "custom"], values["palette"]
            ),
            custom_color=cast(str, values["custom_color"]),
            pulse_curve=cast(
                Literal["sine", "smoothstep", "sharp"], values["pulse_curve"]
            ),
            music_reactivity=cast(int, values["music_reactivity"]),
        )
        if settings.minimum_intensity > settings.maximum_intensity:
            raise ValueError("minimum intensity must not exceed maximum intensity")
        self.settings = settings

    def reset(self) -> None:
        self._pulse = 0.0
        self._hue = 0

    def tick(self, frame: int, controller: Controller) -> None:
        self._render(frame, controller, None)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        if self.settings.music_reactivity == 0:
            self.tick(frame, controller)
            return
        self._render(
            frame,
            controller,
            AudioReactive.from_frame(audio),
            beat=audio.beat,
        )

    def _render(
        self,
        frame: int,
        controller: Controller,
        reactive: AudioReactive | None,
        *,
        beat: bool = False,
    ) -> None:
        period = max(30, round(240 - self.settings.speed * 2.4))
        phase = (frame % period) / period
        normalized = (sin(phase * pi * 2.0) * 0.5 + 0.5)
        if self.settings.pulse_curve == "smoothstep":
            normalized = normalized * normalized * (3.0 - 2.0 * normalized)
        elif self.settings.pulse_curve == "sharp":
            normalized = normalized**2.4
        else:
            normalized = normalized**1.2

        minimum = self.settings.minimum_intensity / 100.0
        maximum = self.settings.maximum_intensity / 100.0
        intensity = minimum + normalized * (maximum - minimum)
        reactivity = self.settings.music_reactivity / 100.0 if reactive else 0.0
        if reactive is not None:
            if beat:
                self._pulse = min(1.0, self._pulse + (0.55 + reactive.accent * 0.35) * reactivity)
                self._hue = (self._hue + int((23.0 + reactive.mid * 18.0) * reactivity)) % 256
            self._pulse = max(
                self._pulse * (1.0 - 0.08 * reactivity),
                reactive.beat_pulse(0.0, 0.18) * reactivity,
            )
            self._hue = (
                self._hue + int(reactive.low * 1.5 * reactivity)
            ) % 256
            audio_boost = self._pulse * (maximum - minimum)
            intensity = max(intensity, min(maximum, minimum + audio_boost))

        hue = (frame * 2 + int(normalized * 18.0) + int(self._hue * reactivity)) % 256
        alpha = max(0.0, min(maximum, intensity))
        for index in range(controller.length):
            spatial_wave = cos(phase * pi * 2.0 + index * 0.05) * 0.5 + 0.5
            lightness = 46 + int(spatial_wave * 5.0)
            controller.set_pixel(
                index,
                palette_color(
                    self.settings.palette,
                    hue,
                    alpha=alpha,
                    custom_color=self.settings.custom_color,
                    lightness=lightness,
                    saturation=82,
                ),
            )


__all__ = ["Pulse", "PulseSettings"]
