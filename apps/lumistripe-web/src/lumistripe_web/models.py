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


class StripeOutputConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=40)
    pixels: int = Field(ge=1, le=4096)
    backend: Literal["spi", "gpio"] = "spi"
    reversed: bool = False
    spi_device: str = Field(default="/dev/spidev0.0", min_length=1)
    spi_speed_hz: int = Field(default=1_000_000, gt=0, le=32_000_000)
    chip: str = Field(default="/dev/gpiochip0", min_length=1)
    data_pin: int = Field(default=10, ge=0)
    clock_pin: int = Field(default=11, ge=0)
    last_output_at: datetime | None = None
    error: str | None = None


class StripeTopology(BaseModel):
    model_config = ConfigDict(frozen=True)

    layout: Literal["mirrored", "continuous", "independent"] = "mirrored"
    outputs: tuple[StripeOutputConfig, ...] = Field(default=(), max_length=2)


class StripePlaybackState(BaseModel):
    model_config = ConfigDict(frozen=True)

    stripe_id: str
    mode: PlaybackMode = PlaybackMode.STATIC
    solid_color: str = "#7C3AED"
    animation: str = ""
    brightness: float = 1.0
    blackout: bool = False
    music_active: bool = False
    music_recognition_enabled: bool = True


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
    music_recognition_enabled: bool = True
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
    stripe_topology: StripeTopology = StripeTopology()
    stripe_playback: tuple[StripePlaybackState, ...] = ()
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
    stripe_id: str | None = None
    music_recognition_enabled: bool | None = None


class BrightnessRequest(BaseModel):
    brightness: float = Field(ge=0.0, le=1.0)
    stripe_id: str | None = None


class AnimationRequest(BaseModel):
    name: str = Field(min_length=1)
    stripe_id: str | None = None


class BlackoutRequest(BaseModel):
    enabled: bool
    stripe_id: str | None = None


class StripeTopologyRequest(BaseModel):
    layout: Literal["mirrored", "continuous", "independent"]
    outputs: tuple[StripeOutputConfig, ...] = Field(max_length=2)


class StripeTestRequest(BaseModel):
    stripe_id: str
    pattern: Literal["identify", "red", "green", "blue", "white"] = "identify"
    topology: StripeTopologyRequest | None = None


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
    hardware_gain_target: float | None = Field(default=None, ge=0.0, le=1.0)
    noise_floor: float = Field(default=0.015, ge=0.0, le=1.0)
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


class AudioOutputDeviceInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    selector: str
    name: str
    volume: float | None = Field(default=None, ge=0.0, le=1.0)
    muted: bool = False
    bluetooth: bool = False
    connected: bool = True


BluetoothOperation = Literal[
    "power",
    "rename",
    "scan",
    "pair",
    "connect",
    "disconnect",
    "forget",
    "output_select",
    "output_volume",
    "output_mute",
]


class BluetoothCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    operations: tuple[BluetoothOperation, ...] = ()
    max_inputs: int = Field(default=1, ge=1)
    max_outputs: int = Field(default=1, ge=1)


class BluetoothDeviceInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    address: str
    name: str
    paired: bool = False
    connected: bool = False
    roles: tuple[Literal["input", "output"], ...] = ()


class BluetoothStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool = False
    powered: bool = False
    adapter_alias: str | None = None
    scanning: bool = False
    streaming: bool = False
    devices: tuple[BluetoothDeviceInfo, ...] = ()
    connected_inputs: tuple[BluetoothDeviceInfo, ...] = ()
    connected_outputs: tuple[BluetoothDeviceInfo, ...] = ()
    connected_device: BluetoothDeviceInfo | None = None
    input_source: str | None = None
    output_devices: tuple[AudioOutputDeviceInfo, ...] = ()
    default_sink: str | None = None
    output_volume: float | None = Field(default=None, ge=0.0, le=1.0)
    output_muted: bool = False
    output_ready: bool = False
    capabilities: BluetoothCapabilities = Field(default_factory=BluetoothCapabilities)
    operation: str | None = None
    operation_id: str | None = None
    operation_state: Literal["idle", "running", "complete", "failed"] = "idle"
    error: str | None = None


class AudioCalibrationStartRequest(BaseModel):
    device: str = Field(min_length=1)
    duration_seconds: float = Field(default=8.0, ge=3.0, le=30.0)


class AudioCalibrationFinishRequest(BaseModel):
    apply: bool
    target_level: float | None = Field(default=None, ge=0.1, le=0.8)
    noise_floor: float | None = Field(default=None, ge=0.0, le=1.0)


class AudioCalibrationResult(BaseModel):
    duration_seconds: float
    samples: int
    measured_floor: float
    measured_peak: float
    recommended_noise_floor: float
    recommended_target_level: float
    recommended_hardware_gain: float | None = Field(default=None, ge=0.0, le=1.0)
    recommended_idle_threshold_scale: float


class AudioCalibrationSessionResponse(BaseModel):
    session_id: str
    status: str
    elapsed_seconds: float = 0.0
    remaining_seconds: float = 0.0
    result: AudioCalibrationResult | None = None
    error: str | None = None


class AudioSettingsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    active_source: str = "off"
    monitoring: bool
    active_device: str | None = None
    fallback_device: str | None = None
    active_device_name: str | None = None
    devices: tuple[AudioDeviceOption, ...] = ()
    settings: AudioTuningValues = AudioTuningValues()
    configured_noise_floor: float = 0.015
    hardware_gain_supported: bool = False
    hardware_gain_writable: bool = False
    hardware_gain_backend: str | None = None
    hardware_gain_control: str | None = None
    hardware_gain_value: float | None = None
    hardware_gain_error: str | None = None
    bluetooth: BluetoothStatusResponse = Field(default_factory=BluetoothStatusResponse)
    error: str | None = None


class AudioSettingsRequest(BaseModel):
    device: str = Field(min_length=1)
    settings: AudioTuningValues


class AudioResetRequest(BaseModel):
    device: str = Field(min_length=1)


class AudioDeviceRequest(BaseModel):
    device: str = Field(min_length=1)


class AudioSourceRequest(BaseModel):
    source: Literal["auto", "off", "demo", "mic", "bluetooth"]


class BluetoothDeviceRequest(BaseModel):
    address: str = Field(min_length=17, max_length=17)
    role: Literal["input", "output"] | None = None


class AudioOutputSelectionRequest(BaseModel):
    selector: str = Field(min_length=1, max_length=256)


class AudioOutputVolumeRequest(BaseModel):
    selector: str = Field(min_length=1, max_length=256)
    volume: float = Field(ge=0.0, le=1.0)


class AudioOutputMuteRequest(BaseModel):
    selector: str = Field(min_length=1, max_length=256)
    muted: bool


class BluetoothPowerRequest(BaseModel):
    powered: bool


class BluetoothAliasRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=64)


class StartupPlaybackState(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: PlaybackMode = PlaybackMode.STATIC
    solid_color: str = Field(default="#7C3AED", pattern=r"^#[0-9A-Fa-f]{6}$")
    animation: str = ""
    brightness: float = Field(default=1.0, ge=0.0, le=1.0)
    blackout: bool = False


class StartupSettingsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    restore_last_state: bool
    remembered: StartupPlaybackState


class StartupSettingsRequest(BaseModel):
    restore_last_state: bool


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
    hardware_gain_value: float | None = Field(default=None, ge=0.0, le=1.0)
    program_loudness: float = Field(default=0.0, ge=0.0, le=1.0)
    musical_impact: float = Field(default=0.0, ge=0.0, le=1.0)
    gate: str = "idle"
    gate_preview: bool = True
    gate_energy: float = Field(default=0.0, ge=0.0, le=1.0)
    gate_onset: float = Field(default=0.0, ge=0.0, le=1.0)
    gate_beat_density: float = Field(default=0.0, ge=0.0, le=1.0)
    gate_brightness: float = Field(default=0.0, ge=0.0, le=1.0)
    gate_spectral_balance: float = Field(default=0.0, ge=0.0, le=1.0)
    gate_reason: str = ""
    gate_checks: tuple[dict[str, object], ...] = ()
    health: str = "inactive"
