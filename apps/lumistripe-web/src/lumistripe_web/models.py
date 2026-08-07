from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from lumistripe import PlaybackMode
from pydantic import BaseModel, ConfigDict, Field


class RuntimeKind(str, Enum):
    SIMULATION = "simulation"
    HARDWARE = "hardware"


class DiagnosticIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    severity: str
    title: str
    message: str
    action: str


class ColorCorrectionProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_index: int = Field(ge=0)
    name: str
    device: str
    red: int = Field(ge=0, le=255)
    green: int = Field(ge=0, le=255)
    blue: int = Field(ge=0, le=255)


class CalibrationStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    active: bool = False
    output_index: int | None = None
    pattern: Literal["white", "red", "green", "blue"] | None = None
    expires_in_seconds: float | None = None


class DashboardState(BaseModel):
    model_config = ConfigDict(frozen=True)

    revision: int = 0
    runtime: RuntimeKind
    output_backend: str = "simulation"
    output_devices: tuple[str, ...] = ()
    spi_speed_hz: int | None = None
    running: bool = False
    mode: PlaybackMode = PlaybackMode.STATIC
    solid_color: str = "#7C3AED"
    animation: str = ""
    brightness: float = 1.0
    blackout: bool = False
    music_active: bool = False
    music_gate: str = "calm"
    bpm: float = 0.0
    audio_status: str = "No audio source active."
    active_effects: tuple[str, ...] = ()
    uptime_seconds: float = 0.0
    frame_rate: float = 0.0
    audio_health: str = "inactive"
    audio_callback_age_seconds: float | None = None
    audio_frame_age_seconds: float | None = None
    last_output_at: datetime | None = None
    last_output_age_seconds: float | None = None
    application_version: str = "development"
    color_corrections: tuple[ColorCorrectionProfile, ...] = ()
    calibration: CalibrationStatus = CalibrationStatus()
    diagnostic_issues: tuple[DiagnosticIssue, ...] = ()
    error: str | None = None


class AnimationOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    mood: str
    dynamic_safe: bool


class AnimationList(BaseModel):
    items: tuple[AnimationOption, ...]


class ModeRequest(BaseModel):
    mode: PlaybackMode
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class BrightnessRequest(BaseModel):
    brightness: float = Field(ge=0.0, le=1.0)


class AnimationRequest(BaseModel):
    name: str = Field(min_length=1)


class BlackoutRequest(BaseModel):
    enabled: bool


class AccessStatus(BaseModel):
    required: bool
    authenticated: bool


class PairingRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{4}$")


class CalibrationStartRequest(BaseModel):
    output_index: int = Field(ge=0)


class CalibrationUpdateRequest(BaseModel):
    red: int = Field(ge=0, le=255)
    green: int = Field(ge=0, le=255)
    blue: int = Field(ge=0, le=255)
    pattern: Literal["white", "red", "green", "blue"]


class CalibrationFinishRequest(BaseModel):
    save: bool


class CalibrationSessionResponse(BaseModel):
    session_id: str
    state: DashboardState


class AudioTuningValues(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_level: float = Field(default=0.36, ge=0.1, le=0.8)
    dynamic_response: float = Field(default=0.65, ge=0.0, le=1.0)
    rms_attack: float = Field(default=0.45, ge=0.01, le=1.0)
    rms_release: float = Field(default=0.12, ge=0.01, le=1.0)
    band_attack: float = Field(default=0.4, ge=0.01, le=1.0)
    band_release: float = Field(default=0.1, ge=0.01, le=1.0)
    beat_release: float = Field(default=0.18, ge=0.01, le=1.0)
    energy_threshold: float = Field(default=0.03, ge=0.0, le=1.0)
    onset_threshold: float = Field(default=0.025, ge=0.0, le=1.0)
    beat_density_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    brightness_threshold: float = Field(default=0.08, ge=0.0, le=1.0)
    spectral_balance_ratio: float = Field(default=0.35, ge=0.0, le=1.0)


class AudioDeviceOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    selector: str
    name: str
    settings: AudioTuningValues


class AudioSettingsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    monitoring: bool
    active_device: str | None = None
    active_device_name: str | None = None
    devices: tuple[AudioDeviceOption, ...] = ()
    settings: AudioTuningValues = AudioTuningValues()
    configured_noise_floor: float = 0.015
    error: str | None = None


class AudioSettingsRequest(BaseModel):
    device: str = Field(min_length=1)
    settings: AudioTuningValues


class AudioResetRequest(BaseModel):
    device: str = Field(min_length=1)


class AudioTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = 0
    fresh: bool = False
    input_level: float = Field(default=0.0, ge=0.0, le=1.0)
    processed_level: float = Field(default=0.0, ge=0.0, le=1.0)
    bands: tuple[float, float, float, float, float, float, float, float] = (
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    beat: bool = False
    beat_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    bpm: float = 0.0
    estimated_noise_floor: float = Field(default=0.0, ge=0.0, le=1.0)
    configured_noise_floor: float = Field(default=0.015, ge=0.0, le=1.0)
    normalization_gain: float = Field(default=1.0, ge=0.0)
    program_loudness: float = Field(default=0.0, ge=0.0, le=1.0)
    musical_impact: float = Field(default=0.0, ge=0.0, le=1.0)
    gate: str = "idle"
    gate_preview: bool = True
    gate_energy: float = Field(default=0.0, ge=0.0, le=1.0)
    gate_onset: float = Field(default=0.0, ge=0.0, le=1.0)
    gate_beat_density: float = Field(default=0.0, ge=0.0, le=1.0)
    gate_brightness: float = Field(default=0.0, ge=0.0, le=1.0)
    health: str = "inactive"
