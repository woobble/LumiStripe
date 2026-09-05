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
    # Roles are relative to the Pi: an advertised Audio Source is an input
    # into LumiStripe, while an advertised Audio Sink is an output from it.
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioOutputDevice:
    selector: str
    name: str
    volume: float | None = None
    muted: bool = False
    bluetooth: bool = False
    connected: bool = True


@dataclass(frozen=True, slots=True)
class BluetoothStatus:
    available: bool = False
    powered: bool = False
    adapter_alias: str | None = None
    scanning: bool = False
    devices: tuple[BluetoothDevice, ...] = ()
    connected_inputs: tuple[BluetoothDevice, ...] = ()
    connected_outputs: tuple[BluetoothDevice, ...] = ()
    connected_device: BluetoothDevice | None = None
    input_source: str | None = None
    output_devices: tuple[AudioOutputDevice, ...] = ()
    default_sink: str | None = None
    output_volume: float | None = None
    output_muted: bool = False
    output_ready: bool = False
    operation: str | None = None
    error: str | None = None

    @property
    def streaming(self) -> bool:
        return bool(self.input_source) and bool(self.connected_inputs or self.connected_device)


Runner = Callable[[tuple[str, ...], float], str]
ScriptRunner = Callable[[tuple[str, ...], str, float], str]


class BluetoothManager:
    """Best-effort BlueZ and PipeWire bridge for a headless Pi.

    BlueZ owns pairing and A2DP reception. PipeWire/WirePlumber routes the
    incoming A2DP stream to the configured default speaker sink and exposes a
    monitor source. LumiStripe selects that monitor as its analysis input by
    temporarily making it the default capture source for the PipeWire/Pulse
    client used by sounddevice. Some WirePlumber versions expose the Bluetooth input
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

    def set_power(self, powered: bool) -> BluetoothStatus:
        self._start_operation(
            f"power:{'on' if powered else 'off'}",
            lambda: self._set_power(powered),
        )
        return self.status()

    def set_alias(self, alias: str) -> BluetoothStatus:
        normalized = _validate_alias(alias)
        self._start_operation(
            "renaming",
            lambda: self._set_alias(normalized),
        )
        return self.status()

    def pair(self, address: str) -> BluetoothStatus:
        normalized = _validate_address(address)
        self._start_operation(f"pairing:{normalized}", lambda: self._pair(normalized))
        return self.status()

    def connect(self, address: str, role: str | None = None) -> BluetoothStatus:
        normalized = _validate_address(address)
        self._start_operation(
            f"connecting:{normalized}",
            lambda: self._connect(normalized, role),
        )
        return self.status()

    def forget(self, address: str) -> BluetoothStatus:
        normalized = _validate_address(address)
        self._start_operation(f"forgetting:{normalized}", lambda: self._forget(normalized))
        return self.status()

    def set_default_sink(self, sink: str) -> BluetoothStatus:
        if not self.enabled:
            raise BluetoothCommandError("Audio outputs are only available in hardware mode.")
        normalized = _validate_selector(sink)
        self._run(("pactl", "set-default-sink", normalized), timeout=2.0)
        self._move_sink_inputs(normalized)
        return self.refresh()

    def set_output_volume(self, sink: str, volume: float) -> BluetoothStatus:
        if not self.enabled:
            raise BluetoothCommandError("Audio outputs are only available in hardware mode.")
        normalized = _validate_selector(sink)
        if not 0.0 <= volume <= 1.0:
            raise BluetoothCommandError("Output volume must be between 0 and 1")
        self._run(
            ("pactl", "set-sink-volume", normalized, f"{round(volume * 100)}%"),
            timeout=2.0,
        )
        return self.refresh()

    def set_output_mute(self, sink: str, muted: bool) -> BluetoothStatus:
        if not self.enabled:
            raise BluetoothCommandError("Audio outputs are only available in hardware mode.")
        normalized = _validate_selector(sink)
        self._run(
            ("pactl", "set-sink-mute", normalized, "1" if muted else "0"),
            timeout=2.0,
        )
        return self.refresh()

    def _move_sink_inputs(self, sink: str) -> None:
        """Move currently playing streams when the output is changed."""
        try:
            inputs = self._run(("pactl", "list", "short", "sink-inputs"), timeout=2.0)
        except BluetoothCommandError as exc:
            logger.debug("could not inspect active audio streams: %s", exc)
            return
        for line in inputs.splitlines():
            fields = line.split()
            if not fields or not fields[0].isdigit():
                continue
            try:
                self._run(
                    ("pactl", "move-sink-input", fields[0], sink),
                    timeout=2.0,
                )
            except BluetoothCommandError as exc:
                logger.warning("could not move audio stream %s to %s: %s", fields[0], sink, exc)

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
                    adapter_alias=_parse_info_value(controller_output, "Alias")
                    or previous.adapter_alias,
                    scanning=_parse_bool_property(controller_output, "Discovering")
                    or self._operation == "scanning",
                    devices=previous.devices,
                    connected_inputs=previous.connected_inputs,
                    connected_outputs=previous.connected_outputs,
                    connected_device=previous.connected_device,
                    input_source=previous.input_source,
                    output_devices=previous.output_devices,
                    default_sink=previous.default_sink,
                    output_volume=previous.output_volume,
                    output_muted=previous.output_muted,
                    output_ready=previous.output_ready,
                    operation=self._operation,
                    error=str(exc),
                )
            )
            return

        try:
            sink_details = self._run(("pactl", "list", "sinks"), timeout=2.0)
        except BluetoothCommandError as exc:
            logger.debug("could not inspect detailed audio outputs: %s", exc)
            sink_details = ""

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
        previous_roles = {
            device.address.casefold(): device.roles for device in self.status().devices
        }
        devices_list: list[BluetoothDevice] = []
        for device in all_devices:
            address_key = device.address.casefold()
            roles = _parse_bluetooth_roles(self._device_info(device.address))
            if not roles:
                roles = previous_roles.get(address_key, ())
            if _contains_bluetooth_token(sinks, device.address):
                roles = _merge_roles(roles, ("output",))
            if _contains_bluetooth_token(sources, device.address) or _has_active_bluetooth_stream(
                stream_status, device.address
            ):
                roles = _merge_roles(roles, ("input",))
            devices_list.append(
                BluetoothDevice(
                    address=device.address,
                    name=device.name,
                    paired=address_key in paired,
                    connected=address_key in connected,
                    roles=roles,
                )
            )
        devices = tuple(devices_list)
        connected_inputs = tuple(
            device for device in devices if device.connected and "input" in device.roles
        )
        connected_outputs = tuple(
            device for device in devices if device.connected and "output" in device.roles
        )
        connected_device = next(
            (device for device in connected_inputs),
            next((device for device in devices if device.connected), None),
        )
        default_sink = _parse_info_value(info, "Default Sink")
        input_source = None
        for input_device in connected_inputs:
            input_source = _find_bluetooth_source(sources, input_device.address)
            if (
                input_source is None
                and _has_active_bluetooth_stream(stream_status, input_device.address)
            ):
                input_source = _find_sink_monitor(sources, default_sink)
            if input_source is not None:
                break
        output_devices = _parse_output_devices(sinks, sink_details, devices)
        default_output = next(
            (device for device in output_devices if device.selector == default_sink),
            None,
        )
        status = BluetoothStatus(
            available=True,
            powered=_parse_bool_property(controller_output, "Powered"),
            adapter_alias=_parse_info_value(controller_output, "Alias"),
            scanning=_parse_bool_property(controller_output, "Discovering")
            or self._operation == "scanning",
            devices=devices,
            connected_inputs=connected_inputs,
            connected_outputs=connected_outputs,
            connected_device=connected_device,
            input_source=input_source,
            output_devices=output_devices,
            default_sink=default_sink,
            output_volume=default_output.volume if default_output is not None else None,
            output_muted=default_output.muted if default_output is not None else False,
            output_ready=bool(default_sink) and bool(output_devices),
            operation=self._operation,
            error=self._operation_error,
        )
        self._set_status(status)

    def _device_info(self, address: str) -> str:
        try:
            return self._run(("bluetoothctl", "info", address), timeout=2.0)
        except BluetoothCommandError as exc:
            logger.debug("could not inspect Bluetooth device %s: %s", address, exc)
            return ""

    def _scan(self) -> None:
        self._run(("bluetoothctl", "power", "on"), timeout=5.0)
        self._run(("bluetoothctl", "--timeout", "8", "scan", "on"), timeout=12.0)

    def _set_power(self, powered: bool) -> None:
        output = self._run(
            ("bluetoothctl", "power", "on" if powered else "off"),
            timeout=8.0,
        )
        _raise_for_bluetooth_failure(output)

    def _set_alias(self, alias: str) -> None:
        output = self._run(("bluetoothctl", "system-alias", alias), timeout=8.0)
        _raise_for_bluetooth_failure(output)

    def _pair(self, address: str) -> None:
        # A command-line command puts bluetoothctl into non-interactive mode,
        # where BlueZ intentionally skips registering its --agent handler.
        # Keep the interactive bluetoothctl session alive for the whole
        # pairing transaction. The `auto` agent accepts the device's numeric
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

    def _connect(self, address: str, role: str | None = None) -> None:
        self._run(("bluetoothctl", "power", "on"), timeout=5.0)
        device = next(
            (
                item
                for item in self.status().devices
                if item.address.casefold() == address.casefold()
            ),
            None,
        )
        selected_role = role or (
            "output"
            if device is not None and "output" in device.roles
            else "input"
        )
        if selected_role not in {"input", "output"}:
            raise BluetoothCommandError("Bluetooth role must be input or output")
        profile = "a2dp-sink" if selected_role == "output" else "a2dp-source"
        # Connecting to a specific A2DP profile avoids unrelated HFP, AVRCP,
        # or networking services masking the audio connection. A remote
        # Audio Sink is a speaker for the Pi; a remote Audio Source is a
        # phone or other device sending music into the Pi.
        output = self._run(
            ("bluetoothctl", "connect", address, profile), timeout=18.0
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


def _validate_alias(alias: str) -> str:
    normalized = alias.strip()
    if not normalized:
        raise BluetoothCommandError("Bluetooth name cannot be empty")
    if len(normalized) > 64:
        raise BluetoothCommandError("Bluetooth name must be 64 characters or fewer")
    return normalized


def _validate_selector(selector: str) -> str:
    normalized = selector.strip()
    if not normalized or len(normalized) > 256 or any(char.isspace() for char in normalized):
        raise BluetoothCommandError("Audio output selector is invalid")
    return normalized


def _parse_bluetooth_roles(output: str) -> tuple[str, ...]:
    roles: set[str] = set()
    for line in output.splitlines():
        lowered = line.casefold()
        if re.search(r"\baudio source\b", lowered):
            roles.add("input")
        if re.search(r"\baudio sink\b", lowered):
            roles.add("output")
    return tuple(role for role in ("input", "output") if role in roles)


def _merge_roles(current: Sequence[str], additional: Sequence[str]) -> tuple[str, ...]:
    roles = set(current)
    roles.update(additional)
    return tuple(role for role in ("input", "output") if role in roles)


def _contains_bluetooth_token(output: str, address: str) -> bool:
    return address.replace(":", "_").casefold() in output.casefold()


def _bluetooth_address_from_node(node: str) -> str | None:
    match = re.search(
        r"bluez_(?:input|output)\.([0-9a-f]{2}(?:_[0-9a-f]{2}){5})(?:[._]|$)",
        node,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group(1).replace("_", ":").upper()


def _parse_sink_details(output: str) -> dict[str, tuple[str | None, float | None, bool]]:
    details: dict[str, tuple[str | None, float | None, bool]] = {}
    blocks = re.split(r"(?=^Sink #)", output, flags=re.MULTILINE)
    for block in blocks:
        selector = _parse_info_value(block, "Name")
        if not selector:
            continue
        description = _parse_info_value(block, "Description")
        muted = _parse_bool_property(block, "Mute")
        volume: float | None = None
        for line in block.splitlines():
            if not line.strip().casefold().startswith("volume:"):
                continue
            match = re.search(r"/\s*([0-9]+(?:\.[0-9]+)?)\s*%", line)
            if match is not None:
                volume = max(0.0, min(1.0, float(match.group(1)) / 100.0))
            break
        details[selector.casefold()] = (description, volume, muted)
    return details


def _parse_output_devices(
    short_output: str,
    detailed_output: str,
    bluetooth_devices: Sequence[BluetoothDevice],
) -> tuple[AudioOutputDevice, ...]:
    details = _parse_sink_details(detailed_output)
    names = {device.address.casefold(): device.name for device in bluetooth_devices}
    outputs: list[AudioOutputDevice] = []
    for line in short_output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        selector = fields[1]
        description, volume, muted = details.get(selector.casefold(), (None, None, False))
        address = _bluetooth_address_from_node(selector)
        is_bluetooth = address is not None
        name = description or selector
        if address is not None:
            name = names.get(address.casefold(), name)
        outputs.append(
            AudioOutputDevice(
                selector=selector,
                name=name,
                volume=volume,
                muted=muted,
                bluetooth=is_bluetooth,
                connected=(
                    any(device.address.casefold() == address.casefold() and device.connected for device in bluetooth_devices)
                    if address is not None
                    else True
                ),
            )
        )
    return tuple(outputs)


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
    """Return whether wpctl reports an active incoming stream for the Pi."""
    token = address.replace(":", "_").casefold()
    stream_pattern = re.compile(
        rf"\bbluez_input\.{re.escape(token)}(?:[._\s]|$)",
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
        adapter_alias=status.adapter_alias,
        scanning=status.scanning or operation == "scanning",
        devices=status.devices,
        connected_inputs=status.connected_inputs,
        connected_outputs=status.connected_outputs,
        connected_device=status.connected_device,
        input_source=status.input_source,
        output_devices=status.output_devices,
        default_sink=status.default_sink,
        output_volume=status.output_volume,
        output_muted=status.output_muted,
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
    "AudioOutputDevice",
    "BluetoothCommandError",
    "BluetoothDevice",
    "BluetoothManager",
    "BluetoothStatus",
    "capture_device_selector",
]
