from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import beam_profile, club_color
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class RaveScanner(Animation):
    phase: float = 0.0
    direction: float = 1.0
    flash: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "rave_scanner"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(AudioFrame(rms=0.2, bands=(0.2, 0.24, 0.22, 0.2, 0.18, 0.16, 0.14, 0.12)))
        self._step(frame, controller, reactive, beat=frame % 19 == 0)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(frame, controller, reactive, beat=audio.beat)

    def _step(self, frame: int, controller: Controller, reactive: AudioReactive, *, beat: bool) -> None:
        length = max(controller.length, 1)
        if beat:
            self.direction *= -1.0
            self.flash.value = 1.0

        width = 2.0 + reactive.low * 5.0
        speed = reactive.speed(0.65, 1.75) + reactive.low * 0.35
        self.phase += self.direction * max(0.45, speed)
        max_pos = float(length - 1)
        if self.phase <= 0.0 or self.phase >= max_pos:
            self.direction *= -1.0
            self.phase = min(max(self.phase, 0.0), max_pos)

        flash = self.flash.step(0.0, 0.16)
        hue = reactive.hue_shift(frame, 2.2)
        brightness = min(1.0, 0.25 + reactive.low * 0.6 + flash * 0.5)
        controller.clear()
        for index in range(length):
            distance = abs(index - self.phase)
            if distance <= width:
                intensity = beam_profile(distance, width)
                alpha = min(1.0, brightness * (0.25 + intensity * 0.75))
                controller.set_pixel(index, club_color(hue + int(distance * 18.0), alpha=alpha, lightness=60))
