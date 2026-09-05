from __future__ import annotations

import subprocess
import time
from pathlib import Path

import lumistripe_web.bluetooth as bluetooth_module
import lumistripe_web.runtime as runtime_module
from lumistripe import (
    AudioFrame,
    AudioInputDevice,
    AudioInputHealth,
    AudioProcessorStats,
    AudioSource,
    MusicFeatures,
    PlaybackMode,
    Stripe,
)
from lumistripe_web.bluetooth import (
    BluetoothCommandError,
    BluetoothDevice,
    BluetoothManager,
    BluetoothStatus,
    _clean_command_output,
    _raise_for_bluetooth_failure,
    capture_device_selector,
)
from lumistripe_web.runtime import LumiStripeRuntime, RuntimeSettings
from lumistripe_web.settings import AudioTuningProfile

PHONE_ADDRESS = "AA:BB:CC:DD:EE:FF"
PHONE_SOURCE = "bluez_output.AA_BB_CC_DD_EE_FF.1.monitor"
SPEAKER_ADDRESS = "11:22:33:44:55:66"
SPEAKER_SINK = "bluez_output.11_22_33_44_55_66.1"


def _pipewire_runner(command: tuple[str, ...], timeout: float) -> str:
    del timeout
    if command == ("bluetoothctl", "show"):
        return "Controller 00:11:22:33:44:55 Pi\n\tName: rpi02\n\tAlias: LumiStripe\n\tPowered: yes\n\tDiscovering: no"
    if command == ("bluetoothctl", "devices"):
        return f"Device {PHONE_ADDRESS} Party iPhone"
    if command == ("bluetoothctl", "devices", "Connected"):
        return f"Device {PHONE_ADDRESS} Party iPhone"
    if command == ("bluetoothctl", "devices", "Paired"):
        return f"Device {PHONE_ADDRESS} Party iPhone"
    if command == ("bluetoothctl", "info", PHONE_ADDRESS):
        return f"Device {PHONE_ADDRESS}\n\tName: Party iPhone\n\tUUID: Audio Source (0000110a-0000-1000-8000-00805f9b34fb)"
    if command == ("pactl", "list", "short", "sources"):
        return f"42 {PHONE_SOURCE} PipeWire s16le 2ch 48000Hz SUSPENDED"
    if command == ("pactl", "list", "short", "sinks"):
        return "43 alsa_output.usb-speakers PipeWire s16le 2ch 48000Hz RUNNING"
    if command == ("pactl", "list", "short", "sink-inputs"):
        return ""
    if command == ("pactl", "list", "sinks"):
        return (
            "Sink #43\n"
            "\tName: alsa_output.usb-speakers\n"
            "\tDescription: USB Speakers\n"
            "\tMute: no\n"
            "\tVolume: front-left: 65536 / 100% / 0.00 dB, front-right: 65536 / 100% / 0.00 dB"
        )
    if command == ("pactl", "info"):
        return "Default Sink: alsa_output.usb-speakers\nDefault Source: alsa_input.usb-mic"
    if command[:2] == ("pactl", "set-default-source"):
        return ""
    if command[:2] in {
        ("pactl", "set-default-sink"),
        ("pactl", "set-sink-volume"),
        ("pactl", "set-sink-mute"),
        ("pactl", "move-sink-input"),
    }:
        return ""
    if command[:2] == ("bluetoothctl", "connect"):
        return "Connection successful"
    if command[:2] == ("bluetoothctl", "power"):
        return "Changing power on succeeded"
    if command[:2] == ("bluetoothctl", "system-alias"):
        return "Changing system-alias succeeded"
    if command[:2] == ("bluetoothctl", "trust"):
        return "Trust succeeded"
    if command[-2:] == ("pair", PHONE_ADDRESS):
        return "Pairing successful"
    if command[:2] == ("bluetoothctl", "remove"):
        return "Device has been removed"
    raise AssertionError(f"unexpected command: {command}")


def test_bluetooth_status_finds_phone_monitor_and_output() -> None:
    manager = BluetoothManager(
        runner=_pipewire_runner,
        command_exists=lambda command: command in {"bluetoothctl", "pactl"},
    )

    status = manager.refresh()

    assert status.available is True
    assert status.powered is True
    assert status.adapter_alias == "LumiStripe"
    assert status.streaming is True
    assert status.connected_device == BluetoothDevice(
        address=PHONE_ADDRESS,
        name="Party iPhone",
        paired=True,
        connected=True,
        roles=("input",),
    )
    assert status.input_source == PHONE_SOURCE
    assert status.default_sink == "alsa_output.usb-speakers"
    assert status.connected_inputs[0].name == "Party iPhone"
    assert status.connected_outputs == ()
    assert status.output_devices[0].name == "USB Speakers"
    assert status.output_volume == 1.0
    assert status.output_ready is True


def test_bluetooth_status_falls_back_to_sink_monitor_for_active_wpctl_stream() -> None:
    def runner(command: tuple[str, ...], timeout: float) -> str:
        if command == ("pactl", "list", "short", "sources"):
            return (
                "42 alsa_input.usb-mic PipeWire s16le 1ch 48000Hz SUSPENDED\n"
                "43 alsa_output.usb-speakers.monitor PipeWire "
                "s16le 2ch 48000Hz RUNNING"
            )
        if command == ("wpctl", "status"):
            return (
                "Audio\n"
                " └─ Streams:\n"
                "        82. bluez_input.AA_BB_CC_DD_EE_FF.2\n"
                "             85. output_FL > alsa_output.usb-speakers:playback_FL [active]\n"
                "             87. output_FR > alsa_output.usb-speakers:playback_FR [active]"
            )
        return _pipewire_runner(command, timeout)

    manager = BluetoothManager(
        runner=runner,
        command_exists=lambda command: command in {"bluetoothctl", "pactl", "wpctl"},
    )

    status = manager.refresh()

    assert status.streaming is True
    assert status.input_source == "alsa_output.usb-speakers.monitor"


def test_bluetooth_status_separates_phone_input_and_speaker_output() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], timeout: float) -> str:
        del timeout
        commands.append(command)
        if command == ("bluetoothctl", "show"):
            return "Controller 00:11:22:33:44:55 Pi\n\tAlias: LumiStripe\n\tPowered: yes"
        if command == ("bluetoothctl", "devices"):
            return (
                f"Device {PHONE_ADDRESS} Party iPhone\n"
                f"Device {SPEAKER_ADDRESS} SONY Speaker"
            )
        if command in {
            ("bluetoothctl", "devices", "Connected"),
            ("bluetoothctl", "devices", "Paired"),
        }:
            return (
                f"Device {PHONE_ADDRESS} Party iPhone\n"
                f"Device {SPEAKER_ADDRESS} SONY Speaker"
            )
        if command == ("bluetoothctl", "info", PHONE_ADDRESS):
            return "UUID: Audio Source (0000110a-0000-1000-8000-00805f9b34fb)"
        if command == ("bluetoothctl", "info", SPEAKER_ADDRESS):
            return "UUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)"
        if command == ("pactl", "list", "short", "sources"):
            return f"42 {SPEAKER_SINK}.monitor PipeWire s16le 2ch 48000Hz RUNNING"
        if command == ("pactl", "list", "short", "sinks"):
            return f"80 {SPEAKER_SINK} PipeWire s16le 2ch 48000Hz RUNNING\n43 alsa_output.usb-speakers PipeWire s16le 2ch 48000Hz IDLE"
        if command == ("pactl", "list", "short", "sink-inputs"):
            return ""
        if command == ("pactl", "list", "sinks"):
            return (
                f"Sink #80\n\tName: {SPEAKER_SINK}\n\tDescription: SONY Speaker\n"
                "\tMute: no\n\tVolume: front-left: 26214 / 40% / -23.88 dB\n"
                "Sink #43\n\tName: alsa_output.usb-speakers\n\tDescription: USB Speakers\n"
                "\tMute: no\n\tVolume: front-left: 65536 / 100% / 0.00 dB"
            )
        if command == ("pactl", "info"):
            return f"Default Sink: {SPEAKER_SINK}\nDefault Source: alsa_input.usb-mic"
        if command == ("wpctl", "status"):
            return (
                "Audio\n"
                " └─ Streams:\n"
                f"        82. bluez_input.{PHONE_ADDRESS.replace(':', '_')}.2\n"
                f"             85. output_FL > {SPEAKER_SINK}:playback_FL [active]"
            )
        if command[:2] in {
            ("pactl", "set-default-sink"),
            ("pactl", "set-sink-volume"),
            ("pactl", "set-sink-mute"),
            ("pactl", "move-sink-input"),
        }:
            return ""
        if command == ("bluetoothctl", "power", "on"):
            return "Changing power on succeeded"
        if command[:2] == ("bluetoothctl", "connect"):
            return "Connection successful"
        raise AssertionError(f"unexpected command: {command}")

    manager = BluetoothManager(
        runner=runner,
        command_exists=lambda command: command in {"bluetoothctl", "pactl", "wpctl"},
    )

    status = manager.refresh()

    assert [device.name for device in status.connected_inputs] == ["Party iPhone"]
    assert [device.name for device in status.connected_outputs] == ["SONY Speaker"]
    assert status.input_source == f"{SPEAKER_SINK}.monitor"
    assert status.default_sink == SPEAKER_SINK
    assert status.output_volume == 0.4
    assert status.output_devices[0].name == "SONY Speaker"
    assert status.output_devices[0].bluetooth is True

    manager.set_default_sink(SPEAKER_SINK)
    assert ("pactl", "set-default-sink", SPEAKER_SINK) in commands

    manager.connect(SPEAKER_ADDRESS)
    deadline = time.monotonic() + 1.0
    while manager.status().operation is not None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert ("bluetoothctl", "connect", SPEAKER_ADDRESS, "a2dp-sink") in commands

    manager.set_output_volume(SPEAKER_SINK, 1.0)
    manager.set_output_mute(SPEAKER_SINK, False)
    assert ("pactl", "set-sink-volume", SPEAKER_SINK, "100%") in commands
    assert ("pactl", "set-sink-mute", SPEAKER_SINK, "0") in commands


def test_bluetooth_status_uses_current_bluetoothctl_filter_command() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], timeout: float) -> str:
        commands.append(command)
        return _pipewire_runner(command, timeout)

    manager = BluetoothManager(
        runner=runner,
        command_exists=lambda command: command in {"bluetoothctl", "pactl"},
    )

    manager.refresh()

    assert ("bluetoothctl", "devices", "Paired") in commands
    assert ("bluetoothctl", "paired-devices") not in commands


def test_bluetooth_command_output_strips_terminal_escape_sequences() -> None:
    output = (
        "\x1b[1;39m[bluetoothctl]>\x1b[0m\n"
        "\x1b[1;39mInvalid command\x1b[0m\nUse \"help\" for a list."
    )

    assert _clean_command_output(output) == 'Invalid command\nUse "help" for a list.'


def test_bluetooth_failure_uses_error_line_instead_of_prompt() -> None:
    output = (
        "\x1b[1;39mFailed to pair: org.bluez.Error.AuthenticationCanceled\x1b[0m\n"
        "\x1b[1;39m[bluetoothctl]>\x1b[0m"
    )

    try:
        _raise_for_bluetooth_failure(output)
    except BluetoothCommandError as exc:
        assert str(exc) == "Failed to pair: org.bluez.Error.AuthenticationCanceled"
    else:
        raise AssertionError("Bluetooth failure was not detected")


def test_bluetooth_pairing_waits_for_agent_and_pairing_result(monkeypatch) -> None:
    class FakePipe:
        def __init__(self) -> None:
            self.closed = False
            self.writes: list[str] = []

        def write(self, value: str) -> int:
            self.writes.append(value)
            return len(value)

        def flush(self) -> None:
            return

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self, command: tuple[str, ...], **kwargs) -> None:
            self.command = command
            self.kwargs = kwargs
            self.stdin = FakePipe()
            self.stdout = FakePipe()
            self.returncode = 0

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    processes: list[FakeProcess] = []

    def fake_popen(command: tuple[str, ...], **kwargs) -> FakeProcess:
        process = FakeProcess(command, **kwargs)
        processes.append(process)
        return process

    def fake_read(process, output, deadline, *, expected, failure, stage) -> None:
        del process, deadline, failure, stage
        output.append(
            "Agent registered\n"
            if expected is bluetooth_module._AGENT_REGISTERED
            else "Pairing successful\n"
        )

    monkeypatch.setattr(bluetooth_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bluetooth_module, "_read_bluetoothctl_until", fake_read)

    assert (
        bluetooth_module._run_bluetoothctl_script(
            ("bluetoothctl", "--agent", "auto", "--timeout", "35"),
            "default-agent\npower on\npairable on\npair AA:BB:CC:DD:EE:FF",
            40.0,
        )
        == "Agent registered\nPairing successful"
    )

    process = processes[0]
    assert process.command == ("bluetoothctl", "--agent", "auto", "--timeout", "35")
    assert process.kwargs["stdin"] is subprocess.PIPE
    assert process.kwargs["stderr"] is subprocess.STDOUT
    assert process.stdin.writes == [
        "default-agent\npower on\npairable on\npair AA:BB:CC:DD:EE:FF\n",
        "quit\n",
    ]
    assert process.stdin.closed is True
    assert process.stdout.closed is True


def test_bluetooth_pairing_runs_in_background() -> None:
    commands: list[tuple[str, ...]] = []
    scripts: list[tuple[tuple[str, ...], str]] = []

    def runner(command: tuple[str, ...], timeout: float) -> str:
        commands.append(command)
        return _pipewire_runner(command, timeout)

    def script_runner(command: tuple[str, ...], script: str, timeout: float) -> str:
        del timeout
        scripts.append((command, script))
        return "Pairing successful"

    manager = BluetoothManager(
        runner=runner,
        script_runner=script_runner,
        command_exists=lambda command: command in {"bluetoothctl", "pactl"},
    )

    status = manager.pair(PHONE_ADDRESS)
    assert status.operation in {None, f"pairing:{PHONE_ADDRESS}"}

    deadline = time.monotonic() + 1.0
    while manager.status().operation is not None:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert scripts == [
        (
            ("bluetoothctl", "--agent", "auto", "--timeout", "35"),
            f"default-agent\npower on\npairable on\npair {PHONE_ADDRESS}",
        )
    ]
    assert ("bluetoothctl", "trust", PHONE_ADDRESS) in commands
    assert ("bluetoothctl", "connect", PHONE_ADDRESS) not in commands
    assert manager.status().error is None


def test_bluetooth_connect_targets_phone_a2dp_source() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], timeout: float) -> str:
        commands.append(command)
        return _pipewire_runner(command, timeout)

    manager = BluetoothManager(
        runner=runner,
        command_exists=lambda command: command in {"bluetoothctl", "pactl"},
    )

    manager.connect(PHONE_ADDRESS)

    deadline = time.monotonic() + 1.0
    while manager.status().operation is not None:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert ("bluetoothctl", "connect", PHONE_ADDRESS, "a2dp-source") in commands
    assert manager.status().error is None


def test_bluetooth_power_and_alias_operations_run_in_background() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], timeout: float) -> str:
        del timeout
        commands.append(command)
        return _pipewire_runner(command, timeout=0.0)

    manager = BluetoothManager(
        runner=runner,
        command_exists=lambda command: command in {"bluetoothctl", "pactl"},
    )

    manager.set_power(False)
    deadline = time.monotonic() + 1.0
    while manager.status().operation is not None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert ("bluetoothctl", "power", "off") in commands
    assert manager.status().error is None

    manager.set_alias("Party Wagon")
    deadline = time.monotonic() + 1.0
    while manager.status().operation is not None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert ("bluetoothctl", "system-alias", "Party Wagon") in commands
    assert manager.status().error is None


def test_bluetooth_rejects_blank_alias() -> None:
    manager = BluetoothManager(enabled=True)
    try:
        manager.set_alias("   ")
    except BluetoothCommandError as exc:
        assert "cannot be empty" in str(exc)
    else:
        raise AssertionError("blank Bluetooth name was accepted")


def test_bluetooth_rejects_invalid_addresses() -> None:
    manager = BluetoothManager(enabled=True)
    try:
        manager.connect("not-an-address")
    except BluetoothCommandError as exc:
        assert "Bluetooth address" in str(exc)
    else:
        raise AssertionError("invalid Bluetooth address was accepted")


def test_capture_selector_prefers_pipewire_compatibility_host() -> None:
    devices = [
        AudioInputDevice(index=1, name="jack"),
        AudioInputDevice(index=2, name="pipewire"),
        AudioInputDevice(index=3, name="pulse"),
    ]

    assert capture_device_selector(devices) == "pulse"


class FakeAudioInput:
    def __init__(self, name: str) -> None:
        self._name = name
        self.closed = False
        self.sequence = 0

    def read(self) -> AudioFrame:
        self.sequence += 1
        return AudioFrame(
            rms=0.3,
            bands=(0.2,) * 8,
            sequence=self.sequence,
            timestamp=time.monotonic(),
            fresh=True,
        )

    def read_features(self) -> MusicFeatures:
        return MusicFeatures(energy=0.3, onset_strength=0.2, silence=False)

    def health(self) -> AudioInputHealth:
        return AudioInputHealth(
            callback_count=self.sequence,
            last_callback_age=0.0,
            last_frame_age=0.0,
            processor=AudioProcessorStats(input_rms=0.2),
        )

    def device_name(self) -> str:
        return self._name

    def reconfigure(self, config) -> None:
        del config

    def close(self) -> None:
        self.closed = True


class FakeBluetoothManager:
    def __init__(self) -> None:
        self.current = BluetoothStatus(
            available=True,
            powered=True,
            devices=(
                BluetoothDevice(
                    address=PHONE_ADDRESS,
                    name="Party iPhone",
                    paired=True,
                    connected=True,
                ),
            ),
            connected_device=BluetoothDevice(
                address=PHONE_ADDRESS,
                name="Party iPhone",
                paired=True,
                connected=True,
            ),
            input_source=PHONE_SOURCE,
            default_sink="alsa_output.usb-speakers",
            output_ready=True,
        )
        self.defaults: list[str] = []
        self.restored: list[str] = []

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def status(self) -> BluetoothStatus:
        return self.current

    def default_source(self) -> str:
        return "alsa_input.usb-mic"

    def set_default_source(self, source: str) -> None:
        self.defaults.append(source)

    def restore_default_source(self, source: str | None) -> None:
        if source is not None:
            self.restored.append(source)


def test_auto_source_switches_between_bluetooth_and_microphone(
    monkeypatch, tmp_path: Path
) -> None:
    bluetooth = FakeBluetoothManager()
    monkeypatch.setattr(
        runtime_module,
        "list_input_device_details",
        lambda: [
            AudioInputDevice(index=2, name="USB Mic"),
            AudioInputDevice(index=7, name="pulse"),
        ],
    )
    opened: list[str | None] = []

    def audio_factory(device, config):
        del config
        opened.append(device)
        return FakeAudioInput("pulse" if device == "pulse" else "USB Mic")

    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=4,
            audio_source="auto",
            audio_device="2",
            settings_file=tmp_path / "settings.json",
        ),
        controller_factory=lambda settings: Stripe(settings.pixels),
        audio_factory=audio_factory,
        bluetooth_manager=bluetooth,
    )
    runtime.start()
    try:
        assert runtime.audio_settings().active_source == AudioSource.BLUETOOTH.value
        assert runtime.audio_settings().active_device_name == "Party iPhone"
        assert opened == ["pulse"]
        assert bluetooth.defaults == [PHONE_SOURCE]

        runtime.set_mode(PlaybackMode.DYNAMIC).result(timeout=1)
        runtime.apply_audio_settings("2", AudioTuningProfile(target_level=0.5)).result(
            timeout=1
        )
        assert opened == ["pulse"]

        bluetooth.current = BluetoothStatus(available=True, powered=True)
        runtime._next_audio_route_check_at = 0.0
        deadline = time.monotonic() + 1.5
        while runtime.audio_settings().active_source != AudioSource.MIC.value:
            assert time.monotonic() < deadline
            time.sleep(0.02)

        assert opened[-1] == "USB Mic"
        assert bluetooth.restored == ["alsa_input.usb-mic"]
    finally:
        runtime.stop()
