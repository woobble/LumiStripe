from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.club_utils import band_alpha, warm_flash
from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..color import Hsla
from ..controller import Controller
from .base import Effect


@dataclass(slots=True)
class SpectrumFlash(Effect):
    flash: Decay = field(default_factory=Decay)
    hue_phase: float = 0.0

    @property
    def name(self) -> str:
        return "spectrum_flash"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(
            AudioFrame(rms=0.18, bands=(0.2, 0.22, 0.26, 0.24, 0.2, 0.18, 0.16, 0.14))
        )
        self._step(
            frame,
            controller,
            reactive,
            AudioFrame(rms=0.18, bands=(0.2, 0.22, 0.26, 0.24, 0.2, 0.18, 0.16, 0.14)),
            beat=frame % 27 == 0,
        )

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(frame, controller, reactive, audio, beat=audio.beat)

    def _step(
        self,
        frame: int,
        controller: Controller,
        reactive: AudioReactive,
        audio: AudioFrame,
        *,
        beat: bool,
    ) -> None:
        if (
            reactive.drop_hit(beat=beat, low_threshold=0.45, accent_threshold=0.68)
            or reactive.rms > 0.82
        ):
            self.flash.value = 1.0

        flash = self.flash.step(0.0, 0.08)
        self.hue_phase = (self.hue_phase + 0.35 + reactive.high * 0.9) % 360.0
        length = max(controller.length, 1)
        controller.clear()
        for index in range(length):
            ratio = 0.0 if length <= 1 else index / (length - 1)
            band_index = min(int(ratio * len(audio.bands)), len(audio.bands) - 1)
            band = audio.bands[band_index]
            alpha = band_alpha(band, base=0.18, scale=0.72)
            hue = (self.hue_phase + ratio * 300.0) % 360.0
            controller.set_pixel(index, Hsla(hue, 100, 56, min(1.0, alpha)))

        if flash > 0.0:
            for index in range(length):
                controller.set_pixel(
                    index,
                    warm_flash(
                        int(self.hue_phase / 8.0) + index,
                        alpha=min(1.0, 0.1 + flash * 0.9),
                    ),
                )
