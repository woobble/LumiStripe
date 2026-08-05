from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from lumistripe import (
    AnimationPlayer,
    AudioAnalysis,
    AudioCalibrationResult,
    AudioConfig,
    AudioFrame,
    AudioInput,
    AudioNormalization,
    AudioSmoothing,
    AudioSnapshot,
    AudioSource,
    CycleOrder,
    CycleTiming,
    CyclingConfig,
    DynamicSelectorConfig,
    MusicActivityConfig,
    MusicFeatures,
    PlaybackConfig,
    PlaybackEngine,
    PlaybackMode,
    Stripe,
    calibrate_audio_input,
    demo_snapshot,
    list_input_device_details,
    load_mic_profile,
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
    static: Rect
    cycling: Rect
    dynamic: Rect
    calibrate: Rect


def pixel_pitch() -> int:
    return 3 * SUB_BAR_W + 2 * SUB_GAP + GROUP_GAP


def window_size(count: int) -> tuple[int, int]:
    return count * pixel_pitch() + 2 * PAD, HEADER_H + BAR_H + 2 * PAD


def layout_controls() -> Controls:
    prev = Rect(x=PAD, y=BUTTON_Y, w=184, h=BUTTON_H)
    next_rect = Rect(x=prev.x + prev.w + BUTTON_GAP, y=BUTTON_Y, w=184, h=BUTTON_H)
    mode_y = BUTTON_Y + BUTTON_H + 20
    static = Rect(x=PAD, y=mode_y, w=MODE_BUTTON_W, h=BUTTON_H)
    cycling = Rect(x=static.x + static.w + BUTTON_GAP, y=mode_y, w=MODE_BUTTON_W, h=BUTTON_H)
    dynamic = Rect(x=cycling.x + cycling.w + BUTTON_GAP, y=mode_y, w=MODE_BUTTON_W, h=BUTTON_H)
    calibrate = Rect(x=dynamic.x + dynamic.w + BUTTON_GAP, y=mode_y, w=148, h=BUTTON_H)
    return Controls(prev=prev, next=next_rect, static=static, cycling=cycling, dynamic=dynamic, calibrate=calibrate)


@dataclass(slots=True)
class SimulatorApp:
    pixel_count: int = 80
    mode: PlaybackMode = PlaybackMode.STATIC
    audio_source: AudioSource | None = None
    audio_device: str | None = None
    mic_target_level: float = field(default_factory=lambda: AudioNormalization().target_level)
    mic_noise_floor: float = field(default_factory=lambda: AudioSmoothing().noise_floor)
    audio_analysis: AudioAnalysis = field(default_factory=AudioAnalysis)
    idle_enter_frames: int = field(default_factory=lambda: MusicActivityConfig().idle_enter_frames)
    idle_threshold_scale: float = DEFAULT_IDLE_THRESHOLD_SCALE
    music_activation_delay: float = field(
        default_factory=lambda: MusicActivityConfig().activation_delay_s
    )
    auto_calibrate_audio: float | None = None
    cycling_config: CyclingConfig = field(default_factory=CyclingConfig)
    dynamic_selector_config: DynamicSelectorConfig = field(default_factory=DynamicSelectorConfig)
    player: AnimationPlayer = field(init=False)
    playback: PlaybackEngine = field(init=False)
    stripe: Stripe = field(init=False)
    controls: Controls = field(init=False)
    running: bool = field(init=False, default=True)
    audio_input: AudioInput | None = field(init=False, default=None)
    audio_status: str = field(init=False, default="No audio source active.")
    audio_error: str | None = field(init=False, default=None)
    audio_frame: AudioFrame = field(init=False, default_factory=AudioFrame)
    music_features: MusicFeatures = field(init=False, default_factory=MusicFeatures)
    audio_snapshot: AudioSnapshot = field(init=False, default_factory=AudioSnapshot.silence)
    audio_calibration: AudioCalibrationResult | None = field(init=False, default=None)
    demo_tick: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.player = AnimationPlayer.party()
        self.playback = PlaybackEngine(
            self.player,
            PlaybackConfig(
                mode=self.mode,
                cycling=self.cycling_config,
                dynamic=self.dynamic_selector_config,
                activity=_build_activity_config(
                    idle_enter_frames=self.idle_enter_frames,
                    idle_threshold_scale=self.idle_threshold_scale,
                    activation_delay_s=self.music_activation_delay,
                ),
            ),
        )
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
        metadata = self.player.animations[self.player.current_index()].animation.metadata
        return f"{raw.upper()}  [{metadata.mood.upper()}]"

    @property
    def mode_label(self) -> str:
        return self.mode.value.upper()

    @property
    def class_label(self) -> str:
        if self.mode is not PlaybackMode.DYNAMIC:
            return "-"
        return "MUSIC" if self.playback.music_active else "CALM"

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
                self.playback.previous_animation()
                self.mode = self.playback.mode
            case "right":
                self.playback.next_animation()
                self.mode = self.playback.mode
            case "s":
                self.set_mode(PlaybackMode.STATIC)
            case "c":
                self.set_mode(PlaybackMode.CYCLING)
            case "d":
                self.set_mode(PlaybackMode.DYNAMIC)
            case "k":
                self.calibrate_audio()
            case "escape":
                self.running = False

    def handle_click(self, x: int, y: int) -> None:
        if self.controls.prev.contains(x, y):
            self.playback.previous_animation()
            self.mode = self.playback.mode
        elif self.controls.next.contains(x, y):
            self.playback.next_animation()
            self.mode = self.playback.mode
        elif self.controls.static.contains(x, y):
            self.set_mode(PlaybackMode.STATIC)
        elif self.controls.cycling.contains(x, y):
            self.set_mode(PlaybackMode.CYCLING)
        elif self.controls.dynamic.contains(x, y):
            self.set_mode(PlaybackMode.DYNAMIC)
        elif self.controls.calibrate.contains(x, y):
            self.calibrate_audio()

    def calibrate_audio(self, duration: float = 3.0) -> AudioCalibrationResult | None:
        try:
            result = calibrate_audio_input(duration=duration, device_pattern=self.audio_device)
        except RuntimeError as exc:
            self.audio_error = str(exc)
            return None
        self._apply_calibration_result(result)
        source = self.audio_source or (AudioSource.MIC if self.mode is PlaybackMode.DYNAMIC else AudioSource.OFF)
        if source is AudioSource.MIC:
            self.set_mode(self.mode)
        return result

    def _apply_calibration_result(self, result: AudioCalibrationResult) -> None:
        self.audio_calibration = result
        self.mic_noise_floor = result.recommended_noise_floor
        self.mic_target_level = result.recommended_target_level
        self.idle_threshold_scale = result.recommended_idle_threshold_scale

    def set_mode(self, mode: PlaybackMode) -> None:
        self._close_audio_input()
        self.player.clear_audio_snapshot()
        self.player.audio_enabled = True
        self.mode = mode
        self.playback.set_mode(mode)
        self.audio_error = None
        self.audio_frame = AudioFrame()
        self.audio_snapshot = AudioSnapshot.silence()
        source = self.audio_source or (AudioSource.MIC if mode is PlaybackMode.DYNAMIC else AudioSource.OFF)
        if mode is PlaybackMode.DYNAMIC and source is AudioSource.OFF:
            raise ValueError("dynamic mode requires mic or demo audio")
        if source is AudioSource.OFF:
            self.audio_status = "No audio source active."
            return
        if source is AudioSource.DEMO:
            self.demo_tick = 0
            self.audio_status = "Using internal demo beat."
            return

        try:
            audio_config = _build_audio_config(
                target_level=self.mic_target_level,
                noise_floor=self.mic_noise_floor,
                analysis=self.audio_analysis,
            )
            self.audio_input = (
                AudioInput.with_device_config(self.audio_device, audio_config)
                if self.audio_device
                else AudioInput.with_config(audio_config)
            )
        except RuntimeError as exc:
            self.mode = PlaybackMode.STATIC
            self.playback.set_mode(PlaybackMode.STATIC)
            self.audio_error = str(exc)
            self.audio_status = "Microphone unavailable."
            return

        self.audio_status = f"Input: {self.audio_input.device_name()}"

    def _close_audio_input(self) -> None:
        if self.audio_input is None:
            return
        self.audio_input.close()
        self.audio_input = None

    def step(self) -> float:
        source = self.audio_source or (AudioSource.MIC if self.mode is PlaybackMode.DYNAMIC else AudioSource.OFF)
        if source is AudioSource.MIC and self.audio_input is not None:
            self.audio_frame = self.audio_input.read()
            self.music_features = self.audio_input.read_features() if self.audio_frame.fresh else MusicFeatures(silence=True)
            self.audio_snapshot = (
                AudioSnapshot.from_parts(self.audio_frame, self.music_features)
                if self.audio_frame.fresh
                else AudioSnapshot.silence(frame=self.audio_frame)
            )
        elif source is AudioSource.DEMO:
            self.audio_snapshot = demo_snapshot(self.demo_tick)
            self.demo_tick += 1
            self.audio_frame = self.audio_snapshot.frame
            self.music_features = self.audio_snapshot.features
        delay = max(self.playback.step(self.stripe, snapshot=None if source is AudioSource.OFF else self.audio_snapshot), MIN_FRAME_SECONDS)
        if source is AudioSource.OFF:
            self.audio_frame = AudioFrame()
            self.music_features = MusicFeatures()
            self.audio_snapshot = AudioSnapshot.silence()
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
            lambda: self.handle_key("left"),
            button_font,
        )
        prev_button.pack(side="left")

        next_button = self._make_button(
            tkinter,
            controls_frame,
            "NEXT",
            lambda: self.handle_key("right"),
            button_font,
        )
        next_button.pack(side="left", padx=(BUTTON_GAP, 0))

        mode_buttons_frame = tkinter.Frame(header, bg=_hex(HEADER_COLOR))
        mode_buttons_frame.place(x=PAD, y=self.controls.static.y)

        static_button = self._make_button(
            tkinter,
            mode_buttons_frame,
            "STATIC",
            lambda: self.set_mode(PlaybackMode.STATIC),
            button_font,
        )
        static_button.pack(side="left")

        cycling_button = self._make_button(
            tkinter,
            mode_buttons_frame,
            "CYCLING",
            lambda: self.set_mode(PlaybackMode.CYCLING),
            button_font,
        )
        cycling_button.pack(side="left", padx=(BUTTON_GAP, 0))

        dynamic_button = self._make_button(
            tkinter,
            mode_buttons_frame,
            "DYNAMIC",
            lambda: self.set_mode(PlaybackMode.DYNAMIC),
            button_font,
        )
        dynamic_button.pack(side="left", padx=(BUTTON_GAP, 0))

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
        animation_label.place(x=PAD, y=self.controls.static.y + BUTTON_H + 34)

        mode_label = tkinter.Label(
            header,
            text="",
            font=header_font,
            fg=_hex(ACCENT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
        )
        mode_label.place(x=PAD, y=self.controls.static.y + BUTTON_H + 88)

        source_label = tkinter.Label(
            header,
            text="",
            font=detail_font,
            fg=_hex(TEXT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
            justify="left",
        )
        source_label.place(x=PAD, y=self.controls.static.y + BUTTON_H + 148)

        family_label = tkinter.Label(
            header,
            text="",
            font=detail_font,
            fg=_hex(ACCENT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
            justify="left",
        )
        family_label.place(x=PAD, y=self.controls.static.y + BUTTON_H + 176)

        analysis_label = tkinter.Label(
            header,
            text="",
            font=detail_font,
            fg=_hex(TEXT_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
            justify="left",
        )
        analysis_label.place(x=PAD, y=self.controls.static.y + BUTTON_H + 232)

        error_label = tkinter.Label(
            header,
            text="",
            font=detail_font,
            fg=_hex(ERROR_COLOR),
            bg=_hex(HEADER_COLOR),
            anchor="w",
            justify="left",
        )
        error_label.place(x=PAD, y=self.controls.static.y + BUTTON_H + 350)

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
        root.bind("d", lambda _event: self.handle_key("d"))
        root.bind("s", lambda _event: self.handle_key("s"))
        root.bind("c", lambda _event: self.handle_key("c"))
        root.bind("k", lambda _event: self.handle_key("k"))
        root.bind("<Button-1>", lambda event: self.handle_click(event.x, event.y))
        root.protocol("WM_DELETE_WINDOW", lambda: self.handle_key("escape"))

        next_frame_at = time.monotonic()

        def refresh_mode_buttons() -> None:
            self._style_mode_button(static_button, self.mode is PlaybackMode.STATIC)
            self._style_mode_button(cycling_button, self.mode is PlaybackMode.CYCLING)
            self._style_mode_button(dynamic_button, self.mode is PlaybackMode.DYNAMIC)

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

            animation_label.configure(text=f"ANIM: {self.animation_name}")
            mode_label.configure(text=f"MODE: {self.mode_label}")
            source_text = f"SOURCE: {self.audio_status}"
            source = self.audio_source or (AudioSource.MIC if self.mode is PlaybackMode.DYNAMIC else AudioSource.OFF)
            if source is AudioSource.MIC:
                source_text = f"{source_text}\n{self.mic_tuning_label}"
            source_label.configure(text=source_text)
            family_label.configure(text=f"CLASS: {self.class_label}")
            analysis_label.configure(text=self.analysis_text())
            error_label.configure(text=f"ERROR: {self.audio_error}" if self.audio_error else "")
            refresh_mode_buttons()
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
            f"{self._dynamic_summary_text()}"
        )

    def _dynamic_summary_text(self) -> str:
        decision = self.playback.last_decision
        if decision is None or not decision.scores:
            return ""
        top = ",".join(
            f"{score.name}:{score.score:0.2f}"
            for score in decision.scores[:3]
        )
        return f"\nDYNAMIC: {top} reason={decision.reason}"

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
        default=PlaybackMode.STATIC,
        help="Playback mode: static, cycling, or dynamic",
    )
    parser.add_argument(
        "--audio-source",
        type=_parse_audio_source,
        help="Audio source: off, mic, or demo (defaults to mic for dynamic and off otherwise)",
    )
    parser.add_argument("--cycle-order", type=CycleOrder, choices=tuple(CycleOrder), default=CycleOrder.SEQUENTIAL)
    parser.add_argument("--cycle-timing", type=CycleTiming, choices=tuple(CycleTiming), default=CycleTiming.PER_ANIMATION)
    parser.add_argument("--cycle-interval", type=_positive_float, default=30.0, metavar="SECONDS")
    parser.add_argument(
        "--audio-device",
        help="Input device index or substring match for the mic audio source",
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
        help="Normalized input level target for microphone calibration",
    )
    parser.add_argument(
        "--mic-noise-floor",
        type=_non_negative_float,
        default=AudioSmoothing().noise_floor,
        help="Noise floor threshold for microphone calibration",
    )
    parser.add_argument(
        "--mic-profile",
        help='Mic profile name, JSON path, or "auto" for device-name matching',
    )
    parser.add_argument(
        "--idle-enter-frames",
        type=_positive_int,
        default=MusicActivityConfig().idle_enter_frames,
        help="Consecutive non-music frames before Dynamic enters its calm state",
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
    parser.add_argument("--dynamic-min-duration", type=_positive_float, default=DynamicSelectorConfig().min_duration_s)
    parser.add_argument("--dynamic-max-duration", type=_positive_float, default=DynamicSelectorConfig().max_duration_s)
    parser.add_argument("--dynamic-switch-cooldown", type=_positive_float, default=DynamicSelectorConfig().switch_cooldown_s)
    parser.add_argument("--dynamic-drop-cooldown", type=_positive_float, default=DynamicSelectorConfig().drop_cooldown_s)
    parser.add_argument("--dynamic-randomness", type=_non_negative_float, default=DynamicSelectorConfig().randomness)
    parser.add_argument("--dynamic-history-size", type=_positive_int, default=DynamicSelectorConfig().history_size)
    parser.add_argument("--dynamic-seed", type=int)
    return parser


def main(argv: list[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    args.audio_analysis = AudioAnalysis()
    if args.list_audio_devices:
        for device in list_input_device_details():
            print(f"{device.index}: {device.name}")
        return
    if args.mic_profile is not None:
        _apply_mic_profile(args, raw_argv)
    SimulatorApp(
        pixel_count=args.pixels,
        mode=args.mode,
        audio_source=args.audio_source,
        audio_device=args.audio_device,
        mic_target_level=args.mic_target_level,
        mic_noise_floor=args.mic_noise_floor,
        audio_analysis=args.audio_analysis,
        idle_enter_frames=args.idle_enter_frames,
        idle_threshold_scale=args.idle_threshold_scale,
        auto_calibrate_audio=args.auto_calibrate_audio,
        cycling_config=_build_cycling_config(args),
        dynamic_selector_config=_build_dynamic_selector_config(args),
    ).run()


def _parse_mode(value: str) -> PlaybackMode:
    try:
        return PlaybackMode(value.lower())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid mode: {value}") from exc


def _parse_audio_source(value: str) -> AudioSource:
    try:
        return AudioSource(value.lower())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid audio source: {value}") from exc


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


def _apply_mic_profile(args: argparse.Namespace, raw_argv: list[str]) -> None:
    device_name = _selected_audio_device_name(args.audio_device) if args.mic_profile == "auto" else None
    profile = load_mic_profile(args.mic_profile, device_name=device_name)
    args.audio_analysis = profile.analysis
    if profile.mic_target_level is not None and not _option_provided(raw_argv, "--mic-target-level"):
        args.mic_target_level = profile.mic_target_level
    if profile.mic_noise_floor is not None and not _option_provided(raw_argv, "--mic-noise-floor"):
        args.mic_noise_floor = profile.mic_noise_floor
    if profile.idle_threshold_scale is not None and not _option_provided(raw_argv, "--idle-threshold-scale"):
        args.idle_threshold_scale = profile.idle_threshold_scale


def _selected_audio_device_name(pattern: str | None) -> str | None:
    try:
        devices = list_input_device_details()
    except RuntimeError:
        return None
    if not devices:
        return None
    if pattern is None:
        return devices[0].name
    if pattern.isdigit():
        index = int(pattern)
        for device in devices:
            if device.index == index:
                return device.name
        return None
    lowered = pattern.casefold()
    for device in devices:
        if lowered in device.name.casefold():
            return device.name
    return None


def _option_provided(argv: list[str], option: str) -> bool:
    prefix = f"{option}="
    return any(value == option or value.startswith(prefix) for value in argv)


def _build_audio_config(*, target_level: float, noise_floor: float, analysis: AudioAnalysis | None = None) -> AudioConfig:
    return AudioConfig(
        smoothing=AudioSmoothing(noise_floor=noise_floor),
        normalization=AudioNormalization(target_level=target_level),
        analysis=analysis or AudioAnalysis(),
    )


def _build_cycling_config(args: argparse.Namespace) -> CyclingConfig:
    return CyclingConfig(
        order=args.cycle_order,
        timing=args.cycle_timing,
        interval_s=args.cycle_interval,
        seed=args.dynamic_seed,
    )


def _build_dynamic_selector_config(args: argparse.Namespace) -> DynamicSelectorConfig:
    return DynamicSelectorConfig(
        min_duration_s=args.dynamic_min_duration,
        max_duration_s=args.dynamic_max_duration,
        switch_cooldown_s=args.dynamic_switch_cooldown,
        drop_cooldown_s=args.dynamic_drop_cooldown,
        randomness=args.dynamic_randomness,
        history_size=args.dynamic_history_size,
        seed=args.dynamic_seed,
    )


def _build_activity_config(
    *,
    idle_enter_frames: int,
    idle_threshold_scale: float,
    activation_delay_s: float,
) -> MusicActivityConfig:
    defaults = MusicActivityConfig()
    return MusicActivityConfig(
        feature_attack=defaults.feature_attack,
        feature_release=defaults.feature_release,
        idle_enter_frames=idle_enter_frames,
        activation_delay_s=activation_delay_s,
        energy_threshold=defaults.energy_threshold * idle_threshold_scale,
        onset_threshold=defaults.onset_threshold * idle_threshold_scale,
        beat_density_threshold=defaults.beat_density_threshold * idle_threshold_scale,
        brightness_threshold=defaults.brightness_threshold * idle_threshold_scale,
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
