import pytest

from lumistripe import (
    AnimationClass,
    AudioConfig,
    AudioFrame,
    AudioNormalization,
    AudioSmoothing,
    MultiController,
    MusicDrivenSelector,
    MusicFeatures,
    MusicSelectorConfig,
    Stripe,
)
from lumistripe_cli.app import (
    MIN_FRAME_SECONDS,
    HeadlessApp,
    RuntimeMode,
    build_output_controller,
    build_parser,
    demo_frame,
    main,
)


class FakeGPIOStripe(Stripe):
    def __init__(self, config, length: int) -> None:
        super().__init__(length)
        self.config = config


def test_demo_frame_has_energy() -> None:
    frame = demo_frame(0)
    assert frame.rms > 0.0
    assert frame.beat is True


def test_headless_app_class_label_reflects_selector_idle_state() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12)
    assert app.class_label == "-"

    app.selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    assert app.class_label == "GROOVY"

    app.selector.idle_active = True
    assert app.class_label == "IDLE"


def test_parser_accepts_second_stripe_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(["--pixels", "32", "--data-pin-2", "16", "--clock-pin-2", "20"])
    assert args.pixels == 32
    assert args.data_pin_2 == 16
    assert args.clock_pin_2 == 20


def test_parser_accepts_quiet_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["--quiet"])
    assert args.quiet is True


def test_parser_accepts_audio_debug_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["--audio-debug"])
    assert args.audio_debug is True


def test_parser_accepts_audio_debug_verbose_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["--audio-debug-verbose"])
    assert args.audio_debug_verbose is True


def test_parser_accepts_mic_tuning_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--mic-target-level",
            "0.5",
            "--mic-noise-floor",
            "0.01",
            "--idle-enter-frames",
            "42",
            "--idle-threshold-scale",
            "1.5",
        ]
    )
    assert args.mic_target_level == pytest.approx(0.5)
    assert args.mic_noise_floor == pytest.approx(0.01)
    assert args.idle_enter_frames == 42
    assert args.idle_threshold_scale == pytest.approx(1.5)


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


def test_build_output_controller_returns_single_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lumistripe_cli.app.GPIOStripe", FakeGPIOStripe)
    monkeypatch.setattr("lumistripe_cli.app._ensure_gpio_ready", lambda chip: None)
    args = build_parser().parse_args(["--pixels", "16"])
    controller = build_output_controller(args)
    assert isinstance(controller, FakeGPIOStripe)
    assert controller.length == 16


def test_build_output_controller_returns_multi_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lumistripe_cli.app.GPIOStripe", FakeGPIOStripe)
    monkeypatch.setattr("lumistripe_cli.app._ensure_gpio_ready", lambda chip: None)
    args = build_parser().parse_args(["--pixels", "16", "--data-pin-2", "16", "--clock-pin-2", "20"])
    controller = build_output_controller(args)
    assert isinstance(controller, MultiController)
    assert controller.length == 16


def test_build_output_controller_rejects_partial_second_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lumistripe_cli.app.GPIOStripe", FakeGPIOStripe)
    monkeypatch.setattr("lumistripe_cli.app._ensure_gpio_ready", lambda chip: None)
    args = build_parser().parse_args(["--pixels", "16", "--data-pin-2", "16"])
    with pytest.raises(ValueError, match="secondary stripe"):
        build_output_controller(args)


def test_build_output_controller_fails_nicely_when_chip_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lumistripe_cli.app.importlib.util.find_spec", lambda name: object())

    class MissingPath:
        def __init__(self, path: str) -> None:
            self.path = path

        def exists(self) -> bool:
            return False

        def is_char_device(self) -> bool:
            return False

    monkeypatch.setattr("lumistripe_cli.app.Path", MissingPath)
    args = build_parser().parse_args(["--pixels", "16", "--chip", "/dev/gpiochip9"])
    with pytest.raises(RuntimeError, match='GPIO chip "/dev/gpiochip9" was not found'):
        build_output_controller(args)


def test_build_output_controller_fails_nicely_when_chip_permission_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lumistripe_cli.app.importlib.util.find_spec", lambda name: object())

    class PresentPath:
        def __init__(self, path: str) -> None:
            self.path = path

        def exists(self) -> bool:
            return True

        def is_char_device(self) -> bool:
            return True

        def __fspath__(self) -> str:
            return self.path

    monkeypatch.setattr("lumistripe_cli.app.Path", PresentPath)
    monkeypatch.setattr("lumistripe_cli.app.os.access", lambda path, mode: False)
    args = build_parser().parse_args(["--pixels", "16", "--chip", "/dev/gpiochip0"])
    with pytest.raises(RuntimeError, match='permission denied for GPIO chip "/dev/gpiochip0"'):
        build_output_controller(args)


def test_main_exits_with_nice_gpio_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lumistripe_cli.app.build_output_controller",
        lambda args: (_ for _ in ()).throw(RuntimeError('GPIO chip "/dev/gpiochip0" was not found.')),
    )
    with pytest.raises(SystemExit, match='error: GPIO chip "/dev/gpiochip0" was not found\\.'):
        main([])


def test_main_exits_with_nice_gpio_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lumistripe_cli.app.build_output_controller",
        lambda args: (_ for _ in ()).throw(RuntimeError('permission denied for GPIO chip "/dev/gpiochip0".')),
    )
    with pytest.raises(SystemExit, match='error: permission denied for GPIO chip "/dev/gpiochip0"\\.'):
        main([])


def test_headless_app_manual_step_returns_minimum_frame_time() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, animation_name="pulse")
    delay = app.step()
    assert delay >= MIN_FRAME_SECONDS


def test_headless_app_formats_status_like_simulator() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, animation_name="pulse", quiet=True)
    app.audio_frame = AudioFrame(
        rms=0.5,
        bands=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
        beat=True,
        beat_strength=0.9,
    )
    app.music_features = MusicFeatures(
        bpm=128.0,
        energy=0.5,
        bass=0.2,
        brightness=0.4,
        onset_strength=0.3,
        dynamic_range=0.2,
        beat=True,
        beat_strength=0.9,
        bands=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
    )
    assert "PULSE" in app.current_animation_label
    assert app.mode_label == "MANUAL"
    assert app.class_label == "-"
    text = app.analysis_text()
    assert "RMS: 0.500" in text
    assert "BEAT: YES" in text
    assert "BPM: 128" in text
    assert "BRIGHT: 0.40" in text
    assert "BANDS: 0.10 0.20 0.30 0.40 0.50 0.60 0.70 0.80" in text


def test_headless_app_status_block_includes_error_when_present() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    app.audio_error = "problem"
    block = app._status_block()
    assert "ERROR: problem" in block


def test_headless_app_status_block_includes_mic_tuning_in_mic_mode() -> None:
    app = HeadlessApp(
        controller=Stripe(12),
        pixel_count=12,
        mode=RuntimeMode.MIC,
        mic_target_level=0.5,
        mic_noise_floor=0.01,
        idle_enter_frames=42,
        idle_threshold_scale=1.5,
        quiet=True,
    )
    app.mode = RuntimeMode.MIC
    block = app._status_block()
    assert "MIC: target=0.50 noise=0.010 idle=42f scale=1.50" in block


def test_headless_app_status_block_omits_mic_tuning_outside_mic_mode() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    assert "MIC:" not in app._status_block()


def test_headless_app_debug_header_and_line_include_metrics() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    app.mode = RuntimeMode.MIC
    app.audio_status = "Input: Fake Mic"
    app.audio_frame = AudioFrame(
        rms=0.5,
        bands=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
        beat=True,
        beat_strength=0.9,
    )
    app.music_features = MusicFeatures(
        bpm=128.0,
        energy=0.5,
        bass=0.2,
        brightness=0.4,
        onset_strength=0.3,
        dynamic_range=0.2,
        beat=True,
        beat_strength=0.9,
        bands=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
    )
    app.selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    assert "AUDIO-DEBUG SOURCE=Input: Fake Mic" in app.debug_header()
    line = app.debug_log_line(1.25)
    assert "t=1.25s" in line
    assert "CLASS=GROOVY" in line
    assert "AUTO=ON" in line
    assert "IDLE=NO" in line
    assert "RMS=0.500" in line
    assert "BPM=128" in line
    assert "BANDS=0.10,0.20,0.30,0.40,0.50,0.60,0.70,0.80" in line
    assert "TOP=" in line


def test_headless_app_debug_log_line_includes_verbose_selector_details() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True, audio_debug_verbose=True)
    app.mode = RuntimeMode.MIC
    app.audio_status = "Input: Fake Mic"
    app.audio_frame = AudioFrame(rms=0.5, bands=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8), beat=True, beat_strength=0.9)
    app.music_features = MusicFeatures(
        bpm=128.0,
        energy=0.5,
        bass=0.2,
        brightness=0.4,
        onset_strength=0.3,
        dynamic_range=0.2,
        beat=True,
        beat_strength=0.9,
        bands=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
    )
    app.selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    line = app.debug_log_line(1.25)
    assert "SEL=energy_short:" in line
    assert "SCORES=" in line


def test_headless_app_debug_transition_line_includes_score_snapshot() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    app.selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    line = app.debug_transition_line(1.25, "GROOVY", "CALM")
    assert "TRANSITION CLASS=GROOVY->CALM" in line
    assert "SCORES=" in line


def test_headless_app_run_audio_debug_prints_header_and_lines(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    app.mode = RuntimeMode.MIC
    app.audio_status = "Input: Fake Mic"
    monkeypatch.setattr(HeadlessApp, "step", lambda self: 0.01)
    monkeypatch.setattr(HeadlessApp, "debug_log_line", lambda self, elapsed: f"tick {elapsed >= 0}")
    app.run_audio_debug(frame_limit=2)
    captured = capsys.readouterr()
    assert "AUDIO-DEBUG SOURCE=Input: Fake Mic" in captured.out
    assert captured.out.count("tick True") == 2


def test_headless_app_run_audio_debug_emits_transition_lines(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    app.mode = RuntimeMode.MIC
    classes = iter(["GROOVY", "CALM"])
    monkeypatch.setattr(HeadlessApp, "step", lambda self: 0.01)
    monkeypatch.setattr(HeadlessApp, "class_label", property(lambda self: next(classes, "CALM")))
    monkeypatch.setattr(HeadlessApp, "debug_log_line", lambda self, elapsed: "tick")
    monkeypatch.setattr(HeadlessApp, "debug_transition_line", lambda self, elapsed, previous_class, current_class: f"transition {previous_class}->{current_class}")
    app.run_audio_debug(frame_limit=2)
    captured = capsys.readouterr()
    assert "transition GROOVY->CALM" in captured.out


def test_headless_app_quiet_disables_status_output(capsys: pytest.CaptureFixture[str]) -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    app._render_status(1.0)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_headless_app_non_tty_status_prints_periodically(capsys: pytest.CaptureFixture[str]) -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=False)
    app._status_tty = False
    app._render_status(1.0)
    app._render_status(1.1)
    captured = capsys.readouterr()
    assert "ANIM:" in captured.out
    assert captured.out.count("ANIM:") == 1


def test_headless_app_skips_status_formatting_when_not_due(monkeypatch: pytest.MonkeyPatch) -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=False)
    app._status_tty = False
    app._last_status_at = 1.0

    def fail() -> str:
        raise AssertionError("status block should not be built when refresh is not due")

    monkeypatch.setattr(HeadlessApp, "_status_block", lambda self: fail())
    app._render_status(1.1)


def test_headless_app_tty_status_writes_live_block(monkeypatch: pytest.MonkeyPatch) -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=False)
    app._status_tty = True

    writes: list[str] = []

    monkeypatch.setattr("lumistripe_cli.app.sys.stdout.write", writes.append)
    monkeypatch.setattr("lumistripe_cli.app.sys.stdout.flush", lambda: None)

    app._write_live_status("ANIM: TEST")
    assert any("ANIM: TEST" in chunk for chunk in writes)


def test_headless_app_finish_status_emits_newline_for_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=False)
    app._status_tty = True
    app._status_lines = 3

    writes: list[str] = []

    monkeypatch.setattr("lumistripe_cli.app.sys.stdout.write", writes.append)
    monkeypatch.setattr("lumistripe_cli.app.sys.stdout.flush", lambda: None)

    app._finish_status()
    assert writes == ["\n"]
    assert app._status_lines == 0


def test_headless_app_mic_mode_falls_back_to_manual_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenAudioInput:
        @classmethod
        def with_config(cls, config):
            del config
            raise RuntimeError("no audio input device available")

    monkeypatch.setattr("lumistripe_cli.app.AudioInput", BrokenAudioInput)
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, mode=RuntimeMode.MIC)
    assert app.mode is RuntimeMode.MANUAL
    assert app.audio_error == "no audio input device available"
    assert app.audio_status == "Microphone unavailable."


def test_headless_app_mic_mode_uses_music_selector(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr("lumistripe_cli.app.AudioInput", FakeAudioInput)
    app = HeadlessApp(
        controller=Stripe(12),
        pixel_count=12,
        mode=RuntimeMode.MIC,
        mic_target_level=0.5,
        mic_noise_floor=0.01,
        idle_enter_frames=42,
        idle_threshold_scale=1.5,
    )
    for _ in range(140):
        app.step()
    assert app.selector is not None
    assert app.selector.current_class in {app.selector.current_class.FAST_PARTY, app.selector.current_class.CHAOTIC}
    assert app.audio_status == "Input: Fake Mic"
    assert FakeAudioInput.last_config == AudioConfig(
        smoothing=AudioSmoothing(noise_floor=0.01),
        normalization=AudioNormalization(target_level=0.5),
    )
    assert app.selector.config.idle_enter_frames == 42
    assert app.selector.config.idle_energy_threshold == pytest.approx(MusicSelectorConfig().idle_energy_threshold * 1.5)


def test_headless_app_mic_mode_uses_device_specific_audio_config(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr("lumistripe_cli.app.AudioInput", FakeAudioInput)
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, mode=RuntimeMode.MIC, audio_device="2")
    assert app.audio_status == "Input: USB Mic"
    assert FakeAudioInput.seen == (
        "2",
        AudioConfig(
            smoothing=AudioSmoothing(noise_floor=AudioSmoothing().noise_floor),
            normalization=AudioNormalization(target_level=AudioNormalization().target_level),
        ),
    )


def test_main_lists_audio_devices_and_exits(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        "lumistripe_cli.app.list_input_device_details",
        lambda: [
            type("Device", (), {"index": 1, "name": "USB Mic"})(),
            type("Device", (), {"index": 2, "name": "Default Input"})(),
        ],
    )

    main(["--list-audio-devices"])

    captured = capsys.readouterr()
    assert "1: USB Mic" in captured.out
    assert "2: Default Input" in captured.out


def test_main_audio_debug_skips_gpio_and_runs_with_in_memory_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fail_build(args):
        del args
        raise AssertionError("GPIO controller should not be built in audio debug mode")

    def fake_run(self):
        called["controller"] = self.controller
        called["mode"] = self.mode
        called["quiet"] = self.quiet
        called["audio_debug_verbose"] = self.audio_debug_verbose

    monkeypatch.setattr("lumistripe_cli.app.build_output_controller", fail_build)
    monkeypatch.setattr("lumistripe_cli.app.HeadlessApp.run_audio_debug", fake_run)

    main(["--audio-debug", "--audio-debug-verbose", "--pixels", "16"])

    assert isinstance(called["controller"], Stripe)
    assert called["mode"] is RuntimeMode.MIC
    assert called["quiet"] is True
    assert called["audio_debug_verbose"] is True
