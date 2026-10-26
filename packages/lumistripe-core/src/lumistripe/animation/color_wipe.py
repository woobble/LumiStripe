from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Color, Hex, Hsla
from ..controller import Controller
from ..render import native_color_wipe_available, render_color_wipe_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class ColorWipe(Animation):
    native_render_enabled = True

    head: float = 0.0
    speed: float = 0.85
    _motion_direction: float = 1.0
    palette_index: int = 0
    palette: tuple[Color, Color, Color, Color] = field(
        default_factory=lambda: (Hex(0xFF004D), Hex(0x00E5FF), Hex(0xFFE600), Hex(0x00FF6A))
    )

    @property
    def name(self) -> str:
        return "color_wipe"

    def tick(self, frame: int, controller: Controller) -> None:
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        self._step(controller, self.speed * speed_scale)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        self.speed = max(0.6, reactive.speed(0.7, 3.2)) * speed_scale
        if audio.beat or reactive.accent > 0.65:
            self.palette_index = (self.palette_index + 1) % len(self.palette)
        self._step(controller, self.speed, audio=audio, reactive=reactive)

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if (
            not native_color_wipe_available()
            or self.setting("palette", "rainbow") != "rainbow"
        ):
            return False
        length = max(len(destination), 1)
        reactive = None
        if audio is not None:
            reactive = AudioReactive.from_frame(audio)
            speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
            self.speed = max(0.6, reactive.speed(0.7, 3.2)) * speed_scale
            if audio.beat or reactive.accent > 0.65:
                self.palette_index = (self.palette_index + 1) % len(self.palette)
            speed = self.speed
        else:
            speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
            speed = self.speed * speed_scale

        configured_direction = self.setting("direction", "bounce")
        if configured_direction == "bounce":
            sign = self._motion_direction
            self.head += sign * speed
            if self.head >= length - 1:
                self.head = float(length - 1)
                self._motion_direction = -1.0
                self.palette_index = (self.palette_index + 1) % len(self.palette)
            elif self.head <= 0.0:
                self.head = 0.0
                self._motion_direction = 1.0
                self.palette_index = (self.palette_index + 1) % len(self.palette)
        else:
            sign = -1.0 if configured_direction == "reverse" else 1.0
            self.head = (self.head + sign * speed) % length
        tail = max(1.0, length * self.setting("tail_length", 8) / 100.0)
        color = self.palette[self.palette_index]
        hue, saturation, _, _ = color.to_rgba()
        boost = reactive.beat_pulse(0.0, 0.4) if reactive is not None else 0.0
        local = (
            reactive.band_window(audio, int(self.head), length, 1)
            if reactive is not None and audio is not None
            else 0.0
        )
        if len(destination) == 0:
            return True
        return render_color_wipe_into(
            destination,
            self.head,
            tail,
            hue,
            saturation,
            1 if sign > 0 else -1,
            local,
            boost,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
        )

    def _step(
        self,
        controller: Controller,
        speed: float,
        *,
        audio: AudioFrame | None = None,
        reactive: AudioReactive | None = None,
    ) -> None:
        length = max(controller.length, 1)
        direction = self.setting("direction", "bounce")
        if direction == "bounce":
            sign = self._motion_direction
            self.head += sign * speed
            if self.head >= length - 1:
                self.head = float(length - 1)
                self._motion_direction = -1.0
                self.palette_index = (self.palette_index + 1) % len(self.palette)
            elif self.head <= 0.0:
                self.head = 0.0
                self._motion_direction = 1.0
                self.palette_index = (self.palette_index + 1) % len(self.palette)
        else:
            sign = -1.0 if direction == "reverse" else 1.0
            self.head = (self.head + sign * speed) % length
        tail = max(1.0, length * self.setting("tail_length", 8) / 100.0)
        color = self.palette[self.palette_index]
        hue, sat, _, _ = color.to_rgba()
        boost = reactive.beat_pulse(0.0, 0.4) if reactive is not None else 0.0
        local = (
            reactive.band_window(audio, int(self.head), length, 1)
            if reactive is not None and audio is not None
            else 0.0
        )
        for i in range(controller.length):
            distance = (self.head - i) % length if sign > 0 else (i - self.head) % length
            fade = max(0.0, 1.0 - distance / tail)
            lightness = min(44 + int(fade * 18.0 + local * 12.0 + boost * 10.0), 66)
            controller.set_pixel(i, Hsla(hue, sat, lightness, 1.0))
