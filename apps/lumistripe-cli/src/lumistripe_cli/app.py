from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import time
from dataclasses import dataclass, field
from enum import Enum

from lumistripe import (
    AnimationClass,
    AnimationPlayer,
    AudioConfig,
    AudioCalibrationResult,
    AudioFrame,
    AudioInput,
    AudioNormalization,
    AudioSnapshot,
    AudioSmoothing,
    CLASS_MAP,
    Config,
    Controller,
    GPIOStripe,
    MultiController,
    MusicDrivenSelector,
    MusicFeatures,
    MusicSelectorConfig,
    Stripe,
    calibrate_audio_input,
    features_from_frame,
    list_input_device_details,
)
from .encoder import ControlEvent, EncoderBackend, EncoderPins, NullEncoderBackend, build_encoder_backend

MIN_FRAME_SECONDS = 0.016
STATUS_INTERVAL_SECONDS = 0.25
DEFAULT_IDLE_THRESHOLD_SCALE = 1.0
BRIGHTNESS_STEP = 0.05


class RuntimeMode(str, Enum):
    MANUAL = "manual"
    DEMO = "demo"
    MIC = "mic"


def demo_frame(frame: int) -> AudioFrame:
    frames_per_beat = 25
    frames_per_measure = frames_per_beat * 4

    measure_pos = frame % frames_per_measure
    beat_idx = measure_pos // frames_per_beat
    phase = (measure_pos % frames_per_beat) / frames_per_beat

    decay = pow(2.718281828, -phase * 5.0)
    fast = pow(2.718281828, -phase * 12.0)
    slow = pow(2.718281828, -phase * 2.0)

    kick = fast if beat_idx in (0, 2) else 0.001
    snare = decay * 0.8 if beat_idx in (1, 3) else 0.001
    bass_harmonic = (1.0, 1.0, 1.0, 0.75)[(frame // frames_per_measure) % 4]
    bass = slow * 0.6 * bass_harmonic

    pos_in_beat = measure_pos % frames_per_beat
    hat = 0.35 if pos_in_beat < 2 or 12 <= pos_in_beat < 14 else 0.001
    accent = 0.2 if 62 <= measure_pos < 64 else 0.0

    bands = (
        min(kick, 1.0),
        min(bass, 1.0),
        min(snare * 0.3 + kick * 0.15, 1.0),
        min(snare, 1.0),
        min(snare * 0.5 + hat * 0.3, 1.0),
        min(hat + accent, 1.0),
        min((hat + accent) * 0.4, 1.0),
        min((hat + accent) * 0.15, 1.0),
    )
    rms = min((sum(bands) / len(bands)) ** 0.5, 1.0)
    beat = beat_idx == 0
    beat_strength = 0.7 + kick * 0.3 if beat else 0.0
    return AudioFrame(rms=rms, bands=bands, beat=beat, beat_strength=beat_strength, sequence=frame + 1, timestamp=time.monotonic(), fresh=True)


@dataclass(slots=True)
class HeadlessApp:
    controller: Controller
    pixel_count: int
    mode: RuntimeMode = RuntimeMode.MANUAL
    audio_device: str | None = None
    mic_target_level: float = field(default_factory=lambda: AudioNormalization().target_level)
    mic_noise_floor: float = field(default_factory=lambda: AudioSmoothing().noise_floor)
    idle_enter_frames: int = field(default_factory=lambda: MusicSelectorConfig().idle_enter_frames)
    idle_threshold_scale: float = DEFAULT_IDLE_THRESHOLD_SCALE
    audio_debug_verbose: bool = False
    audio_calibration: AudioCalibrationResult | None = None
    animation_name: str | None = None
    quiet: bool = False
    gpio_backend_label: str | None = None
    encoder_backend: EncoderBackend = field(default_factory=NullEncoderBackend)
    player: AnimationPlayer = field(init=False)
    running: bool = field(init=False, default=True)
    audio_input: AudioInput | None = field(init=False, default=None)
    audio_status: str = field(init=False, default="No audio source active.")
    audio_error: str | None = field(init=False, default=None)
    audio_frame: AudioFrame = field(init=False, default_factory=AudioFrame)
    music_features: MusicFeatures = field(init=False, default_factory=MusicFeatures)
    audio_snapshot: AudioSnapshot = field(init=False, default_factory=AudioSnapshot.silence)
    demo_tick: int = field(init=False, default=0)
    selector: MusicDrivenSelector | None = field(init=False, default=None)
    _status_enabled: bool = field(init=False, default=False)
    _status_tty: bool = field(init=False, default=False)
    _status_lines: int = field(init=False, default=0)
    _last_status_at: float = field(init=False, default=0.0)
    _last_debug_class: str = field(init=False, default="-")
    _auto_select_enabled: bool = field(init=False, default=True)

    def __post_init__(self) -> None:
        self.player = AnimationPlayer.party()
        self.player.set_brightness(1.0)
        if self.animation_name is not None:
            index = self.player.index_of(self.animation_name)
            if index is None:
                raise ValueError(f"unknown animation: {self.animation_name}")
            self.player.set_index(index)
        self._status_enabled = not self.quiet
        self._status_tty = self._status_enabled and sys.stdout.isatty()
        self.set_mode(self.mode)

    @property
    def current_animation_label(self) -> str:
        raw = self.player.name_at(self.player.current_index()) or "?"
        classes = CLASS_MAP.get(raw, ())
        label = raw.upper()
        if classes:
            tags = ", ".join(c.value.upper() for c in classes)
            label = f"{label}  [{tags}]"
        return label

    @property
    def mode_label(self) -> str:
        return self.mode.value.upper()

    @property
    def class_label(self) -> str:
        if self.selector is None:
            return "-"
        if self.selector.idle_active:
            return "IDLE"
        return self.selector.current_class.value.upper()

    def analysis_text(self) -> str:
        frame = self.audio_frame
        feat = self.music_features
        beat = "YES" if frame.beat else "NO"
        bands = " ".join(f"{value:0.2f}" for value in frame.bands)
        return (
            f"RMS: {frame.rms:0.3f}    BEAT: {beat}    BPM: {feat.bpm:3.0f}\n"
            f"BRIGHT: {feat.brightness:0.2f}    ONSET: {feat.onset_strength:0.3f}    DYN: {feat.dynamic_range:0.3f}\n"
            f"BANDS: {bands}"
        )

    @property
    def mic_tuning_label(self) -> str:
        label = (
            f"target={self.mic_target_level:0.2f} "
            f"noise={self.mic_noise_floor:0.3f} "
            f"idle={self.idle_enter_frames}f "
            f"scale={self.idle_threshold_scale:0.2f}"
        )
        if self.audio_calibration is not None:
            label = f"{label} calibrated={self.audio_calibration.samples}f"
        return label

    @property
    def brightness_label(self) -> str:
        return f"{self.player.brightness:0.2f}"

    def set_mode(self, mode: RuntimeMode) -> None:
        self._close_audio_input()
        self.player.clear_audio_snapshot()
        self.mode = mode
        self.audio_error = None
        self.audio_frame = AudioFrame()
        self.music_features = MusicFeatures()
        self.audio_snapshot = AudioSnapshot.silence()
        self.selector = None

        if mode is RuntimeMode.MANUAL:
            self.audio_status = "No audio source active."
            return

        if mode is RuntimeMode.DEMO:
            self.demo_tick = 0
            self.audio_status = "Using internal demo beat."
            self.player.set_audio_snapshot(self._demo_snapshot)
            return

        try:
            audio_config = _build_audio_config(
                target_level=self.mic_target_level,
                noise_floor=self.mic_noise_floor,
            )
            self.audio_input = (
                AudioInput.with_device_config(self.audio_device, audio_config)
                if self.audio_device
                else AudioInput.with_config(audio_config)
            )
        except RuntimeError as exc:
            self.mode = RuntimeMode.MANUAL
            self.audio_error = str(exc)
            self.audio_status = "Microphone unavailable."
            return

        self.audio_status = f"Input: {self.audio_input.device_name()}"
        self.selector = MusicDrivenSelector(
            config=_build_selector_config(
                idle_enter_frames=self.idle_enter_frames,
                idle_threshold_scale=self.idle_threshold_scale,
            ),
            current_class=AnimationClass.GROOVY,
        )
        self.selector.set_auto_select(self._auto_select_enabled)
        self.player.set_audio_snapshot(self._mic_snapshot)

    def _demo_snapshot(self) -> AudioFrame:
        frame = demo_frame(self.demo_tick)
        self.demo_tick += 1
        self.audio_frame = frame
        self.music_features = features_from_frame(frame)
        self.audio_snapshot = AudioSnapshot.from_parts(frame, self.music_features)
        return frame

    def _mic_snapshot(self) -> AudioFrame:
        return self.audio_snapshot.frame

    def _close_audio_input(self) -> None:
        if self.audio_input is None:
            return
        self.audio_input.close()
        self.audio_input = None

    def step(self) -> float:
        if self.mode is RuntimeMode.MIC and self.audio_input is not None:
            audio_frame = self.audio_input.read()
            health = self._audio_health()
            self.audio_frame = audio_frame
            self.music_features = self.audio_input.read_features() if audio_frame.fresh else MusicFeatures()
            self.audio_snapshot = (
                AudioSnapshot.from_parts(audio_frame, self.music_features, health)
                if audio_frame.fresh
                else AudioSnapshot.silence(frame=audio_frame, health=health)
            )
            if self.selector is not None:
                self.selector.update(self.player, self.audio_snapshot.features)
                self.player.audio_enabled = not self.selector.idle_active
        delay = max(self.player.step(self.controller), MIN_FRAME_SECONDS)
        if self.mode is RuntimeMode.MANUAL:
            self.audio_frame = AudioFrame()
            self.music_features = MusicFeatures()
            self.audio_snapshot = AudioSnapshot.silence()
        return delay

    def apply_control_events(self, events: list[ControlEvent]) -> None:
        for event in events:
            self.apply_control_event(event)

    def apply_control_event(self, event: ControlEvent) -> None:
        if event.kind == "rotate" and event.source == "encoder1":
            self._handle_animation_rotation(event.value)
            return
        if event.kind == "rotate" and event.source == "encoder2":
            self.player.set_brightness(self.player.brightness + (BRIGHTNESS_STEP * event.value))
            return
        if event.kind == "press" and event.source == "encoder1":
            self._cycle_mode()
            return
        if event.kind == "press" and event.source == "encoder2":
            self._set_auto_select(not self._auto_select_enabled)

    def _handle_animation_rotation(self, step: int) -> None:
        self._set_auto_select(False)
        if step > 0:
            for _ in range(step):
                self.player.next()
            return
        for _ in range(abs(step)):
            self.player.prev()

    def _cycle_mode(self) -> None:
        if self.mode is RuntimeMode.MANUAL:
            self.set_mode(RuntimeMode.DEMO)
        elif self.mode is RuntimeMode.DEMO:
            self.set_mode(RuntimeMode.MIC)
        else:
            self.set_mode(RuntimeMode.MANUAL)

    def _set_auto_select(self, enabled: bool) -> None:
        self._auto_select_enabled = enabled
        if self.selector is not None:
            self.selector.set_auto_select(enabled)

    def run(self, *, frame_limit: int | None = None) -> None:
        frames_run = 0
        next_frame_at = time.monotonic()
        try:
            while self.running:
                self.apply_control_events(self.encoder_backend.read_events())
                now = time.monotonic()
                if now < next_frame_at:
                    time.sleep(next_frame_at - now)
                    continue
                next_frame_at = now + self.step()
                self._render_status(now)
                frames_run += 1
                if frame_limit is not None and frames_run >= frame_limit:
                    break
        except KeyboardInterrupt:
            self.running = False
        finally:
            self.controller.clear()
            self.controller.force_flush()
            self._finish_status()
            self.encoder_backend.close()
            self._close_audio_input()

    def _render_status(self, now: float) -> None:
        if not self._status_enabled:
            return
        if not self._status_due(now):
            return
        block = self._status_block()
        if self._status_tty:
            self._write_live_status(block)
            return
        print(block, flush=True)
        self._last_status_at = now

    def _status_block(self) -> str:
        auto_status = "ON" if self._auto_select_enabled else "OFF"
        lines = [
            f"ANIM: {self.current_animation_label}",
            f"MODE: {self.mode_label}  |  AUTO: {auto_status}  |  OUT: {self.brightness_label}",
            f"SOURCE: {self.audio_status}",
            f"CLASS: {self.class_label}",
            self.analysis_text(),
        ]
        if self.gpio_backend_label:
            lines.insert(3, f"GPIO: {self.gpio_backend_label}")
        if self.mode is RuntimeMode.MIC:
            lines.insert(4, f"MIC: {self.mic_tuning_label}")
            health = self._audio_health_summary()
            if health:
                lines.insert(5, f"HEALTH: {health}")
        if self.audio_error:
            lines.append(f"ERROR: {self.audio_error}")
        return "\n".join(lines)

    def _write_live_status(self, block: str) -> None:
        lines = block.count("\n") + 1
        if self._status_lines:
            sys.stdout.write(f"\x1b[{self._status_lines}F")
        sys.stdout.write("\x1b[J")
        sys.stdout.write(block)
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._status_lines = lines
        self._last_status_at = time.monotonic()

    def _status_due(self, now: float) -> bool:
        return self._last_status_at == 0.0 or (now - self._last_status_at) >= STATUS_INTERVAL_SECONDS

    def _finish_status(self) -> None:
        if not self._status_enabled or not self._status_tty:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._status_lines = 0

    def debug_header(self) -> str:
        verbose = " verbose=ON" if self.audio_debug_verbose else ""
        return f"AUDIO-DEBUG SOURCE={self.audio_status} MIC={self.mic_tuning_label}{verbose}"

    def _selector_scores(self) -> dict[str, float]:
        if self.selector is None:
            return {}
        return {class_.value: score for class_, score in self.selector._class_scores().items()}

    def _selector_top_summary(self) -> str:
        scores = self._selector_scores()
        if not scores:
            return "TOP=-"
        top = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]
        formatted = ",".join(f"{name}:{score:0.2f}" for name, score in top)
        return f"TOP={formatted}"

    def _selector_verbose_summary(self) -> str:
        if self.selector is None:
            return "SEL=-"
        sel = self.selector
        scores = ",".join(
            f"{name}:{score:0.2f}" for name, score in sorted(self._selector_scores().items(), key=lambda item: item[0])
        )
        return (
            f"SEL=energy_short:{sel.energy_short:0.3f},"
            f"energy_long:{sel.energy_long:0.3f},"
            f"beat_density:{sel.beat_density:0.3f},"
            f"brightness:{sel.brightness_smooth:0.3f},"
            f"onset:{sel.onset_smooth:0.3f},"
            f"bass:{sel.bass_short:0.3f},"
            f"mid:{sel.mid_short:0.3f},"
            f"high:{sel.high_short:0.3f} "
            f"SCORES={scores}"
        )

    def debug_transition_line(self, elapsed: float, previous_class: str, current_class: str) -> str:
        score_snapshot = ",".join(
            f"{name}:{score:0.2f}" for name, score in sorted(self._selector_scores().items(), key=lambda item: item[1], reverse=True)
        ) or "-"
        return (
            f"t={elapsed:0.2f}s "
            f"TRANSITION CLASS={previous_class}->{current_class} "
            f"{self._selector_top_summary()} "
            f"SCORES={score_snapshot}"
        )

    def debug_log_line(self, elapsed: float) -> str:
        frame = self.audio_frame
        feat = self.music_features
        snapshot = self.audio_snapshot
        beat = "YES" if frame.beat else "NO"
        fresh = "YES" if frame.fresh else "NO"
        idle = "YES" if self.selector is not None and self.selector.idle_active else "NO"
        auto = "ON" if self.selector is not None and self.selector.auto_select else "OFF"
        anim = self.player.name_at(self.player.current_index()) or "?"
        bands = ",".join(f"{value:0.2f}" for value in frame.bands)
        silence = "YES" if feat.silence else "NO"
        drop = "YES" if feat.drop_detected else "NO"
        section = "YES" if feat.section_change else "NO"
        health = self._audio_health()
        age = health.last_frame_age if health is not None else None
        stats = health.processor if health is not None else None
        age_text = "-" if age is None else f"{age:0.3f}"
        fft_count = stats.fft_count if stats is not None else 0
        gain = stats.normalization_gain if stats is not None else 1.0
        status_count = health.status_count if health is not None else 0
        line = (
            f"t={elapsed:0.2f}s "
            f"SRC={self.audio_status} "
            f"CLASS={self.class_label} "
            f"ANIM={anim.upper()} "
            f"AUTO={auto} "
            f"IDLE={idle} "
            f"FRESH={fresh} "
            f"AGE={age_text} "
            f"SEQ={frame.sequence} "
            f"FFT={fft_count} "
            f"GAIN={gain:0.2f} "
            f"STATUS={status_count} "
            f"BASS={feat.bass_energy:0.2f} "
            f"MID={feat.mid_energy:0.2f} "
            f"TREBLE={feat.treble_energy:0.2f} "
            f"CENTROID={feat.spectral_centroid:0.2f} "
            f"FLUX={feat.spectral_flux:0.2f} "
            f"LOUD={feat.rolling_loudness:0.2f} "
            f"SILENCE={silence} "
            f"DROP={drop} "
            f"SECTION={section} "
            f"DRIVE={snapshot.drive:0.2f} "
            f"ACCENT={snapshot.accent:0.2f} "
            f"ACTIVITY={snapshot.activity:0.2f} "
            f"RMS={frame.rms:0.3f} "
            f"BEAT={beat} "
            f"BPM={feat.bpm:0.0f} "
            f"BRIGHT={feat.brightness:0.2f} "
            f"ONSET={feat.onset_strength:0.3f} "
            f"DYN={feat.dynamic_range:0.3f} "
            f"BANDS={bands} "
            f"{self._selector_top_summary()}"
        )
        if self.audio_debug_verbose:
            line = f"{line} {self._selector_verbose_summary()}"
        return line

    def _audio_health(self):
        if self.audio_input is None or not hasattr(self.audio_input, "health"):
            return None
        return self.audio_input.health()

    def _audio_health_summary(self) -> str:
        health = self._audio_health()
        if health is None:
            return ""
        age = "-" if health.last_frame_age is None else f"{health.last_frame_age:0.3f}s"
        callback_age = "-" if health.last_callback_age is None else f"{health.last_callback_age:0.3f}s"
        return (
            f"fresh={'YES' if self.audio_frame.fresh else 'NO'} "
            f"age={age} cb_age={callback_age} "
            f"seq={self.audio_frame.sequence} fft={health.processor.fft_count} "
            f"gain={health.processor.normalization_gain:0.2f} status={health.status_count}"
        )

    def run_audio_debug(self, *, frame_limit: int | None = None) -> None:
        started_at = time.monotonic()
        frames_run = 0
        next_frame_at = started_at
        self._last_debug_class = self.class_label
        print(self.debug_header(), flush=True)
        try:
            while self.running:
                now = time.monotonic()
                if now < next_frame_at:
                    time.sleep(next_frame_at - now)
                    continue
                next_frame_at = now + self.step()
                current_class = self.class_label
                if current_class != self._last_debug_class:
                    print(self.debug_transition_line(now - started_at, self._last_debug_class, current_class), flush=True)
                    self._last_debug_class = current_class
                print(self.debug_log_line(now - started_at), flush=True)
                frames_run += 1
                if frame_limit is not None and frames_run >= frame_limit:
                    break
        except KeyboardInterrupt:
            self.running = False
        finally:
            self.controller.clear()
            self.controller.force_flush()
            self.encoder_backend.close()
            self._close_audio_input()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lumistripe")
    parser.add_argument("--pixels", type=int, default=80, help="Number of LEDs per stripe")
    parser.add_argument(
        "--mode",
        type=_parse_mode,
        default=RuntimeMode.MANUAL,
        help="Startup mode: manual, demo, or mic",
    )
    parser.add_argument("--animation", help="Startup animation name for manual mode")
    parser.add_argument(
        "--audio-device",
        help="Input device index or substring match for the device name when using mic mode",
    )
    parser.add_argument(
        "--list-audio-devices",
        action="store_true",
        help="List available audio input devices and exit",
    )
    parser.add_argument(
        "--audio-debug",
        action="store_true",
        help="Run audio-only mic analysis and selector logging without GPIO output",
    )
    parser.add_argument(
        "--audio-debug-verbose",
        action="store_true",
        help="Include selector internals and full class scores in audio debug logs",
    )
    parser.add_argument(
        "--calibrate-audio",
        type=_positive_float,
        metavar="SECONDS",
        help="Measure the selected audio input and print recommended mic tuning flags",
    )
    parser.add_argument(
        "--auto-calibrate-audio",
        type=_positive_float,
        metavar="SECONDS",
        help="Measure the selected audio input and apply recommended mic tuning before mic mode starts",
    )
    parser.add_argument(
        "--mic-target-level",
        type=_positive_float,
        default=AudioNormalization().target_level,
        help="Normalized input level target for mic mode calibration",
    )
    parser.add_argument(
        "--mic-noise-floor",
        type=_non_negative_float,
        default=AudioSmoothing().noise_floor,
        help="Noise floor threshold for mic mode calibration",
    )
    parser.add_argument(
        "--idle-enter-frames",
        type=_positive_int,
        default=MusicSelectorConfig().idle_enter_frames,
        help="Consecutive low-activity frames before idle activates in mic mode",
    )
    parser.add_argument(
        "--idle-threshold-scale",
        type=_positive_float,
        default=DEFAULT_IDLE_THRESHOLD_SCALE,
        help="Scale factor applied to mic idle activity thresholds",
    )
    parser.add_argument("--quiet", action="store_true", help="Disable runtime status output")
    parser.add_argument("--chip", default="/dev/gpiochip0", help="Primary GPIO chip path")
    parser.add_argument("--data-pin", type=int, default=14, help="Primary stripe data pin")
    parser.add_argument("--clock-pin", type=int, default=15, help="Primary stripe clock pin")
    parser.add_argument("--chip-2", help="Secondary GPIO chip path")
    parser.add_argument("--data-pin-2", type=int, help="Secondary stripe data pin")
    parser.add_argument("--clock-pin-2", type=int, help="Secondary stripe clock pin")
    parser.add_argument("--encoder-chip", help="GPIO chip path for encoder inputs")
    parser.add_argument("--encoder1-a", type=int, help="Encoder 1 A phase input pin")
    parser.add_argument("--encoder1-b", type=int, help="Encoder 1 B phase input pin")
    parser.add_argument("--encoder1-button", type=int, help="Encoder 1 button input pin")
    parser.add_argument("--encoder2-a", type=int, help="Encoder 2 A phase input pin")
    parser.add_argument("--encoder2-b", type=int, help="Encoder 2 B phase input pin")
    parser.add_argument("--encoder2-button", type=int, help="Encoder 2 button input pin")
    return parser


def build_output_controller(args: argparse.Namespace) -> Controller:
    _ensure_gpio_ready(args.chip)
    primary = GPIOStripe(
        Config(chip=args.chip, gpio_data=args.data_pin, gpio_clock=args.clock_pin, consumer="lumistripe"),
        args.pixels,
    )
    if not _secondary_stripe_requested(args):
        return primary
    if args.data_pin_2 is None or args.clock_pin_2 is None:
        raise ValueError("secondary stripe requires both --data-pin-2 and --clock-pin-2")
    secondary_chip = args.chip_2 or args.chip
    _ensure_gpio_ready(secondary_chip)
    secondary = GPIOStripe(
        Config(
            chip=secondary_chip,
            gpio_data=args.data_pin_2,
            gpio_clock=args.clock_pin_2,
            consumer="lumistripe_2",
        ),
        args.pixels,
    )
    return MultiController([primary, secondary])


def gpio_backend_label(controller: Controller) -> str | None:
    if isinstance(controller, MultiController):
        labels = [gpio_backend_label(child) for child in controller.controllers]
        compact = [label for label in labels if label]
        return ", ".join(compact) if compact else None
    label = getattr(controller, "gpio_backend_label", None)
    return str(label) if label else None


def build_runtime_encoder_backend(args: argparse.Namespace) -> EncoderBackend:
    encoder1 = _encoder_pins_from_args(args, "encoder1")
    encoder2 = _encoder_pins_from_args(args, "encoder2")
    if encoder1 is None and encoder2 is None:
        return NullEncoderBackend()
    encoder_chip = args.encoder_chip or args.chip
    _ensure_gpio_input_ready(encoder_chip)
    return build_encoder_backend(
        encoder_chip,
        encoder1=encoder1,
        encoder2=encoder2,
    )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_audio_devices:
        for device in list_input_device_details():
            print(f"{device.index}: {device.name}")
        return
    try:
        if args.calibrate_audio is not None:
            result = calibrate_audio_input(duration=args.calibrate_audio, device_pattern=args.audio_device)
            print(_calibration_text(result))
            return
        calibration = None
        if args.auto_calibrate_audio is not None and (args.audio_debug or args.mode is RuntimeMode.MIC):
            calibration = calibrate_audio_input(duration=args.auto_calibrate_audio, device_pattern=args.audio_device)
            _apply_calibration(args, calibration)
        if args.audio_debug:
            app = HeadlessApp(
                controller=Stripe(args.pixels),
                pixel_count=args.pixels,
                mode=RuntimeMode.MIC,
                audio_device=args.audio_device,
                mic_target_level=args.mic_target_level,
                mic_noise_floor=args.mic_noise_floor,
                idle_enter_frames=args.idle_enter_frames,
                idle_threshold_scale=args.idle_threshold_scale,
                audio_debug_verbose=args.audio_debug_verbose,
                audio_calibration=calibration,
                animation_name=args.animation,
                quiet=True,
            )
            app.run_audio_debug()
            return
        controller = build_output_controller(args)
        encoder_backend = build_runtime_encoder_backend(args)
        HeadlessApp(
            controller=controller,
            pixel_count=args.pixels,
            mode=args.mode,
            audio_device=args.audio_device,
            mic_target_level=args.mic_target_level,
            mic_noise_floor=args.mic_noise_floor,
            idle_enter_frames=args.idle_enter_frames,
            idle_threshold_scale=args.idle_threshold_scale,
            audio_calibration=calibration,
            animation_name=args.animation,
            quiet=args.quiet,
            gpio_backend_label=gpio_backend_label(controller),
            encoder_backend=encoder_backend,
        ).run()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc


def _secondary_stripe_requested(args: argparse.Namespace) -> bool:
    return args.chip_2 is not None or args.data_pin_2 is not None or args.clock_pin_2 is not None


def _ensure_gpio_ready(chip: str) -> None:
    if importlib.util.find_spec("gpiod") is None:
        raise RuntimeError("GPIO runtime unavailable: install lumistripe-core[gpio] to use the headless CLI")
    chip_path = Path(chip)
    if not chip_path.exists():
        raise RuntimeError(f'GPIO chip "{chip}" was not found. Check that GPIO is enabled and the device path is correct.')
    if not chip_path.is_char_device():
        raise RuntimeError(f'GPIO chip "{chip}" is not a character device. Check the configured GPIO chip path.')
    if not os.access(chip_path, os.R_OK | os.W_OK):
        raise RuntimeError(
            f'permission denied for GPIO chip "{chip}". '
            "Add your user to the gpio group or run with appropriate permissions."
        )


def _ensure_gpio_input_ready(chip: str) -> None:
    if importlib.util.find_spec("gpiod") is None:
        raise RuntimeError("GPIO runtime unavailable: install lumistripe-core[gpio] to use the headless CLI")
    chip_path = Path(chip)
    if not chip_path.exists():
        raise RuntimeError(f'GPIO chip "{chip}" was not found. Check that GPIO is enabled and the device path is correct.')
    if not chip_path.is_char_device():
        raise RuntimeError(f'GPIO chip "{chip}" is not a character device. Check the configured GPIO chip path.')
    if not os.access(chip_path, os.R_OK):
        raise RuntimeError(
            f'permission denied for GPIO chip "{chip}". '
            "Add your user to the gpio group or run with appropriate permissions."
        )


def _parse_mode(value: str) -> RuntimeMode:
    try:
        return RuntimeMode(value.lower())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid mode: {value}") from exc


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0.0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _apply_calibration(args: argparse.Namespace, result: AudioCalibrationResult) -> None:
    args.mic_target_level = result.recommended_target_level
    args.mic_noise_floor = result.recommended_noise_floor
    args.idle_threshold_scale = result.recommended_idle_threshold_scale


def _calibration_text(result: AudioCalibrationResult) -> str:
    return "\n".join(
        [
            "Audio calibration complete.",
            f"  samples: {result.samples}",
            f"  measured floor: {result.measured_floor:0.4f}",
            f"  measured peak: {result.measured_peak:0.4f}",
            "Recommended flags:",
            f"  --mic-noise-floor {result.recommended_noise_floor:0.4f}",
            f"  --mic-target-level {result.recommended_target_level:0.3f}",
            f"  --idle-threshold-scale {result.recommended_idle_threshold_scale:0.2f}",
        ]
    )


def _encoder_pins_from_args(args: argparse.Namespace, prefix: str) -> EncoderPins | None:
    a = getattr(args, f"{prefix}_a")
    b = getattr(args, f"{prefix}_b")
    button = getattr(args, f"{prefix}_button")
    if a is None and b is None and button is None:
        return None
    if a is None or b is None or button is None:
        raise ValueError(f"{prefix} requires all of --{prefix}-a, --{prefix}-b, and --{prefix}-button")
    return EncoderPins(a=a, b=b, button=button)


def _build_audio_config(*, target_level: float, noise_floor: float) -> AudioConfig:
    return AudioConfig(
        smoothing=AudioSmoothing(noise_floor=noise_floor),
        normalization=AudioNormalization(target_level=target_level),
    )


def _build_selector_config(*, idle_enter_frames: int, idle_threshold_scale: float) -> MusicSelectorConfig:
    defaults = MusicSelectorConfig()
    return MusicSelectorConfig(
        class_dwell_frames=defaults.class_dwell_frames,
        animation_dwell_frames=defaults.animation_dwell_frames,
        confidence_threshold=defaults.confidence_threshold,
        feature_attack=defaults.feature_attack,
        feature_release=defaults.feature_release,
        idle_enter_frames=idle_enter_frames,
        idle_energy_threshold=defaults.idle_energy_threshold * idle_threshold_scale,
        idle_onset_threshold=defaults.idle_onset_threshold * idle_threshold_scale,
        idle_beat_density_threshold=defaults.idle_beat_density_threshold * idle_threshold_scale,
        idle_brightness_threshold=defaults.idle_brightness_threshold * idle_threshold_scale,
    )
