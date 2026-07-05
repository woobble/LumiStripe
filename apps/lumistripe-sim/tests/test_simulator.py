import pytest

from lumistripe import (
    AnimationClass,
    AudioConfig,
    AudioFrame,
    AudioNormalization,
    AudioSmoothing,
    MusicDrivenSelector,
    MusicFeatures,
    MusicSelectorConfig,
)
from lumistripe_sim.simulator import (
    MIN_FRAME_SECONDS,
    SimulatorApp,
    SimulatorMode,
    build_parser,
    demo_frame,
    layout_controls,
    main,
    pixel_pitch,
    window_size,
)


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


def test_handle_click_changes_animation() -> None:
    app = SimulatorApp(pixel_count=12)
    first = app.player.current_index()
    app.handle_click(app.controls.next.x + 1, app.controls.next.y + 1)
    assert app.player.current_index() != first


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
    text = app.analysis_text()
    assert "RMS: 0.500" in text
    assert "BEAT: YES" in text
    assert "BPM" in text


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


def test_step_returns_minimum_frame_time() -> None:
    app = SimulatorApp(pixel_count=12)
    delay = app.step()
    assert delay >= MIN_FRAME_SECONDS


def test_parser_accepts_audio_device_string() -> None:
    parser = build_parser()
    args = parser.parse_args(["--audio-device", "2"])
    assert args.audio_device == "2"


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
