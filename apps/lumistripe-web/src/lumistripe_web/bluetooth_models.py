"""Typed provider-facing Bluetooth and PipeWire models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

BluetoothOperationState = Literal["idle", "running", "complete", "failed"]


@dataclass(frozen=True, slots=True)
class BluetoothCapabilities:
    """Operations supported by the active Bluetooth/audio provider."""

    operations: tuple[str, ...] = ()
    max_inputs: int = 1
    max_outputs: int = 1


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
    capabilities: BluetoothCapabilities = field(default_factory=BluetoothCapabilities)
    operation: str | None = None
    operation_id: str | None = None
    operation_state: BluetoothOperationState = "idle"
    error: str | None = None

    @property
    def streaming(self) -> bool:
        return bool(self.input_source) and bool(self.connected_inputs or self.connected_device)


Runner = Callable[[tuple[str, ...], float], str]
ScriptRunner = Callable[[tuple[str, ...], str, float], str]


class BluetoothDeviceBackend(Protocol):
    """Provider boundary for Bluetooth discovery and device operations."""

    def status(self) -> BluetoothStatus: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def start_scan(self) -> BluetoothStatus: ...

    def set_power(self, powered: bool) -> BluetoothStatus: ...

    def set_alias(self, alias: str) -> BluetoothStatus: ...

    def pair(self, address: str) -> BluetoothStatus: ...

    def connect(self, address: str, role: str | None = None) -> BluetoothStatus: ...

    def forget(self, address: str) -> BluetoothStatus: ...

    def disconnect(self, address: str) -> BluetoothStatus: ...


class AudioRoutingBackend(Protocol):
    """Provider boundary for the audio graph used by Bluetooth streams."""

    def set_default_sink(self, sink: str) -> BluetoothStatus: ...

    def set_output_volume(self, sink: str, volume: float) -> BluetoothStatus: ...

    def set_output_mute(self, sink: str, muted: bool) -> BluetoothStatus: ...

    def default_source(self) -> str | None: ...

    def set_default_source(self, source: str) -> None: ...

    def restore_default_source(self, source: str | None) -> None: ...

    def ensure_spotify_route(self) -> None: ...


class BluetoothAudioBackend(BluetoothDeviceBackend, AudioRoutingBackend, Protocol):
    """Combined backend consumed by the runtime and HTTP layer."""


__all__ = [
    "AudioOutputDevice",
    "AudioRoutingBackend",
    "BluetoothAudioBackend",
    "BluetoothCapabilities",
    "BluetoothDevice",
    "BluetoothDeviceBackend",
    "BluetoothOperationState",
    "BluetoothStatus",
    "Runner",
    "ScriptRunner",
]
