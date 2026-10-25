from __future__ import annotations

from colorsys import rgb_to_hsv
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from ..animation.palettes import palette_color
from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla, Rgba
from ..controller import Controller
from ..render import native_tunnel_rings_available, render_tunnel_rings_into
from .base import Effect

_Ring = tuple[float, float, float, int, float, float]


def _render_ring(
    controller: Controller,
    center: float,
    radius: float,
    width: float,
    hue: int,
    alpha: float,
    max_dist: float,
) -> None:
    for i in range(controller.length):
        dist = abs(i - center)
        ring = max(1.0 - abs(dist - radius) / width, 0.0)
        ambient = max(1.0 - dist / max_dist, 0.0) * 0.1
        combined = min(ambient + ring * alpha, 1.0)
        if combined > 0.0:
            existing = controller.pixel(i)
            ea = existing.to_rgba()[3]
            if ea > 0.0:
                combined = max(ea, combined)
            controller.set_pixel(i, Hsla(hue, 80, 55, combined))


@dataclass(slots=True)
class BeatTunnel(Effect):
    radius: float = 0.0
    direction: float = 1.0
    pulse: Decay = field(default_factory=Decay)
    _native_geometry: npt.NDArray[np.float64] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _native_colors: PixelBuffer | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _native_clear_color: npt.NDArray[np.uint8] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def name(self) -> str:
        return "beat_tunnel"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgba(0, 0, 0, 0.0))
        self._render_python(self._static_rings(frame, controller.length), controller)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        controller.fill(Rgba(0, 0, 0, 0.0))
        self._render_python(
            self._audio_rings(frame, controller.length, audio), controller
        )

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_tunnel_rings_available():
            return False

        rings = (
            self._static_rings(frame, destination.shape[0])
            if audio is None
            else self._audio_rings(frame, destination.shape[0], audio)
        )
        self._ensure_native_buffers()
        assert self._native_geometry is not None
        assert self._native_colors is not None
        assert self._native_clear_color is not None

        configuration = getattr(self, "_animation_configuration", {})
        palette = str(configuration.get("palette", "rainbow"))
        custom_color = str(configuration.get("custom_color", "#7C3AED"))
        for index, (center, radius, width, hue, alpha, max_dist) in enumerate(rings):
            self._native_geometry[index] = (
                center,
                radius,
                width,
                max_dist,
                alpha,
            )
            self._native_colors[index] = self._mapped_rgb(hue, palette, custom_color)

        self._native_clear_color[:] = self._clear_rgb(palette, custom_color)
        output_brightness = max(
            0.0, min(1.0, float(self._output_brightness(brightness)))
        )
        return render_tunnel_rings_into(
            destination,
            self._native_geometry,
            self._native_colors,
            self._native_clear_color,
            len(rings),
            output_brightness,
        )

    def _static_rings(self, frame: int, length: int) -> tuple[_Ring, _Ring]:
        center = max(length - 1, 0) * 0.5
        max_dist = max(center, 1.0)
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, length * self.setting("width", 30) / 100.0 * 0.14)
        self.radius += 0.5 * speed * self.direction
        if self.radius > max_dist or self.radius < 0.0:
            self.direction *= -1.0
            self.radius = min(max(self.radius, 0.0), max_dist)
        hue_a = (frame * 4) % 256
        hue_b = (frame * 4 + 120) % 256
        inner = (self.radius - max_dist * 0.5) % (max_dist + 4.0)
        return (
            (center, self.radius, width, hue_a, 0.85, max_dist),
            (center, inner, max(1.0, width * 0.75), hue_b, 0.6, max_dist),
        )

    def _audio_rings(
        self, frame: int, length: int, audio: AudioFrame
    ) -> tuple[_Ring, ...]:
        reactive = AudioReactive.from_frame(audio)
        center = max(length - 1, 0) * 0.5
        max_dist = max(center, 1.0)
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, length * self.setting("width", 30) / 100.0 * 0.14)
        hit = self.pulse.step(reactive.accent, 0.05)
        if audio.beat:
            self.radius = 0.0
            self.direction = 1.0
        else:
            self.radius += reactive.speed(0.3, 1.5) * speed * self.direction
            if self.radius > max_dist + 2.0:
                self.direction = -1.0
            elif self.radius < -2.0:
                self.direction = 1.0
        hue = (170 + int(reactive.low * 30) - int(reactive.high * 20)) % 256
        rings: tuple[_Ring, ...] = (
            (
                center,
                abs(self.radius),
                width + reactive.high * 2.0,
                hue,
                0.7 + hit * 0.3,
                max_dist,
            ),
        )
        inner = (abs(self.radius) - max_dist * 0.4) % (max_dist + 2.0)
        if inner > 1.0:
            rings += (
                (
                    center,
                    inner,
                    max(1.0, width * 0.65),
                    (hue + 100) % 256,
                    0.4 + hit * 0.3,
                    max_dist,
                ),
            )
        return rings

    def _render_python(self, rings: tuple[_Ring, ...], controller: Controller) -> None:
        for center, radius, width, hue, alpha, max_dist in rings:
            _render_ring(controller, center, radius, width, hue, alpha, max_dist)

    def _ensure_native_buffers(self) -> None:
        if self._native_geometry is None:
            self._native_geometry = np.empty((2, 5), dtype=np.float64)
            self._native_colors = np.empty((2, 3), dtype=np.uint8)
            self._native_clear_color = np.empty((3,), dtype=np.uint8)

    @staticmethod
    def _mapped_rgb(hue: int, palette: str, custom_color: str) -> tuple[int, int, int]:
        red, green, blue, _ = Hsla(hue, 80, 55, 1.0).to_rgba()
        if palette == "rainbow":
            return red, green, blue
        if palette == "custom":
            color = palette_color(palette, hue, alpha=1.0, custom_color=custom_color)
            mapped_red, mapped_green, mapped_blue, _ = color.to_rgba()
            return mapped_red, mapped_green, mapped_blue

        mapped_hue, saturation_value, value = rgb_to_hsv(
            red / 255.0, green / 255.0, blue / 255.0
        )
        color = palette_color(
            palette,
            mapped_hue * 360.0,
            alpha=1.0,
            custom_color=custom_color,
            lightness=int(36.0 + value * 24.0),
            saturation=int(saturation_value * 100.0),
        )
        mapped_red, mapped_green, mapped_blue, _ = color.to_rgba()
        return mapped_red, mapped_green, mapped_blue

    @staticmethod
    def _clear_rgb(palette: str, custom_color: str) -> tuple[int, int, int]:
        if palette == "rainbow":
            return 0, 0, 0
        if palette == "custom":
            color = palette_color(palette, 0.0, alpha=0.0, custom_color=custom_color)
            red, green, blue, _ = color.to_rgba()
            return red, green, blue

        color = palette_color(
            palette,
            0.0,
            alpha=0.0,
            custom_color=custom_color,
            lightness=36,
            saturation=0,
        )
        red, green, blue, _ = color.to_rgba()
        return red, green, blue
