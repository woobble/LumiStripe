"""Runtime state value objects and factory contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

from lumistripe import ColorCorrectionController, Controller, PixelBuffer
from lumistripe.audio.capture import AudioInput
from lumistripe.audio.config import AudioConfig
from lumistripe.audio.types import AudioFrame, MusicFeatures
from lumistripe.controller import ColorCorrection
from lumistripe.playback import AudioSource

from ..models import RuntimeKind
from ..settings import StripeTopologySettings, default_settings_path
from .outputs import OutputGateController
from .preview import PreviewFrame

CalibrationPattern = Literal["white", "red", "green", "blue"]


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    pixels: int = 80
    hardware: bool = False
    output_backend: str = "spi"
    spi_device: str = "/dev/spidev0.0"
    spi_speed_hz: int = 1_000_000
    spi_device_2: str | None = None
    spi_speed_hz_2: int | None = None
    chip: str = "/dev/gpiochip0"
    data_pin: int = 14
    clock_pin: int = 15
    audio_source: str = "auto"
    audio_device: str | None = None
    settings_file: Path = field(default_factory=default_settings_path)
    ignore_saved_stripes: bool = False

    def __post_init__(self) -> None:
        if self.pixels <= 0:
            raise ValueError("pixels must be greater than zero")
        if self.output_backend not in {"spi", "gpio"}:
            raise ValueError(f"invalid output backend: {self.output_backend}")
        if self.spi_speed_hz <= 0:
            raise ValueError("SPI speed must be greater than zero")
        if self.spi_speed_hz_2 is not None and self.spi_speed_hz_2 <= 0:
            raise ValueError("secondary SPI speed must be greater than zero")
        if self.spi_speed_hz_2 is not None and self.spi_device_2 is None:
            raise ValueError("secondary SPI speed requires a secondary SPI device")
        if self.audio_source not in {
            "auto",
            "off",
            "demo",
            "mic",
            "bluetooth",
            "spotify",
        }:
            raise ValueError(f"invalid audio source: {self.audio_source}")

    @property
    def kind(self) -> RuntimeKind:
        return RuntimeKind.HARDWARE if self.hardware else RuntimeKind.SIMULATION

    def dynamic_audio_source(self) -> AudioSource:
        if self.audio_source == "auto":
            return AudioSource.BLUETOOTH if self.hardware else AudioSource.DEMO
        return AudioSource(self.audio_source)


@dataclass(slots=True)
class _CalibrationSession:
    session_id: str
    output_index: int
    pattern: CalibrationPattern
    original_corrections: tuple[ColorCorrection, ...]
    original_frames: tuple[PixelBuffer, ...]
    original_blackout: bool
    last_activity_at: float


@dataclass(slots=True)
class _BuiltTopology:
    raw: Controller
    shared: OutputGateController
    outputs: tuple[OutputGateController, ...]
    corrections: tuple[ColorCorrectionController, ...]


@dataclass(slots=True)
class _StripeTestSession:
    stripe_id: str
    pattern: str
    expires_at: float
    restore_topology: StripeTopologySettings | None = None


class _AudioCalibrationResult(TypedDict):
    duration_seconds: float
    samples: int
    measured_floor: float
    measured_peak: float
    recommended_noise_floor: float
    recommended_target_level: float
    recommended_hardware_gain: float | None
    recommended_idle_threshold_scale: float


@dataclass(slots=True)
class _AudioCalibrationSession:
    session_id: str
    device: str
    started_at: float
    duration_seconds: float
    frames: list[AudioFrame] = field(default_factory=list)
    features: list[MusicFeatures] = field(default_factory=list)
    result: _AudioCalibrationResult | None = None
    error: str | None = None


_ControllerFactory = Callable[[RuntimeSettings], Controller]
_AudioFactory = Callable[[str | None, AudioConfig], AudioInput]


__all__ = [
    "CalibrationPattern",
    "PreviewFrame",
    "RuntimeSettings",
    "_AudioCalibrationResult",
    "_AudioCalibrationSession",
    "_AudioFactory",
    "_BuiltTopology",
    "_CalibrationSession",
    "_ControllerFactory",
    "_StripeTestSession",
]
