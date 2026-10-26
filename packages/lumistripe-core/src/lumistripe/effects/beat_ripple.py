from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla
from ..controller import Controller
from ..render import native_beat_ripple_available, render_beat_ripple_into
from .base import Effect


@dataclass(slots=True)
class BeatRipple(Effect):
    native_render_enabled = True

    radius: float = 0.0
    burst: Decay = field(default_factory=Decay)
    hue: int = 0

    @property
    def name(self) -> str:
        return "beat_ripple"

    def tick(self, frame: int, controller: Controller) -> None:
        center = max(controller.length - 1, 0) * 0.5
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14)
        decay = 0.4 + self.setting("decay", 50) / 100.0 * 0.6
        self.radius = (frame * 0.4 * speed) % (center + 6.0)
        self.hue = (frame * 3) % 256
        for i in range(controller.length):
            dist = abs(i - center)
            ring = max(1.0 - abs(dist - self.radius) / width, 0.0)
            mirror = max(1.0 - abs(dist - (center - self.radius + center)) / width, 0.0)
            alpha = max(ring, mirror) * (0.1 + decay * (1.0 - self.radius / (center + 6.0)))
            if alpha > 0.01:
                controller.set_pixel(i, Hsla(self.hue, 100, 50, alpha))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        max_pos = float(max(controller.length - 1, 0))
        reactive = AudioReactive.from_frame(audio)
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14)
        decay = self.setting("decay", 50) / 100.0
        hit = self.burst.step(reactive.accent, 0.02 + decay * 0.06)
        if audio.beat:
            self.radius = 0.0
            self.hue = (self.hue + 31) % 256
        else:
            self.radius += reactive.speed(0.4, 2.2) * speed

        for i in range(controller.length):
            left = abs(i - (-self.radius % (max_pos + 1)))
            right = abs(i - (max_pos - (-self.radius % (max_pos + 1))))
            ring = max(1.0 - left / (width + reactive.high * 2.0), 0.0)
            mirror = max(1.0 - right / (width + reactive.high * 2.0), 0.0)
            alpha = max(ring, mirror) * (0.15 + hit * 0.6 + reactive.low * 0.15) * (0.5 + decay)
            if alpha > 0.01:
                light = int(40 + reactive.mid * 20 + hit * 15)
                controller.set_pixel(i, Hsla(self.hue, 100, light, alpha))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_beat_ripple_available():
            return False
        length = int(destination.shape[0])
        max_pos = float(max(length - 1, 0))
        if audio is None:
            center = max_pos * 0.5
            speed = 0.25 + float(self.setting("speed", 50)) / 100.0 * 1.5
            width = max(
                1.0,
                length * float(self.setting("width", 30)) / 100.0 * 0.14,
            )
            decay = 0.4 + float(self.setting("decay", 50)) / 100.0 * 0.6
            radius = (frame * 0.4 * speed) % (center + 6.0)
            self.radius = radius
            self.hue = (frame * 3) % 256
            alpha_scale = 0.1 + decay * (1.0 - radius / (center + 6.0))
            lightness = 50
            radius_or_offset = radius
        else:
            reactive = AudioReactive.from_frame(audio)
            speed = 0.25 + float(self.setting("speed", 50)) / 100.0 * 1.5
            width = max(
                1.0,
                length * float(self.setting("width", 30)) / 100.0 * 0.14,
            )
            decay = float(self.setting("decay", 50)) / 100.0
            hit = self.burst.step(reactive.accent, 0.02 + decay * 0.06)
            if audio.beat:
                self.radius = 0.0
                self.hue = (self.hue + 31) % 256
            else:
                self.radius += reactive.speed(0.4, 2.2) * speed
            radius_or_offset = (-self.radius) % (max_pos + 1.0)
            center = max_pos
            width += reactive.high * 2.0
            alpha_scale = (0.15 + hit * 0.6 + reactive.low * 0.15) * (
                0.5 + decay
            )
            lightness = int(40 + reactive.mid * 20 + hit * 15)
        return render_beat_ripple_into(
            destination,
            audio is not None,
            center,
            radius_or_offset,
            width,
            alpha_scale,
            self.hue,
            lightness,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
