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
