from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla, Rgba
from ..controller import Controller
from ..render import native_dual_comet_available, render_dual_comet_into
from .base import Animation
from .club_utils import trail_profile
from .reactive import AudioReactive, Decay


def _render_dual_comets(
    controller: Controller,
    phase: float,
    width: float,
    alpha: float,
    hue_a: int,
    hue_b: int,
    separation: float,
) -> None:
    max_pos = float(max(controller.length - 1, 0))
    other = (phase + max_pos * separation) % max(max_pos + 1.0, 1.0)
    for i in range(controller.length):
        dist_a = abs(i - phase)
        dist_b = abs(i - other)
        tail_a = trail_profile(dist_a, width)
        tail_b = trail_profile(dist_b, width)
        if tail_a > tail_b and tail_a > 0.0:
            controller.set_pixel(i, Hsla(hue_a, 100, 55, min(tail_a * alpha, 1.0)))
        elif tail_b > 0.0:
            controller.set_pixel(i, Hsla(hue_b, 100, 55, min(tail_b * alpha, 1.0)))
        else:
            controller.set_pixel(i, Rgba(0, 0, 0, 0.0))


@dataclass(slots=True)
class DualComet(Animation):
    native_render_enabled = True

    phase: float = 0.0
    direction: float = 1.0
    burst: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "dual_comet"

    def tick(self, frame: int, controller: Controller) -> None:
        speed = 0.7 * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        self.phase += speed * self.direction
        max_pos = float(max(controller.length - 1, 0))
        if self.phase >= max_pos or self.phase <= 0.0:
            self.direction *= -1.0
            self.phase = min(max(self.phase, 0.0), max_pos)
        width = max(1.0, controller.length * self.setting("tail_length", 8) / 100.0)
        separation = self.setting("separation", 50) / 100.0
        _render_dual_comets(controller, self.phase, width, 0.85, 40, 220, separation)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self.burst.step(reactive.accent, 0.045)
        if audio.beat:
            self.direction *= -1.0
        speed = reactive.speed(0.42, 2.1) * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        self.phase += speed * self.direction
        max_pos = float(max(controller.length - 1, 0))
        if self.phase >= max_pos or self.phase <= 0.0:
            self.direction *= -1.0
            self.phase = min(max(self.phase, 0.0), max_pos)
        width = max(1.0, controller.length * self.setting("tail_length", 8) / 100.0)
        alpha = min(0.85 + reactive.beat_pulse(0.0, 0.15), 1.0)
        hue_a = 10 + int(reactive.low * 90.0)
        hue_b = 150 + int(reactive.high * 80.0)
        separation = self.setting("separation", 50) / 100.0
        _render_dual_comets(controller, self.phase, width, alpha, hue_a, hue_b, separation)

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_dual_comet_available():
            return False
        length = destination.shape[0]
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        max_pos = float(max(length - 1, 0))
        if audio is None:
            self.phase += 0.7 * speed_scale * self.direction
            alpha, hue_a, hue_b = 0.85, 40, 220
        else:
            reactive = AudioReactive.from_frame(audio)
            self.burst.step(reactive.accent, 0.045)
            if audio.beat:
                self.direction *= -1.0
            self.phase += reactive.speed(0.42, 2.1) * speed_scale * self.direction
            alpha = min(0.85 + reactive.beat_pulse(0.0, 0.15), 1.0)
            hue_a = 10 + int(reactive.low * 90.0)
            hue_b = 150 + int(reactive.high * 80.0)
        if self.phase >= max_pos or self.phase <= 0.0:
            self.direction *= -1.0
            self.phase = min(max(self.phase, 0.0), max_pos)
        width = max(1.0, length * self.setting("tail_length", 8) / 100.0)
        separation = self.setting("separation", 50) / 100.0
        return render_dual_comet_into(
            destination,
            self.phase,
            width,
            float(alpha),
            float(separation),
            hue_a,
            hue_b,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
