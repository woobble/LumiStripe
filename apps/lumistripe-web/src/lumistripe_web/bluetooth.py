from __future__ import annotations

import logging
import os
import re
import selectors
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BLUETOOTH_ADDRESS = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_DEVICE_LINE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\s*(.*)$")
_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_AGENT_REGISTERED = re.compile(r"\bagent registered\b", re.IGNORECASE)
_AGENT_FAILURE = re.compile(
    r"failed to (?:register agent(?: object)?|call register agent)|"
    r"agent registration failed",
    re.IGNORECASE,
)
_PAIRING_SUCCESS = re.compile(r"\bpairing successful\b", re.IGNORECASE)
_PAIRING_FAILURE = re.compile(
    r"failed to pair|failed to request default agent|"
    r"failed to set (?:power|pairable)|no agent (?:available|is registered)|"
    r"authentication (?:failed|canceled)|operation not permitted",
    re.IGNORECASE,
)


class BluetoothCommandError(RuntimeError):
    """A Bluetooth or PipeWire command could not be completed."""


@dataclass(frozen=True, slots=True)
class BluetoothDevice:
    address: str
    name: str
    paired: bool = False
    connected: bool = False


@dataclass(frozen=True, slots=True)
class BluetoothStatus:
    available: bool = False
    powered: bool = False
    scanning: bool = False
    devices: tuple[BluetoothDevice, ...] = ()
    connected_device: BluetoothDevice | None = None
    input_source: str | None = None
    default_sink: str | None = None
    output_ready: bool = False
    operation: str | None = None
    error: str | None = None

    @property
    def streaming(self) -> bool:
        return self.connected_device is not None and self.input_source is not None


Runner = Callable[[tuple[str, ...], float], str]
ScriptRunner = Callable[[tuple[str, ...], str, float], str]


class BluetoothManager:
    """Best-effort BlueZ and PipeWire bridge for a headless Pi.

    BlueZ owns pairing and A2DP reception. PipeWire/WirePlumber routes the
    incoming A2DP stream to the configured default speaker sink and exposes a
    monitor source. LumiStripe selects that monitor as its analysis input by
    temporarily making it the default capture source for the PipeWire/Pulse
    client used by sounddevice. Some WirePlumber versions expose the phone
    only as an active ``bluez_input`` stream, not as a Pulse source row; in
    that case the default sink monitor is used as the analysis input.

    The manager deliberately talks to the platform tools instead of importing
    a DBus binding. That keeps Bluetooth optional for simulation and makes the
    web package installable on development machines without a system DBus
    library. The Pi provisioning guide installs the required tools.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        poll_interval: float = 1.0,
        runner: Runner | None = None,
        script_runner: ScriptRunner | None = None,
        command_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.enabled = enabled
        self._poll_interval = max(0.25, poll_interval)
        self._runner = runner or _run_command
        self._script_runner = script_runner
        self._command_exists = command_exists or (lambda command: shutil.which(command) is not None)
        self._lock = threading.RLock()
        self._status = BluetoothStatus()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._operation: str | None = None
        self._operation_error: str | None = None

    def start(self) -> None:
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="lumistripe-bluetooth",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(2.0, self._poll_interval + 0.5))
        self._thread = None

    def status(self) -> BluetoothStatus:
        with self._lock:
            return self._status

    def refresh(self) -> BluetoothStatus:
        """Refresh status synchronously, primarily for API actions and tests."""
        if not self.enabled:
            return self.status()
        self._refresh_once()
        return self.status()

    def start_scan(self) -> BluetoothStatus:
        self._start_operation("scanning", self._scan)
        return self.status()

    def pair(self, address: str) -> BluetoothStatus:
        normalized = _validate_address(address)
        self._start_operation(f"pairing:{normalized}", lambda: self._pair(normalized))
        return self.status()

    def connect(self, address: str) -> BluetoothStatus:
        normalized = _validate_address(address)
        self._start_operation(f"connecting:{normalized}", lambda: self._connect(normalized))
        return self.status()

    def forget(self, address: str) -> BluetoothStatus:
        normalized = _validate_address(address)
        self._start_operation(f"forgetting:{normalized}", lambda: self._forget(normalized))
        return self.status()

    def default_source(self) -> str | None:
        try:
            output = self._run(("pactl", "info"), timeout=2.0)
        except BluetoothCommandError:
            return None
        for line in output.splitlines():
            if line.lower().startswith("default source:"):
                value = line.split(":", 1)[1].strip()
                return value or None
        return None

    def set_default_source(self, source: str) -> None:
        self._run(("pactl", "set-default-source", source), timeout=2.0)

    def restore_default_source(self, source: str | None) -> None:
        if not source:
            return
        try:
            self.set_default_source(source)
        except BluetoothCommandError as exc:
            logger.warning("could not restore PipeWire default source %r: %s", source, exc)

    def _poll_loop(self) -> None:
        self._refresh_once()
        while not self._stop_event.wait(self._poll_interval):
            self._refresh_once()

    def _refresh_once(self) -> None:
        missing_commands = tuple(
            command
            for command in ("bluetoothctl", "pactl")
            if not self._command_exists(command)
        )
        if missing_commands:
            self._set_status(
                BluetoothStatus(
                    available=False,
                    operation=self._operation,
                    error=f"{', '.join(missing_commands)} is not installed.",
                )
            )
            return

        controller_output = ""
        try:
            controller_output = self._run(("bluetoothctl", "show"), timeout=2.0)
            devices_output = self._run(("bluetoothctl", "devices"), timeout=2.0)
            connected_output = self._run(
                ("bluetoothctl", "devices", "Connected"), timeout=2.0
            )
            paired_output = self._run(
                ("bluetoothctl", "devices", "Paired"), timeout=2.0
            )
            sources = self._run(("pactl", "list", "short", "sources"), timeout=2.0)
            sinks = self._run(("pactl", "list", "short", "sinks"), timeout=2.0)
            info = self._run(("pactl", "info"), timeout=2.0)
        except BluetoothCommandError as exc:
            previous = self.status()
            self._set_status(
                BluetoothStatus(
                    available=True,
                    powered=_parse_bool_property(controller_output, "Powered"),
                    scanning=_parse_bool_property(controller_output, "Discovering")
                    or self._operation == "scanning",
                    devices=previous.devices,
                    connected_device=previous.connected_device,
                    input_source=previous.input_source,
                    default_sink=previous.default_sink,
                    output_ready=previous.output_ready,
                    operation=self._operation,
                    error=str(exc),
                )
            )
            return

        # On current Raspberry Pi OS/WirePlumber combinations an A2DP
        # receiver can be shown by wpctl as an active bluez_input stream while
        # pactl exposes only the physical sink monitor. Keep this probe
        # optional so development/simulation environments do not need wpctl.
        stream_status = ""
        if self._command_exists("wpctl"):
            try:
                stream_status = self._run(("wpctl", "status"), timeout=2.0)
            except BluetoothCommandError as exc:
                logger.debug("could not inspect PipeWire streams with wpctl: %s", exc)

        all_devices = _parse_devices(devices_output)
        connected = {device.address.casefold() for device in _parse_devices(connected_output)}
        paired = {device.address.casefold() for device in _parse_devices(paired_output)}
        devices = tuple(
            BluetoothDevice(
                address=device.address,
                name=device.name,
                paired=device.address.casefold() in paired,
                connected=device.address.casefold() in connected,
            )
            for device in all_devices
        )
        connected_device = next((device for device in devices if device.connected), None)
        input_source = (
            _find_bluetooth_source(sources, connected_device.address)
            if connected_device is not None
            else None
        )
        default_sink = _parse_info_value(info, "Default Sink")
        if (
            input_source is None
            and connected_device is not None
            and _has_active_bluetooth_stream(stream_status, connected_device.address)
        ):
            input_source = _find_sink_monitor(sources, default_sink)
        status = BluetoothStatus(
            available=True,
            powered=_parse_bool_property(controller_output, "Powered"),
            scanning=_parse_bool_property(controller_output, "Discovering")
            or self._operation == "scanning",
            devices=devices,
            connected_device=connected_device,
            input_source=input_source,
            default_sink=default_sink,
            output_ready=bool(default_sink) and bool(sinks.strip()),
            operation=self._operation,
            error=self._operation_error,
        )
        self._set_status(status)

    def _scan(self) -> None:
        self._run(("bluetoothctl", "power", "on"), timeout=5.0)
        self._run(("bluetoothctl", "--timeout", "8", "scan", "on"), timeout=12.0)

    def _pair(self, address: str) -> None:
        # A command-line command puts bluetoothctl into non-interactive mode,
        # where BlueZ intentionally skips registering its --agent handler.
        # Keep the interactive bluetoothctl session alive for the whole
        # pairing transaction. The `auto` agent accepts the phone's numeric
        # confirmation/authorization requests without a terminal prompt.
        script = "\n".join(
            (
                "default-agent",
                "power on",
                "pairable on",
                f"pair {address}",
            )
        )
        output = self._run_script(
            ("bluetoothctl", "--agent", "auto", "--timeout", "35"),
            script,
            40.0,
        )
        _raise_for_bluetooth_failure(output)

        trust_output = self._run(("bluetoothctl", "trust", address), timeout=8.0)
        _raise_for_bluetooth_failure(trust_output)

    def _connect(self, address: str) -> None:
        self._run(("bluetoothctl", "power", "on"), timeout=5.0)
        # The phone advertises itself as an A2DP source. Connecting without a
        # profile asks BlueZ to try every advertised service (HFP, AVRCP,
        # networking, ...), and a failure in one of those can mask A2DP.
        output = self._run(
            ("bluetoothctl", "connect", address, "a2dp-source"), timeout=18.0
        )
        _raise_for_bluetooth_failure(output)

    def _forget(self, address: str) -> None:
        output = self._run(("bluetoothctl", "remove", address), timeout=8.0)
        _raise_for_bluetooth_failure(output)

    def _start_operation(self, name: str, operation: Callable[[], None]) -> None:
        if not self.enabled:
            raise BluetoothCommandError("Bluetooth is only available in hardware mode.")
        with self._lock:
            if self._operation is not None:
                raise BluetoothCommandError("another Bluetooth operation is already running")
            self._operation = name
            self._operation_error = None
            self._status = _with_operation(self._status, name, None)

        def run() -> None:
            error: str | None = None
            try:
                operation()
            except (BluetoothCommandError, OSError, subprocess.SubprocessError) as exc:
                error = str(exc)
                logger.warning("Bluetooth operation %s failed: %s", name, exc)
            finally:
                with self._lock:
                    self._operation = None
                    self._operation_error = error
                    self._status = _with_operation(self._status, None, error)
                self._refresh_once()

        threading.Thread(
            target=run,
            name=f"lumistripe-{name.split(':', 1)[0]}",
            daemon=True,
        ).start()

    def _run(self, command: tuple[str, ...], *, timeout: float) -> str:
        return self._runner(command, timeout)

    def _run_script(self, command: tuple[str, ...], script: str, timeout: float) -> str:
        if self._script_runner is not None:
            return self._script_runner(command, script, timeout)
        return _run_bluetoothctl_script(command, script, timeout)

    def _set_status(self, status: BluetoothStatus) -> None:
        with self._lock:
            self._status = status


def capture_device_selector(devices: Sequence[object]) -> str:
    """Return the PortAudio host device that follows PipeWire's default source."""
    names = {str(getattr(device, "name", device)): device for device in devices}
    lowered = {name.casefold(): name for name in names}
    for candidate in ("pulse", "pipewire", "default"):
        if candidate in lowered:
            return lowered[candidate]
    for name in names:
        if "bluez" in name.casefold() or "monitor" in name.casefold():
            return name
    raise BluetoothCommandError(
        "No PipeWire/Pulse capture device is visible to sounddevice. "
        "Install pipewire-alsa and libasound2-plugins, then restart LumiStripe."
    )


def _validate_address(address: str) -> str:
    normalized = address.strip().upper()
    if not BLUETOOTH_ADDRESS.fullmatch(normalized):
        raise BluetoothCommandError("Bluetooth address must look like AA:BB:CC:DD:EE:FF")
    return normalized


def _parse_devices(output: str) -> tuple[BluetoothDevice, ...]:
    devices: list[BluetoothDevice] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = _DEVICE_LINE.match(line.strip())
        if match is None:
            continue
        address = match.group(1).upper()
        if address in seen:
            continue
        seen.add(address)
        devices.append(BluetoothDevice(address=address, name=match.group(2).strip() or address))
    return tuple(devices)


def _parse_bool_property(output: str, property_name: str) -> bool:
    prefix = f"{property_name.casefold()}:"
    for line in output.splitlines():
        if line.strip().casefold().startswith(prefix):
            return line.split(":", 1)[1].strip().casefold() == "yes"
    return False


def _parse_info_value(output: str, property_name: str) -> str | None:
    prefix = f"{property_name.casefold()}:"
    for line in output.splitlines():
        if line.strip().casefold().startswith(prefix):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def _find_bluetooth_source(output: str, address: str) -> str | None:
    token = address.replace(":", "_").casefold()
    candidates: list[str] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        source = fields[1]
        lowered = source.casefold()
        if token not in lowered:
            continue
        if lowered.startswith("bluez_output.") and lowered.endswith(".monitor"):
            candidates.insert(0, source)
        elif lowered.startswith("bluez_input."):
            candidates.append(source)
        else:
            candidates.append(source)
    return candidates[0] if candidates else None


def _has_active_bluetooth_stream(output: str, address: str) -> bool:
    """Return whether wpctl reports an active media stream for the phone."""
    token = address.replace(":", "_").casefold()
    stream_pattern = re.compile(
        rf"\bbluez_(?:input|output)\.{re.escape(token)}(?:[._\s]|$)",
        re.IGNORECASE,
    )
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if stream_pattern.search(line) and any(
            "[active]" in candidate.casefold()
            for candidate in lines[index : index + 5]
        ):
            return True
    return False


def _find_sink_monitor(output: str, default_sink: str | None) -> str | None:
    """Find the Pulse source that mirrors the configured physical sink."""
    monitors: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        source = fields[1]
        if source.casefold().endswith(".monitor"):
            monitors.append((source, fields[-1].casefold()))

    if default_sink is not None:
        expected = f"{default_sink}.monitor".casefold()
        for source, _state in monitors:
            if source.casefold() == expected:
                return source

    for source, state in monitors:
        if state == "running":
            return source
    return monitors[0][0] if monitors else None


def _raise_for_bluetooth_failure(output: str) -> None:
    clean_output = _clean_command_output(output)
    failure_pattern = re.compile(
        r"\b(failed|invalid command|not available|no such device|not connected)\b",
        re.IGNORECASE,
    )
    for line in reversed(clean_output.splitlines()):
        if failure_pattern.search(line):
            raise BluetoothCommandError(line.strip())


def _with_operation(
    status: BluetoothStatus, operation: str | None, error: str | None
) -> BluetoothStatus:
    return BluetoothStatus(
        available=status.available,
        powered=status.powered,
        scanning=status.scanning or operation == "scanning",
        devices=status.devices,
        connected_device=status.connected_device,
        input_source=status.input_source,
        default_sink=status.default_sink,
        output_ready=status.output_ready,
        operation=operation,
        error=error,
    )


def _run_command(command: tuple[str, ...], timeout: float) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=_pipewire_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BluetoothCommandError(f"could not run {' '.join(command)}: {exc}") from exc
    output = _clean_command_output(
        "\n".join(value for value in (result.stdout, result.stderr) if value)
    )
    if result.returncode != 0:
        raise BluetoothCommandError(
            output or f"{' '.join(command)} failed with exit code {result.returncode}"
        )
    return output


def _run_bluetoothctl_script(
    command: tuple[str, ...], script: str, timeout: float
) -> str:
    process: subprocess.Popen[str] | None = None
    output: list[str] = []
    try:
        # Do not pass commands as command-line arguments: bluetoothctl then
        # enters non-interactive mode and suppresses its --agent registration.
        # Keep stdin open and drive the interactive session explicitly so the
        # pairing request cannot be lost before its asynchronous reply arrives.
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=_pipewire_environment(),
        )
        if process.stdin is None or process.stdout is None:
            raise BluetoothCommandError("bluetoothctl did not expose input/output pipes")

        deadline = time.monotonic() + timeout
        if "--agent" in command:
            _read_bluetoothctl_until(
                process,
                output,
                deadline,
                expected=_AGENT_REGISTERED,
                failure=_AGENT_FAILURE,
                stage="Bluetooth agent registration",
            )

        process.stdin.write(script.rstrip() + "\n")
        process.stdin.flush()
        _read_bluetoothctl_until(
            process,
            output,
            deadline,
            expected=_PAIRING_SUCCESS,
            failure=_PAIRING_FAILURE,
            stage="Bluetooth pairing",
        )

        # Pairing is asynchronous. Only close the session after bluetoothctl
        # has reported success; sending quit in the initial command batch can
        # terminate the shell before BlueZ completes authentication.
        process.stdin.write("quit\n")
        process.stdin.flush()
        try:
            process.wait(timeout=max(1.0, min(3.0, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            logger.warning("bluetoothctl did not exit after successful pairing")

        clean_output = _clean_command_output("\n".join(output))
        if process.returncode not in (None, 0):
            raise BluetoothCommandError(
                clean_output or f"bluetoothctl exited with code {process.returncode}"
            )
        return clean_output
    except BluetoothCommandError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise BluetoothCommandError(f"could not run {' '.join(command)}: {exc}") from exc
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()


def _read_bluetoothctl_until(
    process: subprocess.Popen[str],
    output: list[str],
    deadline: float,
    *,
    expected: re.Pattern[str],
    failure: re.Pattern[str],
    stage: str,
) -> None:
    if process.stdout is None:
        raise BluetoothCommandError("bluetoothctl did not expose an output pipe")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BluetoothCommandError(f"timed out waiting for {stage}")
            if not selector.select(remaining):
                raise BluetoothCommandError(f"timed out waiting for {stage}")

            line = process.stdout.readline()
            if not line:
                clean_output = _clean_command_output("\n".join(output))
                raise BluetoothCommandError(
                    clean_output or f"bluetoothctl exited while waiting for {stage}"
                )
            output.append(line)
            clean_line = _clean_command_output(line)
            if failure.search(clean_line):
                raise BluetoothCommandError(clean_line)
            if expected.search(clean_line):
                return
    finally:
        selector.close()


def _pipewire_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in environment:
        candidate = f"/run/user/{os.getuid()}"
        if os.path.isdir(candidate):
            environment["XDG_RUNTIME_DIR"] = candidate
    return environment


def _clean_command_output(output: str) -> str:
    """Remove terminal formatting emitted by bluetoothctl from captured output."""
    prompt = re.compile(r"^\[bluetoothctl\][>#]\s*")
    lines: list[str] = []
    for raw_line in _ANSI_ESCAPE.sub("", output).replace("\r", "").splitlines():
        line = prompt.sub("", raw_line.strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


__all__ = [
    "BLUETOOTH_ADDRESS",
    "BluetoothCommandError",
    "BluetoothDevice",
    "BluetoothManager",
    "BluetoothStatus",
    "capture_device_selector",
]
