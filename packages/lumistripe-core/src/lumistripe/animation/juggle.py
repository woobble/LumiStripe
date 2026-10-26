from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Rgba
from ..controller import Controller
from ..render import native_juggle_available, render_juggle_into
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class Juggle(Animation):
    native_render_enabled = True

    positions: list[float] = field(default_factory=lambda: [0.0, 25.0, 50.0, 75.0])
    speeds: list[float] = field(default_factory=lambda: [2.5, 2.0, 1.8, 2.2])
    _scaled_length: int = field(default=0, repr=False)

    @property
    def name(self) -> str:
        return "juggle"

    def tick(self, frame: int, controller: Controller) -> None:
        max_pos = float(max(controller.length - 1, 0))
        count = self.setting("count", 4)
        self._prepare_positions(controller.length, count)
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        radius = max(1.0, controller.length * self.setting("particle_size", 35) / 100.0 * 0.17)
        for j in range(count):
            self.positions[j] += self.speeds[j] * speed_scale
            if self.positions[j] >= max_pos:
                self.positions[j] = max_pos
                self.speeds[j] = -self.speeds[j]
            if self.positions[j] < 0.0:
                self.positions[j] = 0.0
                self.speeds[j] = -self.speeds[j]
        for i in range(controller.length):
            composite = 0.0
            for j in range(count):
                pos = self.positions[j]
                dist = abs(i - pos)
                if dist < radius:
                    composite += (1.0 - dist / radius) * 0.6
            alpha = min(composite, 1.0)
            controller.set_pixel(i, Rgba((i * 32 + 50) % 256, 255, 200, alpha) if alpha > 0.0 else Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        max_pos = float(max(controller.length - 1, 0))
        reactive = AudioReactive.from_frame(audio)
        scale = 0.45 + reactive.drive() * 1.4
        count = self.setting("count", 4)
        self._prepare_positions(controller.length, count)
        radius = max(1.0, controller.length * self.setting("particle_size", 35) / 100.0 * 0.17)
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        for j in range(count):
            if audio.beat:
                direction = 1.0 if self.speeds[j] >= 0.0 else -1.0
                self.speeds[j] = direction * (max(abs(self.speeds[j]), 2.0) * (0.75 + reactive.accent * 0.6))
            self.positions[j] += self.speeds[j] * scale * speed_scale
            if self.positions[j] >= max_pos:
                self.positions[j] = max_pos
                self.speeds[j] = -self.speeds[j]
            if self.positions[j] < 0.0:
                self.positions[j] = 0.0
                self.speeds[j] = -self.speeds[j]
        for i in range(controller.length):
            composite = 0.0
            for j in range(count):
                pos = self.positions[j]
                dist = abs(i - pos)
                if dist < radius:
                    composite += (1.0 - dist / radius) * 0.6
            alpha = min(composite, 1.0)
            if alpha > 0.0:
                hue = (reactive.hue_shift(frame + i, 0.18) + (i * 11 % 128)) % 256
                controller.set_pixel(i, Rgba(hue, 255, 200, alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def _prepare_positions(self, length: int, count: int) -> None:
        if self._scaled_length != length:
            old_span = 99.0 if self._scaled_length == 0 else max(self._scaled_length - 1, 1)
            new_span = float(max(length - 1, 0))
            self.positions = [position * new_span / old_span for position in self.positions]
            self._scaled_length = length
        while len(self.positions) < count:
            index = len(self.positions)
            self.positions.append((index * 37.0 % 100.0) * max(length - 1, 0) / 100.0)
            self.speeds.append(1.6 + index % 3 * 0.35)

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_juggle_available():
            return False
        length = destination.shape[0]
        max_pos = float(max(length - 1, 0))
        count = self.setting("count", 4)
        self._prepare_positions(length, count)
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        audio_mode = audio is not None
        hue_rate = 0.0
        if audio is None:
            for ball in range(count):
                self.positions[ball] += self.speeds[ball] * speed_scale
                if self.positions[ball] >= max_pos:
                    self.positions[ball] = max_pos
                    self.speeds[ball] = -self.speeds[ball]
                if self.positions[ball] < 0.0:
                    self.positions[ball] = 0.0
                    self.speeds[ball] = -self.speeds[ball]
        else:
            reactive = AudioReactive.from_frame(audio)
            scale = 0.45 + reactive.drive() * 1.4
            if audio.beat:
                for ball in range(count):
                    direction = 1.0 if self.speeds[ball] >= 0.0 else -1.0
                    self.speeds[ball] = direction * (
                        max(abs(self.speeds[ball]), 2.0) * (0.75 + reactive.accent * 0.6)
                    )
            hue_rate = 0.18 + reactive.high * 3.0 + reactive.accent * 6.0
            for ball in range(count):
                self.positions[ball] += self.speeds[ball] * scale * speed_scale
                if self.positions[ball] >= max_pos:
                    self.positions[ball] = max_pos
                    self.speeds[ball] = -self.speeds[ball]
                if self.positions[ball] < 0.0:
                    self.positions[ball] = 0.0
                    self.speeds[ball] = -self.speeds[ball]
        radius = max(1.0, length * self.setting("particle_size", 35) / 100.0 * 0.17)
        return render_juggle_into(
            destination,
            tuple(self.positions[:count]),
            radius,
            frame,
            hue_rate,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            audio_mode,
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
