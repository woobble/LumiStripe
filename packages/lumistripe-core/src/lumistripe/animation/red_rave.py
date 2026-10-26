from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Rgb
from ..controller import Controller
from ..render import (
    native_red_chase_available,
    native_red_sweep_available,
    native_solid_render_available,
    render_red_chase_into,
    render_red_sweep_into,
    render_solid_rgba_into,
)
from .base import Animation
from .reactive import AudioReactive

RED = Rgb(255, 0, 0)


@dataclass(slots=True)
class RedRaveSweep(Animation):
    """A hard-edged red beam that travels back and forth along the strip."""

    native_render_enabled = True

    position: float = 0.0
    direction: float = 1.0

    @property
    def name(self) -> str:
        return "red_rave_sweep"

    def tick(self, frame: int, controller: Controller) -> None:
        self._step(frame, controller, drive=0.35, low=0.25, accent=0.0)

    def tick_audio(
        self, frame: int, controller: Controller, audio: AudioFrame
    ) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(
            frame,
            controller,
            drive=reactive.drive(),
            low=reactive.low,
            accent=reactive.accent if audio.beat else 0.0,
        )

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_red_sweep_available():
            return False
        if audio is None:
            drive, low, accent = 0.35, 0.25, 0.0
        else:
            reactive = AudioReactive.from_frame(audio)
            drive, low, accent = (
                reactive.drive(),
                reactive.low,
                reactive.accent if audio.beat else 0.0,
            )
        length = len(destination)
        width_scale = self.setting("width", 30) / 30.0
        width = max(1, round(length * (0.12 + low * 0.10) * width_scale))
        limit = max(length - width, 0)
        speed = (0.35 + drive * 0.85 + accent * 0.55) * (
            0.25 + self.setting("speed", 50) / 100.0 * 1.5
        )
        self.position += self.direction * speed
        if self.position >= limit:
            overshoot = self.position - limit
            self.position = max(0.0, limit - overshoot)
            self.direction = -1.0
        elif self.position <= 0.0:
            self.position = min(float(limit), -self.position)
            self.direction = 1.0
        start = round(self.position)
        on_frames = max(1, round(10 * self.setting("duty_cycle", 90) / 100.0))
        on = frame % 10 < on_frames
        alpha = self._native_rgba(RED, brightness)[3]
        return render_red_sweep_into(destination, start, width, on, alpha)

    def _step(
        self,
        frame: int,
        controller: Controller,
        *,
        drive: float,
        low: float,
        accent: float,
    ) -> None:
        length = controller.length
        controller.clear()
        if length <= 0:
            return

        width_scale = self.setting("width", 30) / 30.0
        width = max(1, round(length * (0.12 + low * 0.10) * width_scale))
        limit = max(length - width, 0)
        speed = (0.35 + drive * 0.85 + accent * 0.55) * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        self.position += self.direction * speed

        if self.position >= limit:
            overshoot = self.position - limit
            self.position = max(0.0, limit - overshoot)
            self.direction = -1.0
        elif self.position <= 0.0:
            self.position = min(float(limit), -self.position)
            self.direction = 1.0

        start = round(self.position)
        on_frames = max(1, round(10 * self.setting("duty_cycle", 90) / 100.0))
        if frame % 10 >= on_frames:
            return
        for index in range(start, min(start + width, length)):
            controller.set_pixel(index, RED)


@dataclass(slots=True)
class RedRaveChase(Animation):
    """Mirrored red blocks chase from both ends and reverse on beats."""

    native_render_enabled = True

    phase: float = 0.0
    direction: float = 1.0

    @property
    def name(self) -> str:
        return "red_rave_chase"

    def tick(self, frame: int, controller: Controller) -> None:
        self._step(controller, drive=0.4, beat=frame > 0 and frame % 18 == 0)

    def tick_audio(
        self, frame: int, controller: Controller, audio: AudioFrame
    ) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(controller, drive=reactive.drive(), beat=audio.beat)

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_red_chase_available():
            return False
        if audio is None:
            drive, beat = 0.4, frame > 0 and frame % 18 == 0
        else:
            drive, beat = AudioReactive.from_frame(audio).drive(), audio.beat
        length = len(destination)
        count = self.setting("count", 4)
        period = max(2, round(length / count))
        block = max(1, round(period * self.setting("duty_cycle", 50) / 100.0))
        if beat:
            self.direction *= -1.0
        self.phase = (
            self.phase
            + self.direction
            * (0.45 + drive * 0.9)
            * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        ) % period
        alpha = self._native_rgba(RED, brightness)[3]
        return render_red_chase_into(destination, period, block, self.phase, alpha)

    def _step(self, controller: Controller, *, drive: float, beat: bool) -> None:
        length = controller.length
        controller.clear()
        if length <= 0:
            return

        count = self.setting("count", 4)
        period = max(2, round(length / count))
        block = max(1, round(period * self.setting("duty_cycle", 50) / 100.0))
        if beat:
            self.direction *= -1.0
        self.phase = (
            self.phase + self.direction * (0.45 + drive * 0.9) * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        ) % period

        midpoint = (length + 1) // 2
        for index in range(length):
            mirrored_index = index if index < midpoint else length - 1 - index
            if (mirrored_index + self.phase) % period < block:
                controller.set_pixel(index, RED)


@dataclass(slots=True)
class RedBlackoutStrobe(Animation):
    """Short, pure-red flashes separated by complete blackouts."""

    native_render_enabled = True
    # The C fill only beats NumPy's bulk fill at the largest measured size.
    native_render_min_pixels = 600

    flash_frames: int = 0
    _last_beat_frame: int = -10_000

    @property
    def name(self) -> str:
        return "red_blackout_strobe"

    def tick(self, frame: int, controller: Controller) -> None:
        period = max(2, round(20 - self.setting("flash_rate", 50) * 0.2))
        on_frames = max(1, round(period * self.setting("duty_cycle", 30) / 100.0))
        self._paint(controller, on=frame % period < on_frames)

    def tick_audio(
        self, frame: int, controller: Controller, audio: AudioFrame
    ) -> None:
        min_gap = max(1, round(20 - self.setting("flash_rate", 50) * 0.2))
        if audio.beat and frame - self._last_beat_frame >= min_gap:
            self._last_beat_frame = frame
            self.flash_frames = max(1, round(5 * self.setting("duty_cycle", 30) / 100.0))
        on = self.flash_frames > 0
        self.flash_frames = max(self.flash_frames - 1, 0)
        self._paint(controller, on=on)

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_solid_render_available():
            return False
        if audio is None:
            period = max(2, round(20 - self.setting("flash_rate", 50) * 0.2))
            on_frames = max(1, round(period * self.setting("duty_cycle", 30) / 100.0))
            on = frame % period < on_frames
        else:
            min_gap = max(1, round(20 - self.setting("flash_rate", 50) * 0.2))
            if audio.beat and frame - self._last_beat_frame >= min_gap:
                self._last_beat_frame = frame
                self.flash_frames = max(
                    1, round(5 * self.setting("duty_cycle", 30) / 100.0)
                )
            on = self.flash_frames > 0
            self.flash_frames = max(self.flash_frames - 1, 0)
        rgba = self._native_rgba(RED, brightness) if on else (0, 0, 0, 255)
        return render_solid_rgba_into(destination, rgba)

    @staticmethod
    def _paint(controller: Controller, *, on: bool) -> None:
        if on:
            controller.fill(RED)
        else:
            controller.clear()
