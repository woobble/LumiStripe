"""Optional sounddevice loading and input-device discovery."""

from __future__ import annotations

import importlib
from typing import Any

from .types import AudioInputDevice


def list_input_devices() -> list[str]:
    return [device.name for device in list_input_device_details()]


def list_input_device_details() -> list[AudioInputDevice]:
    sounddevice = _load_sounddevice()
    devices = sounddevice.query_devices()
    details: list[AudioInputDevice] = []
    for device in devices:
        if int(device.get("max_input_channels", 0)) > 0:
            details.append(
                AudioInputDevice(
                    index=int(device.get("index", len(details))),
                    name=_device_name(device),
                )
            )
    return details


def _load_sounddevice() -> Any:
    try:
        return importlib.import_module("sounddevice")
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "sounddevice is required for AudioInput; install lumistripe-core[audio]"
        ) from exc


def _device_name(device: Any) -> str:
    if isinstance(device, dict):
        return str(device.get("name", "default"))
    return str(device)


def find_input_device(sounddevice: Any, pattern: str | None) -> Any:
    devices = sounddevice.query_devices()
    if pattern is None:
        for device in devices:
            if int(device.get("max_input_channels", 0)) > 0:
                return device
        raise RuntimeError("no audio input device available")

    if _looks_like_device_index(pattern):
        expected_index = int(pattern)
        for device in devices:
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            if int(device.get("index", -1)) == expected_index:
                return device
        raise RuntimeError(f'no input device with index "{pattern}"')

    lowered = pattern.lower()
    for device in devices:
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        if lowered in str(device.get("name", "")).lower():
            return device
    raise RuntimeError(f'no input device matching "{pattern}"')


def device_id(device: Any) -> Any:
    if isinstance(device, dict):
        index = device.get("index")
        if index is not None:
            return index
        name = device.get("name")
        if name:
            return str(name)
    return device


def device_sample_rate(device: Any) -> float:
    if isinstance(device, dict):
        sample_rate = device.get("default_samplerate")
        if sample_rate is not None:
            try:
                return max(float(sample_rate), 1.0)
            except (TypeError, ValueError):
                pass
    return 44_100.0


def _looks_like_device_index(value: str) -> bool:
    return value.strip().isdigit()


__all__ = [
    "device_id",
    "device_sample_rate",
    "find_input_device",
    "list_input_device_details",
    "list_input_devices",
]
