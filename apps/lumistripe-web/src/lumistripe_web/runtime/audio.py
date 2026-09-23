"""Audio input construction boundary for the runtime worker."""

from __future__ import annotations

from lumistripe import AudioConfig, AudioInput


def default_audio_factory(device: str | None, config: AudioConfig) -> AudioInput:
    return (
        AudioInput.with_device_config(device, config)
        if device
        else AudioInput.with_config(config)
    )


__all__ = ["default_audio_factory"]
