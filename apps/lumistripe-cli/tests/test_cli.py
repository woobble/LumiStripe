import json
from pathlib import Path

import pytest

from lumistripe import (
    AnimationClass,
    AutoSelectorConfig,
    AudioAnalysis,
    AudioCalibrationResult,
    AudioConfig,
    AudioFrame,
    AudioInputHealth,
    AudioNormalization,
    AudioProcessorStats,
    AudioSnapshot,
    AudioSmoothing,
    MultiController,
    MusicDrivenSelector,
    MusicFeatures,
    MusicSelectorConfig,
    Stripe,
)
from lumistripe_cli.app import (
    AudioDebugRecorder,
    MIN_FRAME_SECONDS,
    HeadlessApp,
    RuntimeMode,
    analyze_audio_debug,
    build_runtime_encoder_backend,
    build_output_controller,
    build_parser,
    demo_frame,
    gpio_backend_label,
    main,
)
from lumistripe_cli.encoder import ControlEvent, NullEncoderBackend


class FakeGPIOStripe(Stripe):
    gpio_backend_label = "fake-gpio"

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


def test_parser_accepts_encoder_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--encoder-chip",
            "/dev/gpiochip4",
            "--encoder1-a",
            "5",
            "--encoder1-b",
            "6",
            "--encoder1-button",
            "13",
        ]
    )
    assert args.encoder_chip == "/dev/gpiochip4"
    assert args.encoder1_a == 5
    assert args.encoder1_b == 6
    assert args.encoder1_button == 13


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


def test_parser_accepts_audio_debug_recording_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--audio-debug",
            "--audio-debug-record",
            "captures/song.jsonl",
            "--audio-debug-duration",
            "60",
            "--audio-debug-label",
            "test song",
            "--analyze-audio-debug",
            "captures/song.jsonl",
        ]
    )
    assert args.audio_debug_record == Path("captures/song.jsonl")
    assert args.audio_debug_duration == pytest.approx(60.0)
    assert args.audio_debug_label == "test song"
    assert args.analyze_audio_debug == [Path("captures/song.jsonl")]


def test_parser_accepts_dj_mode_and_selector_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--mode",
            "dj",
            "--debug-selector",
            "--dj-min-duration",
            "3",
            "--dj-max-duration",
            "30",
            "--dj-seed",
            "7",
        ]
    )
    assert args.mode is RuntimeMode.DJ
    assert args.debug_selector is True
    assert args.dj_min_duration == pytest.approx(3.0)
    assert args.dj_max_duration == pytest.approx(30.0)
    assert args.dj_seed == 7


def test_parser_accepts_audio_calibration_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(["--calibrate-audio", "2.5", "--auto-calibrate-audio", "3"])
    assert args.calibrate_audio == pytest.approx(2.5)
    assert args.auto_calibrate_audio == pytest.approx(3.0)


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
            "--write-mic-profile",
            "profile.json",
            "--idle-enter-frames",
            "42",
            "--idle-threshold-scale",
            "1.5",
        ]
    )
    assert args.mic_target_level == pytest.approx(0.5)
    assert args.mic_noise_floor == pytest.approx(0.01)
    assert args.mic_profile == "pcm2902"
    assert args.write_mic_profile == Path("profile.json")
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
    with pytest.raises(SystemExit):
        parser.parse_args(["--calibrate-audio", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--auto-calibrate-audio", "0"])


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


def test_gpio_backend_label_reports_single_and_multi_controller() -> None:
    primary = FakeGPIOStripe(None, 4)
    secondary = FakeGPIOStripe(None, 4)
    assert gpio_backend_label(primary) == "fake-gpio"
    assert gpio_backend_label(MultiController([primary, secondary])) == "fake-gpio, fake-gpio"
    assert gpio_backend_label(Stripe(4)) is None


def test_build_output_controller_rejects_partial_second_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lumistripe_cli.app.GPIOStripe", FakeGPIOStripe)
    monkeypatch.setattr("lumistripe_cli.app._ensure_gpio_ready", lambda chip: None)
    args = build_parser().parse_args(["--pixels", "16", "--data-pin-2", "16"])
    with pytest.raises(ValueError, match="secondary stripe"):
        build_output_controller(args)


def test_build_runtime_encoder_backend_returns_null_when_unconfigured() -> None:
    args = build_parser().parse_args([])
    backend = build_runtime_encoder_backend(args)
    assert isinstance(backend, NullEncoderBackend)


def test_build_runtime_encoder_backend_rejects_partial_encoder() -> None:
    args = build_parser().parse_args(["--encoder1-a", "5"])
    with pytest.raises(ValueError, match="encoder1 requires all"):
        build_runtime_encoder_backend(args)


def test_build_runtime_encoder_backend_uses_encoder_chip(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("lumistripe_cli.app._ensure_gpio_input_ready", lambda chip: calls.append(chip))
    sentinel = object()
    monkeypatch.setattr("lumistripe_cli.app.build_encoder_backend", lambda chip, **kwargs: sentinel)
    args = build_parser().parse_args(
        [
            "--encoder-chip",
            "/dev/gpiochip5",
            "--encoder1-a",
            "5",
            "--encoder1-b",
            "6",
            "--encoder1-button",
            "13",
        ]
    )
    backend = build_runtime_encoder_backend(args)
    assert backend is sentinel
    assert calls == ["/dev/gpiochip5"]


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
        bass_energy=0.15,
        mid_energy=0.40,
        treble_energy=0.70,
        spectral_centroid=0.62,
        spectral_flux=0.31,
        rolling_loudness=0.44,
        drop_detected=True,
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
    assert "OUT: 1.00" in block


def test_headless_app_status_block_includes_gpio_backend_when_present() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True, gpio_backend_label="gpiomem")
    block = app._status_block()
    assert "GPIO: gpiomem" in block


def test_headless_app_status_block_includes_mic_tuning_in_mic_mode() -> None:
    app = HeadlessApp(
        controller=Stripe(12),
        pixel_count=12,
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


def test_headless_app_encoder_controls_update_runtime_state() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, animation_name="pulse", quiet=True)
    initial_name = app.player.name_at(app.player.current_index())

    app.apply_control_event(ControlEvent(kind="rotate", source="encoder2", value=-3))
    assert app.player.brightness == pytest.approx(0.85)

    app.apply_control_event(ControlEvent(kind="press", source="encoder2"))
    assert "AUTO: OFF" in app._status_block()

    app.apply_control_event(ControlEvent(kind="press", source="encoder1"))
    assert app.mode is RuntimeMode.DEMO

    app.apply_control_event(ControlEvent(kind="rotate", source="encoder1", value=1))
    assert app.player.name_at(app.player.current_index()) != initial_name
    assert "AUTO: OFF" in app._status_block()


def test_headless_app_run_consumes_encoder_events() -> None:
    class FakeEncoderBackend:
        def __init__(self) -> None:
            self.closed = False
            self.calls = 0

        def read_events(self) -> list[ControlEvent]:
            self.calls += 1
            if self.calls == 1:
                return [ControlEvent(kind="rotate", source="encoder2", value=-2)]
            return []

        def close(self) -> None:
            self.closed = True

    backend = FakeEncoderBackend()
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True, encoder_backend=backend)
    app.run(frame_limit=1)
    assert app.player.brightness == pytest.approx(0.9)
    assert backend.closed is True


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
        bass_energy=0.15,
        mid_energy=0.40,
        treble_energy=0.70,
        spectral_centroid=0.62,
        spectral_flux=0.31,
        rolling_loudness=0.44,
        drop_detected=True,
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
    assert "BASS=0.15" in line
    assert "MID=0.40" in line
    assert "TREBLE=0.70" in line
    assert "CENTROID=0.62" in line
    assert "FLUX=0.31" in line
    assert "LOUD=0.44" in line
    assert "SILENCE=NO" in line
    assert "DROP=YES" in line
    assert "SECTION=NO" in line
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


def test_headless_app_debug_log_line_includes_audio_health() -> None:
    class FakeAudioInput:
        def health(self) -> AudioInputHealth:
            return AudioInputHealth(
                callback_count=3,
                status_count=1,
                last_frame_age=0.042,
                processor=AudioProcessorStats(fft_count=9, normalization_gain=1.7),
            )

    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    app.mode = RuntimeMode.MIC
    app.audio_input = FakeAudioInput()
    app.audio_frame = AudioFrame(sequence=4, fresh=True)
    app.audio_snapshot = AudioSnapshot.from_parts(
        AudioFrame(
            rms=0.5,
            bands=(0.4, 0.5, 0.3, 0.3, 0.3, 0.2, 0.2, 0.2),
            beat=True,
            beat_strength=0.8,
            sequence=4,
            fresh=True,
        )
    )
    app.music_features = app.audio_snapshot.features

    line = app.debug_log_line(1.25)

    assert "FRESH=YES" in line
    assert "AGE=0.042" in line
    assert "SEQ=4" in line
    assert "FFT=9" in line
    assert "GAIN=1.70" in line
    assert "STATUS=1" in line


def test_headless_app_audio_debug_record_includes_structured_metrics() -> None:
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, quiet=True)
    app.mode = RuntimeMode.MIC
    app.audio_status = "Input: Fake Mic"
    app.audio_frame = AudioFrame(
        rms=0.5,
        bands=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8),
        beat=True,
        beat_strength=0.9,
        sequence=9,
        fresh=True,
    )
    app.music_features = MusicFeatures(
        bpm=128.0,
        energy=0.5,
        bass_energy=0.15,
        mid_energy=0.40,
        treble_energy=0.70,
        spectral_centroid=0.62,
        spectral_flux=0.31,
        drop_detected=True,
    )

    row = app.audio_debug_record(1.25)

    assert row["elapsed_s"] == pytest.approx(1.25)
    assert row["source"] == "Input: Fake Mic"
    assert row["mode"] == "mic"
    assert row["animation"]
    assert row["fresh"] is True
    assert row["sequence"] == 9
    assert row["rms"] == pytest.approx(0.5)
    assert row["beat"] is True
    assert row["bpm"] == pytest.approx(128.0)
    assert row["bass_energy"] == pytest.approx(0.15)
    assert row["bands"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    assert row["drop_detected"] is True
    assert isinstance(row["selector_scores"], dict)


def test_audio_debug_recorder_writes_jsonl_with_label(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"

    with AudioDebugRecorder(path, label="test song") as recorder:
        recorder.write({"elapsed_s": 1.0, "rms": 0.2})

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row == {"label": "test song", "elapsed_s": 1.0, "rms": 0.2}


def test_analyze_audio_debug_reports_recommended_flags_and_warnings(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    idle_rows = [
        {
            "label": "room-idle",
            "rms": 0.001,
            "energy": 0.001,
            "rolling_loudness": 0.001,
            "fresh": True,
            "beat": False,
            "drop_detected": False,
            "section_change": False,
            "silence": True,
            "normalization_gain": 2.0,
            "bpm": 120,
        }
        for _ in range(8)
    ]
    active_rows = [
        {
            "label": "get-lucky",
            "rms": 0.22,
            "energy": 0.30,
            "rolling_loudness": 0.24,
            "fresh": True,
            "beat": True,
            "drop_detected": index == 0,
            "section_change": False,
            "silence": False,
            "normalization_gain": 5.0,
            "bpm": 118,
            "bass_energy": 0.7,
            "mid_energy": 0.3,
            "treble_energy": 0.04,
            "spectral_flux": 0.05,
        }
        for index in range(12)
    ]
    rows = idle_rows + active_rows
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    text = analyze_audio_debug([path])

    assert "Audio debug rows: 20" in text
    assert "Labels: get-lucky, room-idle" in text
    assert "Classified labels: active=1 idle=1 quiet=0" in text
    assert "room-idle: class=idle" in text
    assert "get-lucky: class=active" in text
    assert "--mic-noise-floor" in text
    assert "--mic-target-level" in text
    assert "--idle-threshold-scale" in text
    assert "active-capture normalization gain is often near maximum" in text


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
                sequence=1,
                fresh=True,
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


def test_headless_app_dj_mode_uses_dj_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAudioInput:
        def read(self) -> AudioFrame:
            return AudioFrame(
                rms=0.95,
                bands=(0.9, 0.85, 0.75, 0.72, 0.7, 0.88, 0.9, 0.92),
                beat=True,
                beat_strength=1.0,
                sequence=1,
                fresh=True,
            )

        def read_features(self) -> MusicFeatures:
            return MusicFeatures(
                bpm=128.0,
                bpm_confidence=0.8,
                energy=0.95,
                volume=0.95,
                energy_level=0.95,
                bass=0.9,
                bass_energy=0.9,
                mid_energy=0.3,
                treble_energy=0.2,
                beat=True,
                beat_strength=0.9,
                beat_confidence=0.9,
                drop_detected=True,
            )

        def device_name(self) -> str:
            return "Fake Mic"

        def close(self) -> None:
            return None

        @classmethod
        def with_config(cls, config):
            del config
            return cls()

    monkeypatch.setattr("lumistripe_cli.app.AudioInput", FakeAudioInput)
    app = HeadlessApp(
        controller=Stripe(12),
        pixel_count=12,
        mode=RuntimeMode.DJ,
        auto_selector_config=AutoSelectorConfig(randomness=0.0, min_duration_s=0.1, switch_cooldown_s=0.1),
        quiet=True,
    )

    app.step()
    assert app.dj_selector is not None
    app.dj_selector.last_switch_at_s -= 1.0
    app.step()

    assert app.selector is None
    assert app.dj_selector.last_decision.scores
    assert app.class_label == "DJ"
    assert "TOP=" in app.debug_log_line(1.0)


def test_headless_app_mic_mode_stale_audio_updates_selector_with_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAudioInput:
        def read(self) -> AudioFrame:
            return AudioFrame(
                rms=0.95,
                bands=(0.9, 0.85, 0.75, 0.72, 0.7, 0.88, 0.9, 0.92),
                beat=True,
                beat_strength=1.0,
                sequence=1,
                fresh=False,
            )

        def read_features(self) -> MusicFeatures:
            raise AssertionError("stale audio features should not be read")

        def device_name(self) -> str:
            return "Fake Mic"

        def close(self) -> None:
            return None

        @classmethod
        def with_config(cls, config):
            del config
            return cls()

    monkeypatch.setattr("lumistripe_cli.app.AudioInput", FakeAudioInput)
    app = HeadlessApp(controller=Stripe(12), pixel_count=12, mode=RuntimeMode.MIC)

    app.step()

    assert app.music_features == MusicFeatures()
    assert app.audio_frame.fresh is False
    assert app.audio_snapshot.features == MusicFeatures()
    assert app._mic_snapshot() == AudioFrame(sequence=1, fresh=False)


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


def test_main_calibrate_audio_prints_recommended_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = AudioCalibrationResult(
        duration=2.0,
        samples=5,
        measured_floor=0.01,
        measured_peak=0.2,
        recommended_noise_floor=0.02,
        recommended_target_level=0.3,
        recommended_idle_threshold_scale=1.4,
    )
    seen: dict[str, object] = {}

    def fake_calibrate(*, duration: float, device_pattern: str | None = None):
        seen["duration"] = duration
        seen["device_pattern"] = device_pattern
        return result

    monkeypatch.setattr("lumistripe_cli.app.calibrate_audio_input", fake_calibrate)

    main(["--calibrate-audio", "2", "--audio-device", "usb"])

    captured = capsys.readouterr()
    assert seen == {"duration": 2.0, "device_pattern": "usb"}
    assert "--mic-noise-floor 0.0200" in captured.out
    assert "--mic-target-level 0.300" in captured.out
    assert "--idle-threshold-scale 1.40" in captured.out


def test_main_analyzes_audio_debug_and_exits(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    called: dict[str, object] = {}

    def fake_analyze(paths: list[Path]) -> str:
        called["paths"] = paths
        return "analysis output"

    monkeypatch.setattr("lumistripe_cli.app.analyze_audio_debug", fake_analyze)

    main(["--analyze-audio-debug", "capture.jsonl"])

    captured = capsys.readouterr()
    assert called["paths"] == [Path("capture.jsonl")]
    assert "analysis output" in captured.out


def _fake_audio_config(*args: object, **kwargs: object) -> object:
    class _FakeAudioInput:
        _device_name = "Fake Mic"

        def device_name(self) -> str:
            return self._device_name

        def read(self) -> AudioFrame:
            return AudioFrame()

        def read_features(self) -> MusicFeatures:
            return MusicFeatures()

        def close(self) -> None:
            pass

        def __enter__(self) -> "_FakeAudioInput":
            return self

        def __exit__(self, *exc: object) -> None:
            pass

    return _FakeAudioInput()


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
    monkeypatch.setattr("lumistripe_cli.app.AudioInput.with_config", _fake_audio_config)

    main(["--audio-debug", "--audio-debug-verbose", "--pixels", "16"])

    assert isinstance(called["controller"], Stripe)
    assert called["mode"] is RuntimeMode.MIC
    assert called["quiet"] is True
    assert called["audio_debug_verbose"] is True


def test_main_applies_auto_mic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr(
        "lumistripe_cli.app.list_input_device_details",
        lambda: [type("Device", (), {"index": 1, "name": "Texas Instruments PCM2902 Audio Codec"})()],
    )
    monkeypatch.setattr("lumistripe_cli.app.AudioInput.with_config", _fake_audio_config)

    def fake_run(self):
        called["target"] = self.mic_target_level
        called["noise"] = self.mic_noise_floor
        called["scale"] = self.idle_threshold_scale
        called["analysis"] = self.audio_analysis

    monkeypatch.setattr("lumistripe_cli.app.HeadlessApp.run_audio_debug", fake_run)

    main(["--audio-debug", "--mic-profile", "auto"])

    assert called["target"] == pytest.approx(0.403)
    assert called["noise"] == pytest.approx(0.0137)
    assert called["scale"] == pytest.approx(0.92)
    assert isinstance(called["analysis"], AudioAnalysis)


def test_main_explicit_mic_flags_override_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr("lumistripe_cli.app.AudioInput.with_config", _fake_audio_config)
    monkeypatch.setattr(
        "lumistripe_cli.app.HeadlessApp.run_audio_debug",
        lambda self: called.update({"noise": self.mic_noise_floor, "target": self.mic_target_level}),
    )

    main(["--audio-debug", "--mic-profile", "pcm2902", "--mic-noise-floor", "0.02"])

    assert called["noise"] == pytest.approx(0.02)
    assert called["target"] == pytest.approx(0.403)


def test_main_writes_effective_mic_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "profile.json"

    def unavailable_devices() -> list[object]:
        raise RuntimeError("sounddevice is required for AudioInput; install lumistripe-core[audio]")

    monkeypatch.setattr("lumistripe_cli.app.list_input_device_details", unavailable_devices)
    main(["--mic-profile", "pcm2902", "--mic-target-level", "0.5", "--write-mic-profile", str(path)])

    data = json.loads(path.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert "Wrote mic profile" in captured.out
    assert data["mic_noise_floor"] == pytest.approx(0.0137)
    assert data["mic_target_level"] == pytest.approx(0.5)
    assert data["idle_threshold_scale"] == pytest.approx(0.92)


def test_main_writes_effective_mic_profile_with_audio_devices(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "profile.json"

    monkeypatch.setattr(
        "lumistripe_cli.app.list_input_device_details",
        lambda: [type("Device", (), {"index": 1, "name": "USB Mic"})()],
    )

    main(["--mic-profile", "pcm2902", "--mic-target-level", "0.5", "--write-mic-profile", str(path)])

    data = json.loads(path.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert "Wrote mic profile" in captured.out
    assert data["mic_noise_floor"] == pytest.approx(0.0137)
    assert data["mic_target_level"] == pytest.approx(0.5)
    assert data["idle_threshold_scale"] == pytest.approx(0.92)
    assert data["device_patterns"] == ["USB Mic"]


def test_main_auto_calibrate_audio_applies_runtime_tuning(monkeypatch: pytest.MonkeyPatch) -> None:
    result = AudioCalibrationResult(
        duration=3.0,
        samples=7,
        measured_floor=0.01,
        measured_peak=0.25,
        recommended_noise_floor=0.025,
        recommended_target_level=0.42,
        recommended_idle_threshold_scale=1.8,
    )
    called: dict[str, object] = {}

    monkeypatch.setattr("lumistripe_cli.app.calibrate_audio_input", lambda **kwargs: result)
    monkeypatch.setattr("lumistripe_cli.app.AudioInput.with_config", _fake_audio_config)

    def fake_run(self):
        called["target"] = self.mic_target_level
        called["noise"] = self.mic_noise_floor
        called["scale"] = self.idle_threshold_scale
        called["calibration"] = self.audio_calibration

    monkeypatch.setattr("lumistripe_cli.app.HeadlessApp.run_audio_debug", fake_run)

    main(["--audio-debug", "--auto-calibrate-audio", "3"])

    assert called["target"] == pytest.approx(0.42)
    assert called["noise"] == pytest.approx(0.025)
    assert called["scale"] == pytest.approx(1.8)
    assert called["calibration"] is result
