"""Small BlueZ and PipeWire command providers used by the Bluetooth manager.

The providers deliberately know only command shapes. Parsing, validation, and
operation state remain in :mod:`lumistripe_web.bluetooth`, which keeps these
platform seams easy to inject in tests and replace on another Linux audio
stack.
"""

from __future__ import annotations

from collections.abc import Callable

from .bluetooth_models import Runner, ScriptRunner


class BlueZProvider:
    """Command boundary for ``bluetoothctl``."""

    def __init__(self, runner: Runner, script_runner: ScriptRunner) -> None:
        self._runner = runner
        self._script_runner = script_runner

    def show(self) -> str:
        return self._runner(("bluetoothctl", "show"), 2.0)

    def devices(self) -> str:
        return self._runner(("bluetoothctl", "devices"), 2.0)

    def connected_devices(self) -> str:
        return self._runner(("bluetoothctl", "devices", "Connected"), 2.0)

    def paired_devices(self) -> str:
        return self._runner(("bluetoothctl", "devices", "Paired"), 2.0)

    def info(self, address: str) -> str:
        return self._runner(("bluetoothctl", "info", address), 2.0)

    def scan(self) -> str:
        self._runner(("bluetoothctl", "power", "on"), 5.0)
        return self._runner(("bluetoothctl", "--timeout", "8", "scan", "on"), 12.0)

    def power(self, powered: bool) -> str:
        return self._runner(("bluetoothctl", "power", "on" if powered else "off"), 8.0)

    def set_alias(self, alias: str) -> str:
        return self._runner(("bluetoothctl", "system-alias", alias), 8.0)

    def pair(self, address: str) -> str:
        script = "\n".join(("default-agent", "power on", "pairable on", f"pair {address}"))
        return self._script_runner(
            ("bluetoothctl", "--agent", "auto", "--timeout", "35"),
            script,
            40.0,
        )

    def trust(self, address: str) -> str:
        return self._runner(("bluetoothctl", "trust", address), 8.0)

    def connect(self, address: str, profile: str) -> str:
        self._runner(("bluetoothctl", "power", "on"), 5.0)
        return self._runner(("bluetoothctl", "connect", address, profile), 18.0)

    def forget(self, address: str) -> str:
        return self._runner(("bluetoothctl", "remove", address), 8.0)

    def disconnect(self, address: str) -> str:
        return self._runner(("bluetoothctl", "disconnect", address), 8.0)


class PipeWireProvider:
    """Command boundary for Pulse/PipeWire compatibility tools."""

    def __init__(self, runner: Runner, command_exists: Callable[[str], bool]) -> None:
        self._runner = runner
        self.command_exists = command_exists

    def sources(self) -> str:
        return self._runner(("pactl", "list", "short", "sources"), 2.0)

    def sinks(self) -> str:
        return self._runner(("pactl", "list", "short", "sinks"), 2.0)

    def sink_details(self) -> str:
        return self._runner(("pactl", "list", "sinks"), 2.0)

    def sink_inputs(self) -> str:
        return self._runner(("pactl", "list", "short", "sink-inputs"), 2.0)

    def modules(self) -> str:
        return self._runner(("pactl", "list", "short", "modules"), 2.0)

    def info(self) -> str:
        return self._runner(("pactl", "info"), 2.0)

    def stream_status(self) -> str:
        return self._runner(("wpctl", "status"), 2.0)

    def set_default_sink(self, sink: str) -> None:
        self._runner(("pactl", "set-default-sink", sink), 2.0)

    def move_sink_input(self, input_id: str, sink: str) -> None:
        self._runner(("pactl", "move-sink-input", input_id, sink), 2.0)

    def load_module(self, module: str, arguments: str) -> str:
        return self._runner(("pactl", "load-module", module, arguments), 2.0)

    def unload_module(self, module_id: str) -> None:
        self._runner(("pactl", "unload-module", module_id), 2.0)

    def set_volume(self, sink: str, volume: float) -> None:
        self._runner(("pactl", "set-sink-volume", sink, f"{round(volume * 100)}%"), 2.0)

    def set_mute(self, sink: str, muted: bool) -> None:
        self._runner(("pactl", "set-sink-mute", sink, "1" if muted else "0"), 2.0)

    def set_default_source(self, source: str) -> None:
        self._runner(("pactl", "set-default-source", source), 2.0)


__all__ = ["BlueZProvider", "PipeWireProvider"]
