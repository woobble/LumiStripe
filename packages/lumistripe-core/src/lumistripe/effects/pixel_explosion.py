from __future__ import annotations

from dataclasses import dataclass, field
from random import Random

import numpy as np

from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla
from ..controller import Controller
from ..render import native_pixel_explosion_available, render_pixel_explosion_into
from .base import Effect


@dataclass(slots=True)
class _Burst:
    x: float
    radius: float
    hue: int
    alpha: Decay


@dataclass(slots=True)
class PixelExplosion(Effect):
    native_render_enabled = True
    bursts: list[_Burst] = field(default_factory=list)
    _rng: Random = field(default_factory=Random)
    _native_bursts: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._native_bursts = np.empty((0, 4), dtype=np.float64)

    @property
    def name(self) -> str:
        return "pixel_explosion"

    def tick(self, frame: int, controller: Controller) -> None:
        if frame % 12 < 2:
            x = self._rng.randint(0, max(controller.length - 1, 0))
            self.bursts.append(
                _Burst(x=float(x), radius=0.0, hue=(frame * 17) % 256, alpha=Decay(1.0))
            )
            self.bursts = self.bursts[-self.setting("count", 4):]
        self._update(controller, 0.6)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        count_setting = self.setting("count", 4)
        spread = self.setting("spread", 60) / 100.0
        decay = self.setting("decay", 50) / 100.0
        if audio.beat or reactive.accent > 0.5:
            count = min(1 + int(reactive.accent * 2 + reactive.drive() * count_setting), count_setting)
            for _ in range(count):
                center = (controller.length - 1) / 2.0
                spread_px = center * spread
                x = round(center + self._rng.uniform(-spread_px, spread_px))
                hue = (
                    int(reactive.low * 90) + frame * 6 + self._rng.randint(0, 120)
                ) % 256
                self.bursts.append(
                    _Burst(
                        x=float(x),
                        radius=0.0,
                        hue=hue,
                        alpha=Decay(0.7 + reactive.accent * 0.3),
                    )
                )
            self.bursts = self.bursts[-count_setting:]
        self._update(controller, (0.8 + reactive.high * 1.2) * (0.25 + self.setting("spread", 60) / 100.0 * 1.5), decay=decay)

    def _update(self, controller: Controller, speed: float, *, decay: float = 0.5) -> None:
        max_pos = float(max(controller.length - 1, 0))
        for b in list(self.bursts):
            b.radius += speed
            b.alpha.step(0.0, 0.015 + decay * 0.05)
            if b.alpha.value < 0.01 or b.radius > max_pos + 5.0:
                self.bursts.remove(b)
                continue
            for i in range(controller.length):
                dist = abs(i - b.x)
                width = max(1.0, controller.length * self.setting("spread", 60) / 100.0 * 0.035)
                ring = max(1.0 - abs(dist - b.radius) / width, 0.0)
                if ring > 0.0:
                    alpha = ring * b.alpha.value
                    if alpha > 0.01:
                        controller.set_pixel(i, Hsla(b.hue, 100, 55, alpha))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_pixel_explosion_available():
            return False
        length = int(destination.shape[0])
        count_setting = int(self.setting("count", 4))
        spread = float(self.setting("spread", 60)) / 100.0
        if audio is None:
            if frame % 12 < 2:
                x = self._rng.randint(0, max(length - 1, 0))
                self.bursts.append(
                    _Burst(
                        x=float(x),
                        radius=0.0,
                        hue=(frame * 17) % 256,
                        alpha=Decay(1.0),
                    )
                )
                self.bursts = self.bursts[-count_setting:]
            speed = 0.6
            decay = 0.5
        else:
            reactive = AudioReactive.from_frame(audio)
            decay = float(self.setting("decay", 50)) / 100.0
            if audio.beat or reactive.accent > 0.5:
                count = min(
                    1 + int(reactive.accent * 2 + reactive.drive() * count_setting),
                    count_setting,
                )
                for _ in range(count):
                    center = (length - 1) / 2.0
                    spread_px = center * spread
                    x = round(center + self._rng.uniform(-spread_px, spread_px))
                    hue = (
                        int(reactive.low * 90)
                        + frame * 6
                        + self._rng.randint(0, 120)
                    ) % 256
                    self.bursts.append(
                        _Burst(
                            x=float(x),
                            radius=0.0,
                            hue=hue,
                            alpha=Decay(0.7 + reactive.accent * 0.3),
                        )
                    )
                self.bursts = self.bursts[-count_setting:]
            speed = (0.8 + reactive.high * 1.2) * (0.25 + spread * 1.5)
        max_pos = float(max(length - 1, 0))
        active: list[_Burst] = []
        for burst in list(self.bursts):
            burst.radius += speed
            burst.alpha.step(0.0, 0.015 + decay * 0.05)
            if burst.alpha.value < 0.01 or burst.radius > max_pos + 5.0:
                self.bursts.remove(burst)
            else:
                active.append(burst)
        self.bursts = active
        if self._native_bursts.shape[0] < len(self.bursts):
            self._native_bursts = np.empty(
                (max(8, len(self.bursts)), 4), dtype=np.float64
            )
        for index, burst in enumerate(self.bursts):
            self._native_bursts[index] = (
                burst.x,
                burst.radius,
                burst.hue,
                burst.alpha.value,
            )
        width = max(1.0, length * spread * 0.035)
        return render_pixel_explosion_into(
            destination,
            self._native_bursts,
            len(self.bursts),
            width,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
