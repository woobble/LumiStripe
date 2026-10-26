from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla, Rgba
from ..controller import Controller
from ..render import native_bouncing_ball_available, render_bouncing_ball_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class BouncingBall(Animation):
    native_render_enabled = True

    pos: float = 0.0
    speed: float = 0.85
    direction: float = 1.0

    @property
    def name(self) -> str:
        return "bouncing_ball"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0))
        speed = 0.85 * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        travel = self.setting("direction", "bounce")
        if travel == "forward":
            self.pos = (self.pos + speed) % max(controller.length, 1)
        elif travel == "reverse":
            self.pos = (self.pos - speed) % max(controller.length, 1)
        else:
            self.pos += speed * self.direction
            if self.pos >= max_pos:
                self.pos = max_pos
                self.direction = -1.0
            if self.pos < 0.0:
                self.pos = 0.0
                self.direction = 1.0

        hue = (frame * 5) % 256
        radius = max(1.0, controller.length * self.setting("particle_size", 35) / 100.0 * 0.1)
        for i in range(controller.length):
            dist = abs(i - self.pos)
            alpha = 1.0 - dist / radius if dist < radius else 0.0
            if alpha > 0.0:
                controller.set_pixel(i, Hsla(hue, 100, 56, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        travel = self.setting("direction", "bounce")
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        if audio.beat:
            if travel == "bounce" and (reactive.low > 0.3 or reactive.accent > 0.35):
                self.direction *= -1.0
            self.speed = min(3.0, self.speed + reactive.accent * 1.2 + reactive.low * 0.4)
        self.speed = max(0.45, min(3.2, self.speed * 0.985 + reactive.drive() * 0.08))

        max_pos = float(max(controller.length - 1, 0))
        movement = self.speed * speed_scale
        if travel == "forward":
            self.pos = (self.pos + movement) % max(controller.length, 1)
        elif travel == "reverse":
            self.pos = (self.pos - movement) % max(controller.length, 1)
        else:
            self.pos += movement * self.direction
            if self.pos >= max_pos:
                self.pos = max_pos
                self.direction = -1.0
            if self.pos < 0.0:
                self.pos = 0.0
                self.direction = 1.0

        hue = reactive.hue_shift(frame, 0.35)
        ball_rad = max(1.0, controller.length * self.setting("particle_size", 35) / 100.0 * 0.1) + reactive.low * 3.0
        glow = 0.1 + reactive.rms * 0.5
        glow_width = max(1.0, controller.length * 0.04)
        for i in range(controller.length):
            dist = abs(i - self.pos)
            if dist < ball_rad:
                alpha = min(1.0 - dist / ball_rad + glow * 0.3, 1.0)
                controller.set_pixel(i, Hsla(hue, 100, 56, alpha))
            elif dist < ball_rad + glow_width:
                alpha = glow * (1.0 - (dist - ball_rad) / glow_width)
                if alpha > 0.0:
                    controller.set_pixel(i, Hsla(hue, 90, 42, alpha))
                else:
                    controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_bouncing_ball_available():
            return False
        length = destination.shape[0]
        max_pos = float(max(length - 1, 0))
        travel = self.setting("direction", "bounce")
        audio_mode = audio is not None
        if audio is None:
            speed = 0.85 * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
            if travel == "forward":
                self.pos = (self.pos + speed) % max(length, 1)
            elif travel == "reverse":
                self.pos = (self.pos - speed) % max(length, 1)
            else:
                self.pos += speed * self.direction
                if self.pos >= max_pos:
                    self.pos = max_pos
                    self.direction = -1.0
                if self.pos < 0.0:
                    self.pos = 0.0
                    self.direction = 1.0
            hue = (frame * 5) % 256
            radius = max(1.0, length * self.setting("particle_size", 35) / 100.0 * 0.1)
            glow_width = 1.0
            glow = 0.0
        else:
            reactive = AudioReactive.from_frame(audio)
            speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
            if audio.beat:
                if travel == "bounce" and (reactive.low > 0.3 or reactive.accent > 0.35):
                    self.direction *= -1.0
                self.speed = min(3.0, self.speed + reactive.accent * 1.2 + reactive.low * 0.4)
            self.speed = max(0.45, min(3.2, self.speed * 0.985 + reactive.drive() * 0.08))
            movement = self.speed * speed_scale
            if travel == "forward":
                self.pos = (self.pos + movement) % max(length, 1)
            elif travel == "reverse":
                self.pos = (self.pos - movement) % max(length, 1)
            else:
                self.pos += movement * self.direction
                if self.pos >= max_pos:
                    self.pos = max_pos
                    self.direction = -1.0
                if self.pos < 0.0:
                    self.pos = 0.0
                    self.direction = 1.0
            hue = reactive.hue_shift(frame, 0.35)
            radius = max(1.0, length * self.setting("particle_size", 35) / 100.0 * 0.1) + reactive.low * 3.0
            glow_width = max(1.0, length * 0.04)
            glow = 0.1 + reactive.rms * 0.5
        return render_bouncing_ball_into(
            destination,
            self.pos,
            radius,
            glow_width,
            glow,
            hue,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            audio_mode,
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
