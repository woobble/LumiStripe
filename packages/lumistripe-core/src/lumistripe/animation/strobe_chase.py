from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..controller import Controller
from ..render import native_strobe_chase_available, render_strobe_chase_into
from .base import Animation
from .club_utils import club_color, warm_flash
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class StrobeChase(Animation):
    native_render_enabled = True

    phase: float = 0.0
    hue_phase: float = 0.0
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
        stride = max(2, self.setting("count", 4))
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        advance = (0.3 + reactive.drive() * 0.65) * speed_scale
        self.phase = (self.phase + advance + (0.25 if beat else 0.0)) % stride
        self.hue_phase = (self.hue_phase + 0.6 + reactive.high * 1.2) % 360.0

        if bass_hit:
            self.flash.value = 1.0

        release = 0.02 + (1.0 - self.setting("duty_cycle", 30) / 100.0) * 0.24
        flash = self.flash.step(0.0, release)
        hue = int(self.hue_phase)
        controller.clear()
        for index in range(controller.length):
            slot = index % stride
            direct = abs(slot - self.phase)
            distance = min(direct, stride - direct)
            chase = max(1.0 - distance / 1.2, 0.0)
            if chase > 0.01:
                alpha = chase * (0.34 + reactive.drive() * 0.5)
                controller.set_pixel(index, club_color(hue + index * 11, alpha=alpha))
        if flash > 0.0:
            controller.fill(warm_flash(hue + frame, alpha=min(1.0, 0.12 + flash * 0.88)))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_strobe_chase_available():
            return False
        reactive = AudioReactive.from_frame(
            audio
            if audio is not None
            else AudioFrame(
                rms=0.2,
                bands=(0.22, 0.2, 0.18, 0.16, 0.14, 0.18, 0.2, 0.22),
            )
        )
        if audio is None:
            beat = frame % 12 == 0
            bass_hit = frame % 18 == 0
        else:
            beat = audio.beat
            bass_hit = audio.beat and (reactive.low > 0.34 or reactive.accent > 0.52)
        stride = max(2, int(self.setting("count", 4)))
        speed_scale = 0.25 + float(self.setting("speed", 50)) / 100.0 * 1.5
        advance = (0.3 + reactive.drive() * 0.65) * speed_scale
        self.phase = (self.phase + advance + (0.25 if beat else 0.0)) % stride
        self.hue_phase = (self.hue_phase + 0.6 + reactive.high * 1.2) % 360.0
        if bass_hit:
            self.flash.value = 1.0
        release = 0.02 + (
            1.0 - float(self.setting("duty_cycle", 30)) / 100.0
        ) * 0.24
        flash = self.flash.step(0.0, release)
        hue = int(self.hue_phase)
        return render_strobe_chase_into(
            destination,
            stride,
            self.phase,
            hue,
            frame,
            reactive.drive(),
            flash,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            flash > 0.0,
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
