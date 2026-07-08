import argparse
import builtins

import numpy as np
import pytest

from lumistripe import (
    AnimationClass,
    AutoSelectorConfig,
    AudioAnalysis,
    AudioCalibrationResult,
    AudioConfig,
    AudioFrame,
    AudioNormalization,
    AudioSmoothing,
    MusicDrivenSelector,
    MusicFeatures,
    MusicSelectorConfig,
    DJModeSelector,
)
from lumistripe_sim.simulator import (
    MIN_FRAME_SECONDS,
    SimulatorApp,
    SimulatorMode,
    _build_auto_selector_config,
    _load_tkfont,
    _load_tkinter,
    _option_provided,
    _parse_mode,
    _selected_audio_device_name,
    build_parser,
    demo_frame,
    layout_controls,
    main,
    pixel_pitch,
    window_size,
)


class FakeCanvas:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.rectangles: list[tuple[int, int, int, int, dict[str, str]]] = []

    def delete(self, tag: str) -> None:
        self.deleted.append(tag)

    def create_rectangle(self, x1: int, y1: int, x2: int, y2: int, **kwargs: str) -> None:
        self.rectangles.append((x1, y1, x2, y2, kwargs))


class FakeButton:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.configures: list[dict[str, object]] = []

    def configure(self, **kwargs: object) -> None:
        self.configures.append(kwargs)


class FakeTkinter:
    @staticmethod
    def Button(*args: object, **kwargs: object) -> FakeButton:
        return FakeButton(*args, **kwargs)


def test_pixel_pitch_and_window_size() -> None:
    assert pixel_pitch() == 32
    assert window_size(10) == (416, 676)


def test_layout_controls_are_clickable() -> None:
    controls = layout_controls()
    assert controls.prev.contains(48, 32)
    assert controls.next.contains(253, 40)
    assert controls.manual.contains(48, 116)
    assert controls.demo.contains(216, 116)
    assert controls.mic.contains(384, 116)
    assert controls.calibrate.contains(721, 116)
    assert not controls.next.contains(10, 10)


def test_simulator_app_initial_state() -> None:
    app = SimulatorApp(pixel_count=12)
    assert app.pixel_count == 12
    assert app.animation_name
    assert app.mode_label == "MANUAL"
    assert app.audio_status == "No audio source active."


def test_simulator_app_class_label_reflects_selector_idle_state() -> None:
    app = SimulatorApp(pixel_count=12)
    assert app.class_label == "-"

    app.selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    assert app.class_label == "GROOVY"

    app.selector.idle_active = True
    assert app.class_label == "IDLE"


def test_simulator_app_mic_tuning_label_formats_values() -> None:
    app = SimulatorApp(pixel_count=12, mic_target_level=0.5, mic_noise_floor=0.01, idle_enter_frames=42, idle_threshold_scale=1.5)
    assert app.mic_tuning_label == "MIC: target=0.50 noise=0.010 idle=42f scale=1.50"


def test_handle_key_changes_animation_and_exit_state() -> None:
    app = SimulatorApp(pixel_count=12)
    first = app.player.current_index()
    app.handle_key("right")
    assert app.player.current_index() != first
    app.handle_key("left")
    assert app.player.current_index() == first
    app.handle_key("escape")
    assert app.running is False


def test_handle_key_switches_modes_and_calibrates(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAudioInput:
        def read(self) -> AudioFrame:
            return AudioFrame()

        def read_features(self) -> MusicFeatures:
            return MusicFeatures()

        def device_name(self) -> str:
            return "Fake Mic"

        def close(self) -> None:
            return None

        @classmethod
        def with_config(cls, config: AudioConfig) -> "FakeAudioInput":
            del config
            return cls()

    calibration = AudioCalibrationResult(
        duration=2.0,
        samples=5,
        measured_floor=0.01,
        measured_peak=0.2,
        recommended_noise_floor=0.02,
        recommended_target_level=0.4,
        recommended_idle_threshold_scale=1.3,
    )
    monkeypatch.setattr("lumistripe_sim.simulator.AudioInput", FakeAudioInput)
    monkeypatch.setattr("lumistripe_sim.simulator.calibrate_audio_input", lambda **kwargs: calibration)

    app = SimulatorApp(pixel_count=12)
    app.handle_key("d")
    assert app.mode is SimulatorMode.DEMO
    app.handle_key("m")
    assert app.mode is SimulatorMode.MANUAL
    app.handle_key("a")
    assert app.mode is SimulatorMode.MIC
    app.handle_key("s")
    assert app.selector is not None
    assert app.selector.auto_select is True
    app.handle_key("j")
    assert app.mode is SimulatorMode.DJ
    assert app.dj_selector is not None
    app.handle_key("c")
    assert app.audio_calibration is calibration


def test_handle_click_changes_animation() -> None:
    app = SimulatorApp(pixel_count=12)
    first = app.player.current_index()
    app.handle_click(app.controls.next.x + 1, app.controls.next.y + 1)
    assert app.player.current_index() != first


def test_handle_click_routes_mode_auto_and_calibration(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAudioInput:
        def read(self) -> AudioFrame:
            return AudioFrame()

        def read_features(self) -> MusicFeatures:
            return MusicFeatures()

        def device_name(self) -> str:
            return "Fake Mic"

        def close(self) -> None:
            return None

        @classmethod
        def with_config(cls, config: AudioConfig) -> "FakeAudioInput":
            del config
            return cls()

    calibration = AudioCalibrationResult(
        duration=2.0,
        samples=8,
        measured_floor=0.01,
        measured_peak=0.2,
        recommended_noise_floor=0.02,
        recommended_target_level=0.4,
        recommended_idle_threshold_scale=1.3,
    )
    monkeypatch.setattr("lumistripe_sim.simulator.AudioInput", FakeAudioInput)
    monkeypatch.setattr("lumistripe_sim.simulator.calibrate_audio_input", lambda **kwargs: calibration)

    app = SimulatorApp(pixel_count=12)
    app.handle_click(app.controls.demo.x + 1, app.controls.demo.y + 1)
    assert app.mode is SimulatorMode.DEMO
    app.handle_click(app.controls.manual.x + 1, app.controls.manual.y + 1)
    assert app.mode is SimulatorMode.MANUAL
    app.handle_click(app.controls.mic.x + 1, app.controls.mic.y + 1)
    assert app.mode is SimulatorMode.MIC

    app.handle_click(app.controls.auto_sel.x + 1, app.controls.auto_sel.y + 1)
    assert app.selector is not None
    assert app.selector.auto_select is True
    app.handle_click(app.controls.calibrate.x + 1, app.controls.calibrate.y + 1)
    assert app.audio_calibration is calibration


def test_mode_switch_to_demo_sets_audio_snapshot() -> None:
    app = SimulatorApp(pixel_count=12)
    start_index = app.player.current_index()
    app.set_mode(SimulatorMode.DEMO)
    assert app.mode is SimulatorMode.DEMO
    assert app.audio_status == "Using internal demo beat."
    assert app.player.current_index() == start_index
    delay = app.step()
    assert delay >= MIN_FRAME_SECONDS
    assert app.audio_frame.rms > 0.0


def test_analysis_text_formats_audio_values() -> None:
    app = SimulatorApp(pixel_count=12)
    app.audio_frame = AudioFrame(
        rms=0.5,
        bands=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
        beat=True,
        beat_strength=0.9,
    )
    app.music_features = MusicFeatures(
        rolling_loudness=0.44,
        spectral_flux=0.31,
        drop_detected=True,
    )
    text = app.analysis_text()
    assert "RMS: 0.500" in text
    assert "BEAT: YES" in text
    assert "BPM" in text
    assert "LOUD: 0.44" in text
    assert "FLUX: 0.31" in text
    assert "DROP: YES" in text


def test_analysis_text_includes_dj_summary() -> None:
    app = SimulatorApp(pixel_count=12)
    app.dj_selector = DJModeSelector(AutoSelectorConfig(randomness=0.0))
    app.dj_selector.update(app.player, MusicFeatures(), now_s=0.0)

    text = app.analysis_text()

    assert "DJ:" in text
    assert "reason=initial_hold" in text


def test_demo_frame_has_energy() -> None:
    frame = demo_frame(0)
    assert frame.rms > 0.0
    assert frame.beat is True


def test_mic_mode_falls_back_to_manual_on_error(monkeypatch) -> None:
    class BrokenAudioInput:
        @classmethod
        def with_config(cls, config) -> AudioFrame:
            del config
            raise RuntimeError("no audio input device available")

    monkeypatch.setattr("lumistripe_sim.simulator.AudioInput", BrokenAudioInput)
    app = SimulatorApp(pixel_count=12, mode=SimulatorMode.MIC)
    assert app.mode is SimulatorMode.MANUAL
    assert app.audio_error == "no audio input device available"
    assert app.audio_status == "Microphone unavailable."


def test_mic_mode_keeps_animation_stable(monkeypatch) -> None:
    class FakeAudioInput:
        last_config: AudioConfig | None = None

        def read(self) -> AudioFrame:
            return AudioFrame(
                rms=0.95,
                bands=(0.9, 0.85, 0.75, 0.72, 0.7, 0.88, 0.9, 0.92),
                beat=True,
                beat_strength=1.0,
            )

        def read_features(self) -> MusicFeatures:
            return MusicFeatures(
                bpm=140.0,
                energy=0.95,
                bass=0.7,
                brightness=0.7,
                onset_strength=0.5,
                dynamic_range=0.6,
                beat=True,
                beat_strength=1.0,
                bands=(0.9, 0.85, 0.75, 0.72, 0.7, 0.88, 0.9, 0.92),
            )

        def device_name(self) -> str:
            return "Fake Mic"

        def close(self) -> None:
            return None

        @classmethod
        def with_config(cls, config):
            cls.last_config = config
            return cls()

    monkeypatch.setattr("lumistripe_sim.simulator.AudioInput", FakeAudioInput)
    app = SimulatorApp(
        pixel_count=12,
        mode=SimulatorMode.MIC,
        mic_target_level=0.5,
        mic_noise_floor=0.01,
        idle_enter_frames=42,
        idle_threshold_scale=1.5,
    )
    start_index = app.player.current_index()
    for _ in range(140):
        app.step()
    assert app.player.current_index() == start_index
    assert app.animation_name.startswith(app.player.name_at(start_index).upper())
    assert app.audio_status == "Input: Fake Mic"
    assert FakeAudioInput.last_config == AudioConfig(
        smoothing=AudioSmoothing(noise_floor=0.01),
        normalization=AudioNormalization(target_level=0.5),
    )
    assert app.selector is not None
    assert app.selector.auto_select is False
    assert app.selector.config.idle_enter_frames == 42
    assert app.selector.config.idle_energy_threshold == pytest.approx(MusicSelectorConfig().idle_energy_threshold * 1.5)


def test_mode_switches_do_not_change_current_animation(monkeypatch) -> None:
    class FakeAudioInput:
        def read(self) -> AudioFrame:
            return AudioFrame()

        def read_features(self) -> MusicFeatures:
            return MusicFeatures()

        def device_name(self) -> str:
            return "Fake Mic"

        def close(self) -> None:
            return None

        @classmethod
        def with_config(cls, config):
            del config
            return cls()

    monkeypatch.setattr("lumistripe_sim.simulator.AudioInput", FakeAudioInput)
    app = SimulatorApp(pixel_count=12)
    app.player.next()
    app.player.next()
    start_index = app.player.current_index()

    app.set_mode(SimulatorMode.DEMO)
    assert app.player.current_index() == start_index

    app.set_mode(SimulatorMode.MIC)
    assert app.player.current_index() == start_index

    app.set_mode(SimulatorMode.MANUAL)
    assert app.player.current_index() == start_index


def test_calibrate_audio_applies_recommended_values(monkeypatch: pytest.MonkeyPatch) -> None:
    result = AudioCalibrationResult(
        duration=2.0,
        samples=6,
        measured_floor=0.01,
        measured_peak=0.2,
        recommended_noise_floor=0.03,
        recommended_target_level=0.4,
        recommended_idle_threshold_scale=1.7,
    )

    monkeypatch.setattr("lumistripe_sim.simulator.calibrate_audio_input", lambda **kwargs: result)

    app = SimulatorApp(pixel_count=12)
    assert app.calibrate_audio(2.0) is result
    assert app.audio_calibration is result
    assert app.mic_noise_floor == pytest.approx(0.03)
    assert app.mic_target_level == pytest.approx(0.4)
    assert app.idle_threshold_scale == pytest.approx(1.7)
    assert "calibrated=6f" in app.mic_tuning_label


def test_calibrate_audio_records_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**kwargs: object) -> AudioCalibrationResult:
        del kwargs
        raise RuntimeError("calibration failed")

    monkeypatch.setattr("lumistripe_sim.simulator.calibrate_audio_input", fail)

    app = SimulatorApp(pixel_count=12)

    assert app.calibrate_audio(2.0) is None
    assert app.audio_error == "calibration failed"
    assert app.audio_calibration is None


def test_startup_auto_calibration_applies_before_mic_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    result = AudioCalibrationResult(
        duration=2.0,
        samples=6,
        measured_floor=0.01,
        measured_peak=0.2,
        recommended_noise_floor=0.03,
        recommended_target_level=0.4,
        recommended_idle_threshold_scale=1.7,
    )

    class FakeAudioInput:
        seen: AudioConfig | None = None

        def read(self) -> AudioFrame:
            return AudioFrame()

        def read_features(self) -> MusicFeatures:
            return MusicFeatures()

        def device_name(self) -> str:
            return "USB Mic"

        def close(self) -> None:
            return None

        @classmethod
        def with_config(cls, config):
            cls.seen = config
            return cls()

    monkeypatch.setattr("lumistripe_sim.simulator.calibrate_audio_input", lambda **kwargs: result)
    monkeypatch.setattr("lumistripe_sim.simulator.AudioInput", FakeAudioInput)

    app = SimulatorApp(pixel_count=12, mode=SimulatorMode.MIC, auto_calibrate_audio=2.0)

    assert app.audio_calibration is result
    assert app.audio_status == "Input: USB Mic"
    assert FakeAudioInput.seen == AudioConfig(
        smoothing=AudioSmoothing(noise_floor=0.03),
        normalization=AudioNormalization(target_level=0.4),
    )


def test_step_returns_minimum_frame_time() -> None:
    app = SimulatorApp(pixel_count=12)
    delay = app.step()
    assert delay >= MIN_FRAME_SECONDS


def test_parser_accepts_audio_device_string() -> None:
    parser = build_parser()
    args = parser.parse_args(["--audio-device", "2"])
    assert args.audio_device == "2"


def test_parser_accepts_dj_mode_and_selector_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(["--mode", "dj", "--dj-min-duration", "3", "--dj-seed", "9"])
    assert args.mode is SimulatorMode.DJ
    assert args.dj_min_duration == pytest.approx(3.0)
    assert args.dj_seed == 9


def test_parser_accepts_mic_tuning_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--mic-target-level",
            "0.5",
            "--mic-noise-floor",
            "0.01",
            "--mic-profile",
            "pcm2902",
            "--idle-enter-frames",
            "42",
            "--idle-threshold-scale",
            "1.5",
        ]
    )
    assert args.mic_target_level == pytest.approx(0.5)
    assert args.mic_noise_floor == pytest.approx(0.01)
    assert args.mic_profile == "pcm2902"
    assert args.idle_enter_frames == 42
    assert args.idle_threshold_scale == pytest.approx(1.5)


def test_parser_accepts_auto_calibration_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["--auto-calibrate-audio", "2.5"])
    assert args.auto_calibrate_audio == pytest.approx(2.5)


def test_parser_rejects_invalid_mic_tuning_values() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--mic-target-level", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--mic-noise-floor", "-0.1"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--idle-enter-frames", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--idle-threshold-scale", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--auto-calibrate-audio", "0"])


def test_parser_rejects_invalid_mode() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="invalid mode"):
        _parse_mode("unknown")


def test_option_provided_detects_space_and_equals_forms() -> None:
    assert _option_provided(["--mic-target-level", "0.5"], "--mic-target-level")
    assert _option_provided(["--mic-target-level=0.5"], "--mic-target-level")
    assert not _option_provided(["--mic-noise-floor", "0.1"], "--mic-target-level")


def test_build_auto_selector_config_maps_parser_args() -> None:
    args = build_parser().parse_args(
        [
            "--dj-min-duration",
            "3",
            "--dj-max-duration",
            "9",
            "--dj-switch-cooldown",
            "1.5",
            "--dj-drop-cooldown",
            "2.5",
            "--dj-randomness",
            "0.2",
            "--dj-history-size",
            "7",
            "--dj-seed",
            "123",
        ]
    )

    config = _build_auto_selector_config(args)

    assert config.min_duration_s == pytest.approx(3.0)
    assert config.max_duration_s == pytest.approx(9.0)
    assert config.switch_cooldown_s == pytest.approx(1.5)
    assert config.drop_cooldown_s == pytest.approx(2.5)
    assert config.randomness == pytest.approx(0.2)
    assert config.history_size == 7
    assert config.seed == 123


def test_mic_mode_uses_device_specific_audio_config(monkeypatch) -> None:
    class FakeAudioInput:
        seen: tuple[str, AudioConfig] | None = None

        def read(self) -> AudioFrame:
            return AudioFrame()

        def read_features(self) -> MusicFeatures:
            return MusicFeatures()

        def device_name(self) -> str:
            return "USB Mic"

        def close(self) -> None:
            return None

        @classmethod
        def with_device_config(cls, pattern, config):
            cls.seen = (pattern, config)
            return cls()

    monkeypatch.setattr("lumistripe_sim.simulator.AudioInput", FakeAudioInput)
    app = SimulatorApp(pixel_count=12, mode=SimulatorMode.MIC, audio_device="2")
    assert app.audio_status == "Input: USB Mic"
    assert FakeAudioInput.seen == (
        "2",
        AudioConfig(
            smoothing=AudioSmoothing(noise_floor=AudioSmoothing().noise_floor),
            normalization=AudioNormalization(target_level=AudioNormalization().target_level),
        ),
    )


def test_selected_audio_device_name_matches_defaults_and_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = [
        type("Device", (), {"index": 1, "name": "USB Mic"})(),
        type("Device", (), {"index": 2, "name": "Line Input"})(),
    ]
    monkeypatch.setattr("lumistripe_sim.simulator.list_input_device_details", lambda: devices)

    assert _selected_audio_device_name(None) == "USB Mic"
    assert _selected_audio_device_name("2") == "Line Input"
    assert _selected_audio_device_name("line") == "Line Input"
    assert _selected_audio_device_name("9") is None
    assert _selected_audio_device_name("missing") is None


def test_selected_audio_device_name_handles_empty_or_unavailable_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lumistripe_sim.simulator.list_input_device_details", lambda: [])
    assert _selected_audio_device_name(None) is None

    def fail() -> list[object]:
        raise RuntimeError("no devices")

    monkeypatch.setattr("lumistripe_sim.simulator.list_input_device_details", fail)
    assert _selected_audio_device_name(None) is None


def test_main_lists_audio_devices_and_exits(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        "lumistripe_sim.simulator.list_input_device_details",
        lambda: [
            type("Device", (), {"index": 1, "name": "USB Mic"})(),
            type("Device", (), {"index": 2, "name": "Default Input"})(),
        ],
    )

    main(["--list-audio-devices"])

    captured = capsys.readouterr()
    assert "1: USB Mic" in captured.out
    assert "2: Default Input" in captured.out


def test_main_applies_auto_mic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr(
        "lumistripe_sim.simulator.list_input_device_details",
        lambda: [type("Device", (), {"index": 1, "name": "Texas Instruments PCM2902 Audio Codec"})()],
    )

    class FakeSimulatorApp:
        def __init__(self, **kwargs) -> None:
            called.update(kwargs)

        def run(self) -> None:
            called["ran"] = True

    monkeypatch.setattr("lumistripe_sim.simulator.SimulatorApp", FakeSimulatorApp)

    main(["--mode", "mic", "--mic-profile", "auto"])

    assert called["mic_target_level"] == pytest.approx(0.403)
    assert called["mic_noise_floor"] == pytest.approx(0.0137)
    assert called["idle_threshold_scale"] == pytest.approx(0.92)
    assert isinstance(called["audio_analysis"], AudioAnalysis)
    assert called["ran"] is True


def test_render_draws_scaled_subpixel_bars() -> None:
    app = SimulatorApp(pixel_count=2)
    app.stripe.set_pixels(np.array([[100, 50, 25, 128], [10, 20, 30, 255]], dtype=np.uint8))
    canvas = FakeCanvas()

    app.render(canvas, width=200, height=220)

    assert canvas.deleted == ["all"]
    assert canvas.rectangles[0] == (
        0,
        0,
        200,
        220,
        {"fill": "#1a1a1a", "outline": ""},
    )
    assert len(canvas.rectangles) == 1 + app.pixel_count * 3
    assert canvas.rectangles[1] == (
        48,
        48,
        56,
        168,
        {"fill": "#32190c", "outline": ""},
    )
    assert canvas.rectangles[4] == (
        80,
        48,
        88,
        168,
        {"fill": "#0a141e", "outline": ""},
    )


def test_render_caps_bar_height_to_available_space() -> None:
    app = SimulatorApp(pixel_count=1)
    app.stripe.set_pixels(np.array([[255, 255, 255, 255]], dtype=np.uint8))
    canvas = FakeCanvas()

    app.render(canvas, width=120, height=120)

    assert canvas.rectangles[1][:4] == (48, 48, 56, 72)


def test_style_mode_button_sets_active_and_inactive_colors() -> None:
    app = SimulatorApp(pixel_count=1)
    button = FakeButton()

    app._style_mode_button(button, True)
    app._style_mode_button(button, False)

    assert button.configures[0] == {
        "bg": "#3c5a2a",
        "activebackground": "#3c5a2a",
        "highlightbackground": "#86d26a",
        "highlightcolor": "#86d26a",
    }
    assert button.configures[1] == {
        "bg": "#363636",
        "activebackground": "#363636",
        "highlightbackground": "#7a7a7a",
        "highlightcolor": "#7a7a7a",
    }


def test_make_button_passes_tk_options() -> None:
    app = SimulatorApp(pixel_count=1)
    command_called = False

    def command() -> None:
        nonlocal command_called
        command_called = True

    button = app._make_button(FakeTkinter, "parent", "RUN", command, "font")

    assert button.args == ("parent",)
    assert button.kwargs["text"] == "RUN"
    assert button.kwargs["command"] is command
    assert button.kwargs["font"] == "font"
    assert button.kwargs["fg"] == "#f1f1f1"
    assert button.kwargs["bg"] == "#363636"
    assert button.kwargs["highlightbackground"] == "#7a7a7a"

    button.kwargs["command"]()
    assert command_called is True


def test_load_tkinter_reports_missing_module(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "tkinter":
            raise ImportError("missing tkinter")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="tkinter is required"):
        _load_tkinter()


def test_load_tkfont_reports_missing_module(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "tkinter.font":
            raise ImportError("missing tkinter font")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="tkinter font support"):
        _load_tkfont()
