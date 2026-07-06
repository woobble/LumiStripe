from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lumistripe import (
    AnimationClass,
    AnimationPlayer,
    AudioConfig,
    AudioCalibrationResult,
    AudioFrame,
    AudioInput,
    AudioNormalization,
    AudioSmoothing,
    CLASS_MAP,
    MusicFeatures,
    MusicDrivenSelector,
    MusicSelectorConfig,
    Stripe,
    calibrate_audio_input,
    list_input_device_details,
)

SUB_BAR_W = 8
SUB_GAP = 2
GROUP_GAP = 4
BAR_H = 120
PAD = 48
HEADER_H = 460
BUTTON_H = 64
BUTTON_GAP = 20
BUTTON_Y = 32
MODE_BUTTON_W = 148
MIN_FRAME_SECONDS = 0.016
BUTTON_FONT_SIZE = -22
HEADER_FONT_SIZE = -16
DETAIL_FONT_SIZE = -13
DEFAULT_IDLE_THRESHOLD_SCALE = 1.0

BACKGROUND_COLOR = (26, 26, 26)
HEADER_COLOR = (35, 35, 35)
BUTTON_FILL = (54, 54, 54)
BUTTON_BORDER = (122, 122, 122)
BUTTON_ACTIVE_FILL = (60, 90, 42)
BUTTON_ACTIVE_BORDER = (134, 210, 106)
TEXT_COLOR = (241, 241, 241)
ACCENT_COLOR = (180, 212, 255)
ERROR_COLOR = (255, 170, 170)


class SimulatorMode(str, Enum):
    MANUAL = "manual"
    DEMO = "demo"
    MIC = "mic"

    @property
    def label(self) -> str:
        return self.value.upper()


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


@dataclass(frozen=True, slots=True)
class Controls:
    prev: Rect
    next: Rect
    manual: Rect
    demo: Rect
    mic: Rect
    auto_sel: Rect
    calibrate: Rect


def pixel_pitch() -> int:
    return 3 * SUB_BAR_W + 2 * SUB_GAP + GROUP_GAP


def window_size(count: int) -> tuple[int, int]:
    return count * pixel_pitch() + 2 * PAD, HEADER_H + BAR_H + 2 * PAD


def layout_controls() -> Controls:
    prev = Rect(x=PAD, y=BUTTON_Y, w=184, h=BUTTON_H)
    next_rect = Rect(x=prev.x + prev.w + BUTTON_GAP, y=BUTTON_Y, w=184, h=BUTTON_H)
    mode_y = BUTTON_Y + BUTTON_H + 20
    manual = Rect(x=PAD, y=mode_y, w=MODE_BUTTON_W, h=BUTTON_H)
    demo = Rect(x=manual.x + manual.w + BUTTON_GAP, y=mode_y, w=MODE_BUTTON_W, h=BUTTON_H)
    mic = Rect(x=demo.x + demo.w + BUTTON_GAP, y=mode_y, w=MODE_BUTTON_W, h=BUTTON_H)
    auto_sel = Rect(x=mic.x + mic.w + BUTTON_GAP, y=mode_y, w=148, h=BUTTON_H)
    calibrate = Rect(x=auto_sel.x + auto_sel.w + BUTTON_GAP, y=mode_y, w=148, h=BUTTON_H)
    return Controls(prev=prev, next=next_rect, manual=manual, demo=demo, mic=mic, auto_sel=auto_sel, calibrate=calibrate)


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
    return AudioFrame(rms=rms, bands=bands, beat=beat, beat_strength=beat_strength)


@dataclass(slots=True)
class SimulatorApp:
    pixel_count: int = 80
    mode: SimulatorMode = SimulatorMode.MANUAL
    audio_device: str | None = None
    mic_target_level: float = field(default_factory=lambda: AudioNormalization().target_level)
    mic_noise_floor: float = field(default_factory=lambda: AudioSmoothing().noise_floor)
    idle_enter_frames: int = field(default_factory=lambda: MusicSelectorConfig().idle_enter_frames)
    idle_threshold_scale: float = DEFAULT_IDLE_THRESHOLD_SCALE
    auto_calibrate_audio: float | None = None
    player: AnimationPlayer = field(init=False)
    stripe: Stripe = field(init=False)
    controls: Controls = field(init=False)
    running: bool = field(init=False, default=True)
    audio_input: AudioInput | None = field(init=False, default=None)
    audio_status: str = field(init=False, default="No audio source active.")
    audio_error: str | None = field(init=False, default=None)
    audio_frame: AudioFrame = field(init=False, default_factory=AudioFrame)
    music_features: MusicFeatures = field(init=False, default_factory=MusicFeatures)
    audio_calibration: AudioCalibrationResult | None = field(init=False, default=None)
    demo_tick: int = field(init=False, default=0)
    selector: MusicDrivenSelector | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.player = AnimationPlayer.party()
        self.stripe = Stripe(self.pixel_count)
        self.controls = layout_controls()
        self.running = True
        calibration_error: str | None = None
        if self.auto_calibrate_audio is not None:
            try:
                self._apply_calibration_result(
                    calibrate_audio_input(duration=self.auto_calibrate_audio, device_pattern=self.audio_device)
                )
            except RuntimeError as exc:
                calibration_error = str(exc)
        self.set_mode(self.mode)
        if calibration_error is not None:
            self.audio_error = calibration_error

    @property
    def animation_name(self) -> str:
        raw = self.player.name_at(self.player.current_index()) or "?"
        classes = CLASS_MAP.get(raw, ())
        label = raw.upper()
        if classes:
            tags = ", ".join(c.value.upper() for c in classes)
            label = f"{label}  [{tags}]"
        return label

    @property
    def mode_label(self) -> str:
        return self.mode.label

    @property
    def class_label(self) -> str:
        if self.selector is None:
            return "-"
        if self.selector.idle_active:
            return "IDLE"
        return self.selector.current_class.value.upper()

    @property
    def mic_tuning_label(self) -> str:
        return (
            f"MIC: target={self.mic_target_level:0.2f} "
            f"noise={self.mic_noise_floor:0.3f} "
            f"idle={self.idle_enter_frames}f "
            f"scale={self.idle_threshold_scale:0.2f}"
            f"{' calibrated=' + str(self.audio_calibration.samples) + 'f' if self.audio_calibration else ''}"
        )

    def handle_key(self, key: str) -> None:
        match key:
            case "left":
                self.player.prev()
            case "right":
                self.player.next()
            case "m":
                self.set_mode(SimulatorMode.MANUAL)
            case "d":
                self.set_mode(SimulatorMode.DEMO)
            case "a":
                self.set_mode(SimulatorMode.MIC)
            case "s":
                self._toggle_auto_select()
            case "c":
                self.calibrate_audio()
            case "escape":
                self.running = False

    def _toggle_auto_select(self) -> None:
        if self.selector is not None:
            self.selector.set_auto_select(not self.selector.auto_select)

    def handle_click(self, x: int, y: int) -> None:
        if self.controls.prev.contains(x, y):
            self.player.prev()
        elif self.controls.next.contains(x, y):
            self.player.next()
        elif self.controls.manual.contains(x, y):
            self.set_mode(SimulatorMode.MANUAL)
        elif self.controls.demo.contains(x, y):
            self.set_mode(SimulatorMode.DEMO)
        elif self.controls.mic.contains(x, y):
            self.set_mode(SimulatorMode.MIC)
        elif self.controls.auto_sel.contains(x, y):
            self._toggle_auto_select()
        elif self.controls.calibrate.contains(x, y):
            self.calibrate_audio()

    def calibrate_audio(self, duration: float = 3.0) -> AudioCalibrationResult | None:
        try:
            result = calibrate_audio_input(duration=duration, device_pattern=self.audio_device)
        except RuntimeError as exc:
            self.audio_error = str(exc)
            return None
        self._apply_calibration_result(result)
        if self.mode is SimulatorMode.MIC:
            self.set_mode(SimulatorMode.MIC)
        return result

    def _apply_calibration_result(self, result: AudioCalibrationResult) -> None:
        self.audio_calibration = result
        self.mic_noise_floor = result.recommended_noise_floor
        self.mic_target_level = result.recommended_target_level
        self.idle_threshold_scale = result.recommended_idle_threshold_scale

    def set_mode(self, mode: SimulatorMode) -> None:
        self._close_audio_input()
        self.player.clear_audio_snapshot()
        self.player.audio_enabled = True
        self.mode = mode
        self.audio_error = None
        self.audio_frame = AudioFrame()
        self.selector = None

        if mode is SimulatorMode.MANUAL:
            self.audio_status = "No audio source active."
            return

        if mode is SimulatorMode.DEMO:
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
            self.mode = SimulatorMode.MANUAL
            self.audio_error = str(exc)
            self.audio_status = "Microphone unavailable."
            return

        self.audio_status = f"Input: {self.audio_input.device_name()}"
        current_name = self.player.name_at(self.player.current_index())
        current_classes = CLASS_MAP.get(current_name or "", ())
        self.selector = MusicDrivenSelector(
            config=_build_selector_config(
                idle_enter_frames=self.idle_enter_frames,
                idle_threshold_scale=self.idle_threshold_scale,
            ),
            current_class=current_classes[0] if current_classes else AnimationClass.GROOVY,
        )
        self.selector.set_auto_select(False)
        self.player.set_audio_snapshot(self._mic_snapshot)

    def _demo_snapshot(self) -> AudioFrame:
        frame = demo_frame(self.demo_tick)
        self.demo_tick += 1
        self.audio_frame = frame
        return frame

    def _mic_snapshot(self) -> AudioFrame:
        return self.audio_frame

    def _close_audio_input(self) -> None:
        if self.audio_input is None:
            return
        self.audio_input.close()
        self.audio_input = None

    def step(self) -> float:
        if self.mode is SimulatorMode.MIC and self.audio_input is not None:
            self.audio_frame = self.audio_input.read()
            self.music_features = self.audio_input.read_features()
            if self.selector is not None:
                self.selector.update(self.player, self.music_features)
                self.player.audio_enabled = not self.selector.idle_active
        delay = max(self.player.step(self.stripe), MIN_FRAME_SECONDS)
        if self.mode is SimulatorMode.MANUAL:
            self.audio_frame = AudioFrame()
            self.music_features = MusicFeatures()
        return delay

    def run(self) -> None:
        tkinter = _load_tkinter()
        tkfont = _load_tkfont()
        width, height = window_size(self.pixel_count)
        root = tkinter.Tk()
        root.title(f"LED Simulator - {self.pixel_count} pixels")
        root.configure(bg=_hex(BACKGROUND_COLOR))
        root.resizable(False, False)
        root.tk.call("tk", "scaling", 2.0)

        button_font = tkfont.Font(root=root, family="DejaVu Sans", size=BUTTON_FONT_SIZE, weight="bold")
        header_font = tkfont.Font(root=root, family="DejaVu Sans", size=HEADER_FONT_SIZE, weight="bold")
        detail_font = tkfont.Font(root=root, family="DejaVu Sans Mono", size=DETAIL_FONT_SIZE)

        header = tkinter.Frame(root, bg=_hex(HEADER_COLOR), height=HEADER_H)
        header.pack(fill="x")
        header.pack_propagate(False)

        controls_frame = tkinter.Frame(header, bg=_hex(HEADER_COLOR))
        controls_frame.place(x=PAD, y=BUTTON_Y)

        prev_button = self._make_button(
            tkinter,
            controls_frame,
            "PREV",
            self.player.prev,
            button_font,
        )
        prev_button.pack(side="left")

        next_button = self._make_button(
            tkinter,
            controls_frame,
            "NEXT",
            self.player.next,
            button_font,
        )
        next_button.pack(side="left", padx=(BUTTON_GAP, 0))

        mode_buttons_frame = tkinter.Frame(header, bg=_hex(HEADER_COLOR))
        mode_buttons_frame.place(x=PAD, y=self.controls.manual.y)

        manual_button = self._make_button(
            tkinter,
            mode_buttons_frame,
            "MANUAL",
            lambda: self.set_mode(SimulatorMode.MANUAL),
            button_font,
        )
        manual_button.pack(side="left")

        demo_button = self._make_button(
            tkinter,
            mode_buttons_frame,
            "DEMO",
            lambda: self.set_mode(SimulatorMode.DEMO),
            button_font,
        )
        demo_button.pack(side="left", padx=(BUTTON_GAP, 0))

        mic_button = self._make_button(
            tkinter,
            mode_buttons_frame,
            "MIC",
            lambda: self.set_mode(SimulatorMode.MIC),
            button_font,
        )
        mic_button.pack(side="left", padx=(BUTTON_GAP, 0))

        auto_sel_button = self._make_button(
            tkinter,
            mode_buttons_frame,
            "AUTO",
            self._toggle_auto_select,
            button_font,
        )
        auto_sel_button.pack(side="left", padx=(BUTTON_GAP, 0))

        calibrate_button = self._make_button(
            tkinter,
            mode_buttons_frame,
            "CAL",
            self.calibrate_audio,
            button_font,
        )
        calibrate_button.pack(side="left", padx=(BUTTON_GAP, 0))

        animation_label = tkinter.Label(
            header,
            text="",
            font=header_font,
            fg=_hex(TEXT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
        )
        animation_label.place(x=PAD, y=self.controls.manual.y + BUTTON_H + 34)

        mode_label = tkinter.Label(
            header,
            text="",
            font=header_font,
            fg=_hex(ACCENT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
        )
        mode_label.place(x=PAD, y=self.controls.manual.y + BUTTON_H + 88)

        source_label = tkinter.Label(
            header,
            text="",
            font=detail_font,
            fg=_hex(TEXT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
            justify="left",
        )
        source_label.place(x=PAD, y=self.controls.manual.y + BUTTON_H + 148)

        family_label = tkinter.Label(
            header,
            text="",
            font=detail_font,
            fg=_hex(ACCENT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
            justify="left",
        )
        family_label.place(x=PAD, y=self.controls.manual.y + BUTTON_H + 176)

        analysis_label = tkinter.Label(
            header,
            text="",
            font=detail_font,
            fg=_hex(TEXT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
            justify="left",
        )
        analysis_label.place(x=PAD, y=self.controls.manual.y + BUTTON_H + 232)

        error_label = tkinter.Label(
            header,
            text="",
            font=detail_font,
            fg=_hex(ERROR_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
            justify="left",
        )
        error_label.place(x=PAD, y=self.controls.manual.y + BUTTON_H + 350)

        canvas = tkinter.Canvas(
            root,
            width=width,
            height=height - HEADER_H,
            bg=_hex(BACKGROUND_COLOR),
            highlightthickness=0,
        )
        canvas.pack(fill="both", expand=False)

        root.bind("<Left>", lambda _event: self.handle_key("left"))
        root.bind("<Right>", lambda _event: self.handle_key("right"))
        root.bind("<Escape>", lambda _event: self.handle_key("escape"))
        root.bind("m", lambda _event: self.handle_key("m"))
        root.bind("d", lambda _event: self.handle_key("d"))
        root.bind("a", lambda _event: self.handle_key("a"))
        root.bind("s", lambda _event: self.handle_key("s"))
        root.bind("c", lambda _event: self.handle_key("c"))
        root.bind("<Button-1>", lambda event: self.handle_click(event.x, event.y))
        root.protocol("WM_DELETE_WINDOW", lambda: self.handle_key("escape"))

        next_frame_at = time.monotonic()

        def refresh_mode_buttons() -> None:
            self._style_mode_button(manual_button, self.mode is SimulatorMode.MANUAL)
            self._style_mode_button(demo_button, self.mode is SimulatorMode.DEMO)
            self._style_mode_button(mic_button, self.mode is SimulatorMode.MIC)

        def refresh_auto_sel_button() -> None:
            active = self.selector is not None and self.selector.auto_select
            self._style_mode_button(auto_sel_button, active)

        def tick() -> None:
            nonlocal next_frame_at
            if not self.running:
                self.stripe.clear()
                self.stripe.force_flush()
                self._close_audio_input()
                root.destroy()
                return

            now = time.monotonic()
            if now >= next_frame_at:
                next_frame_at = now + self.step()

            auto_status = "AUTO: ON" if self.selector is not None and self.selector.auto_select else "AUTO: OFF"
            animation_label.configure(text=f"ANIM: {self.animation_name}")
            mode_label.configure(text=f"MODE: {self.mode_label}  |  {auto_status}")
            source_text = f"SOURCE: {self.audio_status}"
            if self.mode is SimulatorMode.MIC:
                source_text = f"{source_text}\n{self.mic_tuning_label}"
            source_label.configure(text=source_text)
            family_label.configure(text=f"CLASS: {self.class_label}")
            analysis_label.configure(text=self.analysis_text())
            error_label.configure(text=f"ERROR: {self.audio_error}" if self.audio_error else "")
            refresh_mode_buttons()
            refresh_auto_sel_button()
            self.render(canvas, width, height - HEADER_H)
            root.after(8, tick)

        tick()
        try:
            root.mainloop()
        except KeyboardInterrupt:
            self.running = False
            self.stripe.clear()
            self.stripe.force_flush()
            self._close_audio_input()
            try:
                root.destroy()
            except tkinter.TclError:
                pass

    def analysis_text(self) -> str:
        frame = self.audio_frame
        feat = self.music_features
        beat = "YES" if frame.beat else "NO"
        silence = "YES" if feat.silence else "NO"
        drop = "YES" if feat.drop_detected else "NO"
        section = "YES" if feat.section_change else "NO"
        bands = " ".join(f"{value:0.2f}" for value in frame.bands)
        return (
            f"RMS: {frame.rms:0.3f}    BEAT: {beat}    BPM: {feat.bpm:3.0f}\n"
            f"BRIGHT: {feat.brightness:0.2f}    ONSET: {feat.onset_strength:0.3f}    DYN: {feat.dynamic_range:0.3f}\n"
            f"LOUD: {feat.rolling_loudness:0.2f}    FLUX: {feat.spectral_flux:0.2f}    SILENCE: {silence}    DROP: {drop}    SECTION: {section}\n"
            f"BANDS: {bands}"
        )

    def render(self, canvas: Any, width: int, height: int) -> None:
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill=_hex(BACKGROUND_COLOR), outline="")

        bar_height = min(BAR_H, height - PAD * 2)
        for index, pixel in enumerate(self.stripe.pixels()):
            alpha = pixel[3] / 255.0
            color = (
                int(pixel[0] * alpha),
                int(pixel[1] * alpha),
                int(pixel[2] * alpha),
            )
            base_x = PAD + index * pixel_pitch()
            for sub in range(3):
                sx = base_x + sub * (SUB_BAR_W + SUB_GAP)
                canvas.create_rectangle(
                    sx,
                    PAD,
                    sx + SUB_BAR_W,
                    PAD + bar_height,
                    fill=_hex(color),
                    outline="",
                )

    def _make_button(
        self,
        tkinter: Any,
        parent: Any,
        text: str,
        command: Any,
        font: Any,
    ) -> Any:
        return tkinter.Button(
            parent,
            text=text,
            command=command,
            font=font,
            fg=_hex(TEXT_COLOR),
            bg=_hex(BUTTON_FILL),
            activeforeground=_hex(TEXT_COLOR),
            activebackground=_hex(BUTTON_FILL),
            relief="flat",
            bd=1,
            highlightthickness=1,
            highlightbackground=_hex(BUTTON_BORDER),
            highlightcolor=_hex(BUTTON_BORDER),
            padx=20,
            pady=8,
        )

    def _style_mode_button(self, button: Any, active: bool) -> None:
        if active:
            button.configure(
                bg=_hex(BUTTON_ACTIVE_FILL),
                activebackground=_hex(BUTTON_ACTIVE_FILL),
                highlightbackground=_hex(BUTTON_ACTIVE_BORDER),
                highlightcolor=_hex(BUTTON_ACTIVE_BORDER),
            )
        else:
            button.configure(
                bg=_hex(BUTTON_FILL),
                activebackground=_hex(BUTTON_FILL),
                highlightbackground=_hex(BUTTON_BORDER),
                highlightcolor=_hex(BUTTON_BORDER),
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lumistripe-sim")
    parser.add_argument("--pixels", type=int, default=80, help="Number of simulated LEDs")
    parser.add_argument(
        "--mode",
        type=_parse_mode,
        default=SimulatorMode.MANUAL,
        help="Startup mode: manual, demo, or mic",
    )
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
    parser.add_argument(
        "--auto-calibrate-audio",
        type=_positive_float,
        metavar="SECONDS",
        help="Measure the selected audio input and apply recommended mic tuning before startup",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_audio_devices:
        for device in list_input_device_details():
            print(f"{device.index}: {device.name}")
        return
    SimulatorApp(
        pixel_count=args.pixels,
        mode=args.mode,
        audio_device=args.audio_device,
        mic_target_level=args.mic_target_level,
        mic_noise_floor=args.mic_noise_floor,
        idle_enter_frames=args.idle_enter_frames,
        idle_threshold_scale=args.idle_threshold_scale,
        auto_calibrate_audio=args.auto_calibrate_audio,
    ).run()


def _parse_mode(value: str) -> SimulatorMode:
    try:
        return SimulatorMode(value.lower())
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


def _load_tkinter() -> Any:
    try:
        import tkinter
    except ImportError as exc:
        raise RuntimeError("tkinter is required for the simulator") from exc
    return tkinter


def _load_tkfont() -> Any:
    try:
        import tkinter.font
    except ImportError as exc:
        raise RuntimeError("tkinter font support is required for the simulator") from exc
    return tkinter.font


def _hex(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)
