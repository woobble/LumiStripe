import pytest

from lumistripe import AudioFrame
from lumistripe_app.simulator import (
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
    app.set_mode(SimulatorMode.DEMO)
    assert app.mode is SimulatorMode.DEMO
    assert app.audio_status == "Using internal demo beat."
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
        def new(cls) -> AudioFrame:
            raise RuntimeError("no audio input device available")

    monkeypatch.setattr("lumistripe_app.simulator.AudioInput", BrokenAudioInput)
    app = SimulatorApp(pixel_count=12, mode=SimulatorMode.MIC)
    assert app.mode is SimulatorMode.MANUAL
    assert app.audio_error == "no audio input device available"
    assert app.audio_status == "Microphone unavailable."


def test_mic_mode_uses_music_selector(monkeypatch) -> None:
    from lumistripe import MusicFeatures

    class FakeAudioInput:
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
        def new(cls):
            return cls()

    monkeypatch.setattr("lumistripe_app.simulator.AudioInput", FakeAudioInput)
    app = SimulatorApp(pixel_count=12, mode=SimulatorMode.MIC)
    for _ in range(140):
        app.step()
    assert app.class_label in ("FAST_PARTY", "CHAOTIC")
    assert app.audio_status == "Input: Fake Mic"


def test_step_returns_minimum_frame_time() -> None:
    app = SimulatorApp(pixel_count=12)
    delay = app.step()
    assert delay >= MIN_FRAME_SECONDS


def test_parser_accepts_audio_device_string() -> None:
    parser = build_parser()
    args = parser.parse_args(["--audio-device", "2"])
    assert args.audio_device == "2"


def test_main_lists_audio_devices_and_exits(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        "lumistripe_app.simulator.list_input_device_details",
        lambda: [
            type("Device", (), {"index": 1, "name": "USB Mic"})(),
            type("Device", (), {"index": 2, "name": "Default Input"})(),
        ],
    )

    main(["--list-audio-devices"])

    captured = capsys.readouterr()
    assert "1: USB Mic" in captured.out
    assert "2: Default Input" in captured.out
