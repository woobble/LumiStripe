from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..animation.reactive import AudioReactive
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla, Rgba
from ..controller import Controller
from ..render import native_firework_burst_available, render_firework_burst_into
from .base import Effect


@dataclass(slots=True)
class _Burst:
    phase: float = 0.0
    speed: float = 1.0
    strength: float = 1.0
    hue: int = 0


@dataclass(slots=True)
class FireworkBurst(Effect):
    native_render_enabled = True
    bursts: list[_Burst] = field(default_factory=list)
    _hue: int = 0
    _native_phases: np.ndarray = field(init=False, repr=False)
    _native_bursts: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._native_phases = np.zeros(12, dtype=np.float64)
        self._native_bursts = np.zeros((12, 3), dtype=np.float64)

    @property
    def name(self) -> str:
        return "firework_burst"

    def tick(self, frame: int, controller: Controller) -> None:
        center = max(controller.length - 1, 0) * 0.5
        spread = 0.5 + self.setting("spread", 60) / 100.0
        count = self.setting("count", 4)
        phases = [
            (frame * 0.25 * spread + index * (center + 4.0) / count)
            % (center + 4.0)
            for index in range(count)
        ]
        fade = self.setting("fade_time", 50) / 100.0
        fade_alpha = max(0.25, 1.0 - (frame % 36) / 36.0 * (1.0 - fade * 0.75))
        burst_id = frame // 36
        seed = burst_id * 1103515245 + 12345
        hue = ((seed // 100) % 256 + frame * 2) % 256
        for i in range(controller.length):
            dist = abs(i - center)
            alpha = 0.0
            for phase in phases:
                if dist <= phase:
                    sparkle_dist = (phase - dist) / max(phase, 1.0)
                    candidate = max(1.0 - sparkle_dist * 1.5, 0.0)
                    offset = int(i * 7 + burst_id * 13) % 5
                    variance = (offset - 2) * 0.04
                    alpha = max(alpha, max(candidate + variance, 0.0))
            if alpha > 0.0:
                controller.set_pixel(i, Hsla(hue, 100, 60, alpha * 0.85 * fade_alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        center = max(controller.length - 1, 0) * 0.5
        count = self.setting("count", 4)
        spread = 0.5 + self.setting("spread", 60) / 100.0
        fade = self.setting("fade_time", 50) / 100.0

        if audio.beat:
            self._hue = (self._hue + 41) % 256
            self.bursts.append(
                _Burst(
                    phase=0.5,
                    speed=(0.5
                    + reactive.speed(0.15, 2.5) * (0.8 + reactive.accent * 0.4)) * spread,
                    strength=0.7 + reactive.accent * 0.3,
                    hue=self._hue,
                )
            )
            self.bursts = self.bursts[-count:]

        for b in self.bursts:
            b.phase += b.speed
            b.strength *= 0.90 + fade * 0.14
        self.bursts = [
            b for b in self.bursts if b.strength > 0.02 and b.phase < center + 6.0
        ]

        bg_glow = reactive.rms * 0.08
        for i in range(controller.length):
            dist = abs(i - center)
            pixel_alpha = bg_glow
            pixel_hue = reactive.hue_shift(frame, 0.25)
            for b in self.bursts:
                if dist <= b.phase:
                    sparkle_dist = (b.phase - dist) / max(b.phase, 1.0)
                    wave_alpha = max(1.0 - sparkle_dist * 1.8, 0.0) * b.strength
                    particle = int(i * 7 + int(b.phase * 10) * 13) % 7
                    variance = (particle - 3) * 0.06
                    wave_alpha = max(wave_alpha + variance, 0.0)
                    band_sparkle = reactive.band_at(audio, i, controller.length) * 0.2
                    pixel_alpha = min(pixel_alpha + wave_alpha + band_sparkle, 1.0)
                    if wave_alpha > 0:
                        pixel_hue = b.hue
            if pixel_alpha > 0.0:
                lightness = int(45 + reactive.mid * 20 + reactive.high * 10)
                controller.set_pixel(i, Hsla(pixel_hue, 100, lightness, pixel_alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_firework_burst_available():
            return False
        count = int(self.setting("count", 4))
        if not 1 <= count <= 12:
            return False
        length = int(destination.shape[0])
        center = max(length - 1, 0) * 0.5
        spread = 0.5 + self.setting("spread", 60) / 100.0
        fade = self.setting("fade_time", 50) / 100.0
        phase_count = 0
        burst_count = 0
        hue = 0
        fade_alpha = 1.0
        glow = mid = high = 0.0
        bands: tuple[float, ...] | None = None

        if audio is None:
            radius = center + 4.0
            for index in range(count):
                self._native_phases[index] = (
                    frame * 0.25 * spread + index * radius / count
                ) % radius
            phase_count = count
            fade_alpha = max(
                0.25,
                1.0 - (frame % 36) / 36.0 * (1.0 - fade * 0.75),
            )
            burst_id = frame // 36
            seed = burst_id * 1103515245 + 12345
            hue = ((seed // 100) % 256 + frame * 2) % 256
        else:
            reactive = AudioReactive.from_frame(audio)
            if audio.beat:
                self._hue = (self._hue + 41) % 256
                self.bursts.append(
                    _Burst(
                        phase=0.5,
                        speed=(
                            0.5
                            + reactive.speed(0.15, 2.5)
                            * (0.8 + reactive.accent * 0.4)
                        )
                        * spread,
                        strength=0.7 + reactive.accent * 0.3,
                        hue=self._hue,
                    )
                )
                self.bursts = self.bursts[-count:]
            for burst in self.bursts:
                burst.phase += burst.speed
                burst.strength *= 0.90 + fade * 0.14
            self.bursts = [
                burst
                for burst in self.bursts
                if burst.strength > 0.02 and burst.phase < center + 6.0
            ]
            burst_count = len(self.bursts)
            for index, burst in enumerate(self.bursts):
                self._native_bursts[index, 0] = burst.phase
                self._native_bursts[index, 1] = burst.strength
                self._native_bursts[index, 2] = burst.hue
            hue = reactive.hue_shift(frame, 0.25)
            glow = reactive.rms * 0.08
            mid, high = reactive.mid, reactive.high
            bands = audio.bands

        return render_firework_burst_into(
            destination,
            self._native_phases,
            phase_count,
            self._native_bursts,
            burst_count,
            frame,
            audio is not None,
            float(center),
            float(fade_alpha),
            float(glow),
            float(mid),
            float(high),
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            bands,
            int(hue),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
