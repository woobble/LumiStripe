from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import club_color, warm_flash
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class StrobeChase(Animation):
    phase: int = 0
    flash: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "strobe_chase"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(AudioFrame(rms=0.2, bands=(0.22, 0.2, 0.18, 0.16, 0.14, 0.18, 0.2, 0.22)))
        self._step(frame, controller, reactive, beat=frame % 12 == 0, bass_hit=frame % 18 == 0)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        bass_hit = audio.beat and (reactive.low > 0.34 or reactive.accent > 0.52)
        self._step(frame, controller, reactive, beat=audio.beat, bass_hit=bass_hit)

    def _step(self, frame: int, controller: Controller, reactive: AudioReactive, *, beat: bool, bass_hit: bool) -> None:
        stride = 3 if reactive.high < 0.65 else 4
        if beat:
            self.phase = (self.phase + 1) % stride
        else:
            self.phase = (self.phase + 1 + int(reactive.drive() * 1.5)) % stride

        if bass_hit:
            self.flash.value = 1.0

        flash = self.flash.step(0.0, 0.2)
        hue = reactive.hue_shift(frame, 1.0)
        controller.clear()
        for index in range(controller.length):
            if index % stride == self.phase:
                controller.set_pixel(index, club_color(hue + index * 11, alpha=min(1.0, 0.38 + reactive.drive() * 0.62)))
        if flash > 0.0:
            controller.fill(warm_flash(hue + frame, alpha=min(1.0, 0.12 + flash * 0.88)))
