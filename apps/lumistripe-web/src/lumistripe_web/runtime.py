from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Literal, TypedDict, cast
from uuid import uuid4

import numpy as np
import numpy.typing as npt
from lumistripe import (
    AnimationPlayer,
    AudioConfig,
    AudioFrame,
    AudioInput,
    AudioInputHealth,
    AudioSnapshot,
    AudioSource,
    Color,
    ColorCorrection,
    ColorCorrectionController,
    CompositeController,
    Config,
    Controller,
    GPIOStripe,
    HardwareGainController,
    MultiController,
    MusicActivityDetector,
    MusicFeatures,
    NullController,
    PixelBuffer,
    PlaybackConfig,
    PlaybackEngine,
    PlaybackMode,
    ReversedController,
    Rgb,
    ScaledMultiController,
    SPIConfig,
    SPIStripe,
    Stripe,
    demo_snapshot,
    list_input_device_details,
)
from lumistripe.audio import BandTuple, recommend_audio_calibration

from .bluetooth import (
    BluetoothAudioBackend,
    BluetoothCommandError,
    BluetoothManager,
    BluetoothStatus,
    capture_device_selector,
)
from .models import (
    AnimationOption,
    AudioCalibrationSessionResponse,
    AudioDeviceOption,
    AudioOutputDeviceInfo,
    AudioSettingsResponse,
    AudioTelemetry,
    AudioTuningValues,
    BluetoothCapabilities,
    BluetoothDeviceInfo,
    BluetoothOperation,
    BluetoothStatusResponse,
    CalibrationSessionResponse,
    CalibrationStatus,
    ColorCorrectionProfile,
    DashboardState,
    DiagnosticIssue,
    RuntimeKind,
    StartupPlaybackState,
    StartupSettingsResponse,
    StripeOutputConfig,
    StripePlaybackState,
    StripeTopology,
)
from .models import AudioCalibrationResult as AudioCalibrationResultModel
from .settings import (
    AudioTuningProfile,
    CalibrationSettingsStore,
    StartupPlaybackSettings,
    StripeOutputSettings,
    StripeTopologySettings,
    default_settings_path,
)

MIN_FRAME_SECONDS = 0.016
FPS_SAMPLE_SECONDS = 1.0
AUDIO_ROUTE_POLL_SECONDS = 1.0
CALIBRATION_FRAME_SECONDS = 0.05
CALIBRATION_TIMEOUT_SECONDS = 300.0
BLUETOOTH_PROFILE_KEY = "Bluetooth music"
CalibrationPattern = Literal["white", "red", "green", "blue"]

try:
    APPLICATION_VERSION = metadata.version("lumistripe-web")
except metadata.PackageNotFoundError:
    APPLICATION_VERSION = "development"


class RuntimeCommandError(RuntimeError):
    """A command was valid but could not be applied by the runtime."""


class UnknownAnimationError(RuntimeCommandError):
    pass


class RuntimeUnavailableError(RuntimeError):
    pass


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
        if self.audio_source not in {"auto", "off", "demo", "mic", "bluetooth"}:
            raise ValueError(f"invalid audio source: {self.audio_source}")

    @property
    def kind(self) -> RuntimeKind:
        return RuntimeKind.HARDWARE if self.hardware else RuntimeKind.SIMULATION

    def dynamic_audio_source(self) -> AudioSource:
        if self.audio_source == "auto":
            return AudioSource.BLUETOOTH if self.hardware else AudioSource.DEMO
        return AudioSource(self.audio_source)


class OutputGateController(Controller):
    """Suppress output flushes while preserving the latest rendered frame."""

    def __init__(self, inner: Controller) -> None:
        self._inner = inner
        self._blackout = False
        self._last_successful_update_at: datetime | None = None
        self._last_successful_update_monotonic: float | None = None

    @property
    def blackout(self) -> bool:
        return self._blackout

    @property
    def last_successful_update_at(self) -> datetime | None:
        return self._last_successful_update_at

    @property
    def last_successful_update_age_seconds(self) -> float | None:
        if self._last_successful_update_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self._last_successful_update_monotonic)

    def _record_successful_update(self) -> None:
        self._last_successful_update_at = datetime.now(UTC)
        self._last_successful_update_monotonic = time.monotonic()

    def set_blackout(self, enabled: bool) -> None:
        if enabled == self._blackout:
            return
        if enabled:
            buffered_frame = self._inner.pixels().copy()
            self._inner.clear()
            self._inner.force_flush()
            self._record_successful_update()
            self._inner.set_pixels(buffered_frame)
            self._blackout = True
            return
        self._blackout = False
        self.force_flush()

    @property
    def length(self) -> int:
        return self._inner.length

    def pixels(self) -> PixelBuffer:
        return self._inner.pixels()

    def pixel(self, index: int) -> Color:
        return self._inner.pixel(index)

    def set_pixel(self, index: int, color: Color) -> None:
        self._inner.set_pixel(index, color)

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        self._inner.set_pixels(colors)

    def fill(self, color: Color) -> None:
        self._inner.fill(color)

    def clear(self) -> None:
        self._inner.clear()

    def flush(self) -> None:
        if not self._blackout:
            self._inner.flush()
            self._record_successful_update()

    def force_flush(self) -> None:
        if not self._blackout:
            self._inner.force_flush()
            self._record_successful_update()

    def close(self) -> None:
        self._inner.close()


@dataclass(slots=True)
class _Command:
    name: str
    value: object
    future: Future[object]


@dataclass(frozen=True, slots=True)
class _ModeCommand:
    mode: PlaybackMode
    color: str | None = None
    stripe_id: str | None = None
    music_recognition_enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class _TargetCommand:
    value: object
    stripe_id: str | None = None


@dataclass(frozen=True, slots=True)
class _StripeTopologyCommand:
    topology: StripeTopologySettings


@dataclass(frozen=True, slots=True)
class _StripeTestCommand:
    stripe_id: str
    pattern: str
    topology: StripeTopologySettings | None = None


@dataclass(frozen=True, slots=True)
class _CalibrationStartCommand:
    output_index: int


@dataclass(frozen=True, slots=True)
class _CalibrationUpdateCommand:
    session_id: str
    correction: ColorCorrection
    pattern: CalibrationPattern


@dataclass(frozen=True, slots=True)
class _CalibrationFinishCommand:
    session_id: str
    save: bool


@dataclass(frozen=True, slots=True)
class _AudioSettingsCommand:
    device: str
    profile: AudioTuningProfile


@dataclass(frozen=True, slots=True)
class _AudioDeviceCommand:
    device: str


@dataclass(frozen=True, slots=True)
class _AudioSourceCommand:
    source: str


@dataclass(frozen=True, slots=True)
class _AudioCalibrationStartCommand:
    device: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class _AudioCalibrationFinishCommand:
    session_id: str
    apply: bool
    target_level: float | None = None
    noise_floor: float | None = None


@dataclass(frozen=True, slots=True)
class _StartupSettingsCommand:
    restore_last_state: bool


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


@dataclass(frozen=True, slots=True)
class PreviewFrame:
    """An immutable snapshot of the latest rendered output frame."""

    sequence: int
    outputs: tuple[bytes, ...]


@dataclass(slots=True)
class _StripeTestSession:
    stripe_id: str
    pattern: str
    expires_at: float
    restore_topology: StripeTopologySettings | None = None


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


class _AudioCalibrationResult(TypedDict):
    duration_seconds: float
    samples: int
    measured_floor: float
    measured_peak: float
    recommended_noise_floor: float
    recommended_target_level: float
    recommended_hardware_gain: float | None
    recommended_idle_threshold_scale: float


_ControllerFactory = Callable[[RuntimeSettings], Controller]
_AudioFactory = Callable[[str | None, AudioConfig], AudioInput]


def _default_controller_factory(settings: RuntimeSettings) -> Controller:
    if not settings.hardware:
        return Stripe(settings.pixels)
    if settings.output_backend == "gpio":
        return GPIOStripe(
            Config(
                chip=settings.chip,
                gpio_data=settings.data_pin,
                gpio_clock=settings.clock_pin,
                consumer="lumistripe-web",
            ),
            settings.pixels,
        )
    primary = SPIStripe(
        SPIConfig(device=settings.spi_device, speed_hz=settings.spi_speed_hz),
        settings.pixels,
    )
    if settings.spi_device_2 is None:
        return primary
    try:
        secondary = SPIStripe(
            SPIConfig(
                device=settings.spi_device_2,
                speed_hz=settings.spi_speed_hz_2 or settings.spi_speed_hz,
            ),
            settings.pixels,
        )
    except Exception:
        primary.close()
        raise
    return MultiController([primary, secondary])


def _default_audio_factory(device: str | None, config: AudioConfig) -> AudioInput:
    return (
        AudioInput.with_device_config(device, config)
        if device
        else AudioInput.with_config(config)
    )


def _topology_from_runtime(settings: RuntimeSettings) -> StripeTopologySettings:
    primary = StripeOutputSettings(
        id="primary",
        name="Primary",
        pixels=settings.pixels,
        backend=cast(Literal["spi", "gpio"], settings.output_backend),
        spi_device=settings.spi_device,
        spi_speed_hz=settings.spi_speed_hz,
        chip=settings.chip,
        data_pin=settings.data_pin,
        clock_pin=settings.clock_pin,
    )
    outputs = [primary]
    if settings.output_backend == "spi" and settings.spi_device_2 is not None:
        outputs.append(
            StripeOutputSettings(
                id="secondary",
                name="Secondary",
                pixels=settings.pixels,
                backend="spi",
                spi_device=settings.spi_device_2,
                spi_speed_hz=settings.spi_speed_hz_2 or settings.spi_speed_hz,
            )
        )
    return StripeTopologySettings(layout="mirrored", outputs=tuple(outputs))


class LumiStripeRuntime:
    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        *,
        controller_factory: _ControllerFactory = _default_controller_factory,
        audio_factory: _AudioFactory = _default_audio_factory,
        bluetooth_manager: BluetoothAudioBackend | None = None,
    ) -> None:
        self.settings = settings or RuntimeSettings()
        self._controller_factory = controller_factory
        self._uses_default_controller_factory = (
            controller_factory is _default_controller_factory
        )
        self._audio_factory = audio_factory
        self._bluetooth = bluetooth_manager or BluetoothManager(enabled=self.settings.hardware)
        self._settings_store = CalibrationSettingsStore(self.settings.settings_file)
        loaded_settings, self._settings_warning = self._settings_store.load_all()
        self._saved_corrections = loaded_settings.color_corrections
        self._topology = (
            _topology_from_runtime(self.settings)
            if self.settings.ignore_saved_stripes
            else loaded_settings.stripe_topology
            or _topology_from_runtime(self.settings)
        )
        self._audio_profiles = loaded_settings.audio_profiles
        self._selected_audio_device = (
            loaded_settings.selected_audio_device or self.settings.audio_device
        )
        self._restore_last_state = loaded_settings.restore_last_state
        self._remembered_playback = loaded_settings.remembered_playback
        self._settings_io_lock = threading.RLock()
        self._startup_persist_lock = threading.Lock()
        self._startup_persist_timer: threading.Timer | None = None
        self._audio_profile = self._audio_profiles.get(
            self._selected_audio_device or "", AudioTuningProfile()
        )
        self.player = AnimationPlayer.party()
        self.playback = PlaybackEngine(
            self.player,
            PlaybackConfig(
                mode=PlaybackMode.STATIC,
                activity=self._audio_profile.activity_config(),
                dynamic_response=self._audio_profile.dynamic_response,
            ),
        )
        self._monitor_detector = MusicActivityDetector(
            self._audio_profile.activity_config()
        )
        self.player.set_brightness(1.0)

        self._commands: queue.Queue[_Command | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._started_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot_lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._revision = 0
        self._preview_sequence = 0
        self._preview_outputs: tuple[bytes, ...] = ()
        self._snapshot = DashboardState(
            runtime=self.settings.kind,
            mode=PlaybackMode.STATIC,
            animation=self._current_animation(),
        )

        self._raw_controller: Controller | None = None
        self._controller: OutputGateController | None = None
        self._output_gates: tuple[OutputGateController, ...] = ()
        self._playbacks: dict[str, tuple[AnimationPlayer, PlaybackEngine]] = {}
        self._correction_controllers: tuple[ColorCorrectionController, ...] = ()
        self._calibration: _CalibrationSession | None = None
        self._audio_calibration: _AudioCalibrationSession | None = None
        self._stripe_test: _StripeTestSession | None = None
        self._audio_input: AudioInput | None = None
        self._hardware_gain: HardwareGainController | None = None
        self._audio_source_setting = self.settings.audio_source
        self._configured_audio_source = self.settings.dynamic_audio_source()
        self._active_audio_source = AudioSource.OFF
        self._monitor_audio_source = AudioSource.OFF
        self._active_audio_device_name: str | None = None
        self._active_bluetooth_address: str | None = None
        self._bluetooth_previous_default_source: str | None = None
        self._next_audio_route_check_at = 0.0
        self._audio_frame = AudioFrame()
        self._music_features = MusicFeatures()
        self._demo_tick = 0
        self._audio_status = "No audio source active."
        self._audio_monitor_error: str | None = None
        self._noise_samples: deque[float] = deque(maxlen=300)
        self._audio_telemetry = AudioTelemetry()
        self._fatal_error: str | None = None
        self._last_command_error: str | None = None
        self._started_at_s: float | None = None
        self._fps_window_started_s: float | None = None
        self._fps_window_frames = 0
        self._frame_rate = 0.0
        self._audio_health = AudioInputHealth()

    @property
    def healthy(self) -> bool:
        state = self.snapshot()
        return state.running and state.error is None

    def start(self, *, timeout: float = 5.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._started_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="lumistripe-runtime",
            daemon=False,
        )
        self._thread.start()
        if not self._started_event.wait(timeout):
            raise RuntimeUnavailableError("runtime startup timed out")

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._commands.put(None)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise RuntimeUnavailableError("runtime shutdown timed out")
        self._thread = None

    def snapshot(self) -> DashboardState:
        with self._snapshot_lock:
            return self._snapshot

    def preview_frame(self) -> PreviewFrame:
        with self._preview_lock:
            return PreviewFrame(self._preview_sequence, self._preview_outputs)

    def animations(self) -> tuple[AnimationOption, ...]:
        return tuple(
            AnimationOption(
                name=entry.animation.name,
                mood=entry.animation.metadata.mood,
                dynamic_safe=entry.animation.metadata.dynamic_safe,
            )
            for entry in self.player.animations
        )

    def audio_telemetry(self) -> AudioTelemetry:
        with self._snapshot_lock:
            return self._audio_telemetry

    def audio_settings(self) -> AudioSettingsResponse:
        try:
            devices = list_input_device_details()
            options_list = [
                AudioDeviceOption(
                    selector=str(device.index),
                    name=device.name,
                    settings=_profile_values(
                        self._audio_profiles.get(device.name, AudioTuningProfile())
                    ),
                )
                for device in devices
            ]
            if self.settings.hardware:
                options_list.append(
                    AudioDeviceOption(
                        selector="bluetooth",
                        name=BLUETOOTH_PROFILE_KEY,
                        settings=_profile_values(
                            self._audio_profiles.get(
                                BLUETOOTH_PROFILE_KEY, AudioTuningProfile()
                            )
                        ),
                    )
                )
            options = tuple(options_list)
            enumeration_error = None
        except RuntimeError as exc:
            options = ()
            enumeration_error = str(exc)
        active_name = self._active_audio_device_name
        if active_name is None and self._audio_input is not None:
            active_name = self._audio_input.device_name()
        selected_name = (
            BLUETOOTH_PROFILE_KEY
            if self._monitor_audio_source is AudioSource.BLUETOOTH
            else (active_name or self._selected_audio_device)
        )
        active_selector = next(
            (option.selector for option in options if option.name == selected_name),
            "bluetooth"
            if self._monitor_audio_source is AudioSource.BLUETOOTH
            else selected_name,
        )
        fallback_selector = next(
            (
                option.selector
                for option in options
                if option.name == self._selected_audio_device
            ),
            self._selected_audio_device,
        )
        return AudioSettingsResponse(
            source=self._audio_source_setting,
            active_source=self._monitor_audio_source.value,
            monitoring=self._audio_input is not None,
            active_device=active_selector,
            fallback_device=fallback_selector,
            active_device_name=active_name,
            devices=options,
            settings=_profile_values(
                self._audio_profiles.get(
                    BLUETOOTH_PROFILE_KEY
                    if self._monitor_audio_source is AudioSource.BLUETOOTH
                    else (active_name or self._selected_audio_device or ""),
                    self._audio_profile,
                )
            ),
            configured_noise_floor=self._audio_profile.audio_config().smoothing.noise_floor,
            **_hardware_gain_values(self._hardware_gain),
            bluetooth=self._bluetooth_status_response(),
            error=self._audio_monitor_error or enumeration_error,
        )

    def bluetooth_status(self) -> BluetoothStatusResponse:
        return self._bluetooth_status_response()

    def _bluetooth_status_response(
        self, status: BluetoothStatus | None = None
    ) -> BluetoothStatusResponse:
        current = status or self._bluetooth.status()

        def device_info(device) -> BluetoothDeviceInfo:
            return BluetoothDeviceInfo(
                address=device.address,
                name=device.name,
                paired=device.paired,
                connected=device.connected,
                roles=tuple(device.roles),
            )

        connected = current.connected_device
        return BluetoothStatusResponse(
            available=current.available,
            powered=current.powered,
            adapter_alias=current.adapter_alias,
            scanning=current.scanning,
            streaming=current.streaming,
            devices=tuple(device_info(device) for device in current.devices),
            connected_inputs=tuple(device_info(device) for device in current.connected_inputs),
            connected_outputs=tuple(device_info(device) for device in current.connected_outputs),
            connected_device=device_info(connected) if connected is not None else None,
            input_source=current.input_source,
            output_devices=tuple(
                AudioOutputDeviceInfo(
                    selector=device.selector,
                    name=device.name,
                    volume=device.volume,
                    muted=device.muted,
                    bluetooth=device.bluetooth,
                    connected=device.connected,
                )
                for device in current.output_devices
            ),
            default_sink=current.default_sink,
            output_volume=current.output_volume,
            output_muted=current.output_muted,
            output_ready=current.output_ready,
            capabilities=BluetoothCapabilities(
                operations=cast(
                    tuple[BluetoothOperation, ...],
                    tuple(current.capabilities.operations),
                ),
                max_inputs=current.capabilities.max_inputs,
                max_outputs=current.capabilities.max_outputs,
            ),
            operation=current.operation,
            operation_id=current.operation_id,
            operation_state=current.operation_state,
            error=current.error,
        )

    def start_bluetooth_scan(self) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(self._bluetooth.start_scan())
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def set_bluetooth_power(self, powered: bool) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(self._bluetooth.set_power(powered))
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def set_bluetooth_alias(self, alias: str) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(self._bluetooth.set_alias(alias))
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def pair_bluetooth_device(self, address: str) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(self._bluetooth.pair(address))
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def connect_bluetooth_device(
        self, address: str, role: str | None = None
    ) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(self._bluetooth.connect(address, role))
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def forget_bluetooth_device(self, address: str) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(self._bluetooth.forget(address))
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def disconnect_bluetooth_device(self, address: str) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(self._bluetooth.disconnect(address))
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def set_audio_output(self, selector: str) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(
                self._bluetooth.set_default_sink(selector)
            )
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def set_audio_output_volume(
        self, selector: str, volume: float
    ) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(
                self._bluetooth.set_output_volume(selector, volume)
            )
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def set_audio_output_mute(
        self, selector: str, muted: bool
    ) -> BluetoothStatusResponse:
        try:
            return self._bluetooth_status_response(
                self._bluetooth.set_output_mute(selector, muted)
            )
        except BluetoothCommandError as exc:
            raise RuntimeCommandError(str(exc)) from exc

    def startup_settings(self) -> StartupSettingsResponse:
        remembered = self._remembered_playback
        return StartupSettingsResponse(
            restore_last_state=self._restore_last_state,
            remembered=StartupPlaybackState(
                mode=remembered.mode,
                solid_color=remembered.solid_color,
                animation=remembered.animation,
                brightness=remembered.brightness,
                blackout=remembered.blackout,
            ),
        )

    def select_audio_device(self, device: str) -> Future[AudioSettingsResponse]:
        return cast(
            Future[AudioSettingsResponse],
            self._submit("audio_device", _AudioDeviceCommand(device)),
        )

    def set_audio_source(self, source: str) -> Future[AudioSettingsResponse]:
        return cast(
            Future[AudioSettingsResponse],
            self._submit("audio_source", _AudioSourceCommand(source)),
        )

    def set_startup_restore(
        self, enabled: bool
    ) -> Future[StartupSettingsResponse]:
        return cast(
            Future[StartupSettingsResponse],
            self._submit("startup_settings", _StartupSettingsCommand(enabled)),
        )

    def apply_audio_settings(
        self, device: str, profile: AudioTuningProfile
    ) -> Future[AudioSettingsResponse]:
        return cast(
            Future[AudioSettingsResponse],
            self._submit("audio_settings", _AudioSettingsCommand(device, profile)),
        )

    def reset_audio_settings(self, device: str) -> Future[AudioSettingsResponse]:
        return self.apply_audio_settings(device, AudioTuningProfile())

    def start_audio_calibration(self, device: str, duration_seconds: float) -> Future[AudioCalibrationSessionResponse]:
        return cast(Future[AudioCalibrationSessionResponse], self._submit("audio_calibration_start", _AudioCalibrationStartCommand(device, duration_seconds)))

    def audio_calibration_status(self, session_id: str) -> AudioCalibrationSessionResponse:
        session = self._audio_calibration
        if session is None or session.session_id != session_id:
            raise RuntimeCommandError("audio calibration session not found")
        elapsed = min(session.duration_seconds, max(0.0, time.monotonic() - session.started_at))
        status = "complete" if session.result is not None else "capturing"
        result = None
        if session.result is not None:
            result = AudioCalibrationResultModel(**session.result)
        return AudioCalibrationSessionResponse(
            session_id=session.session_id,
            status=status,
            elapsed_seconds=elapsed,
            remaining_seconds=max(0.0, session.duration_seconds - elapsed),
            result=result,
            error=session.error,
        )

    def finish_audio_calibration(self, session_id: str, *, apply: bool, target_level: float | None = None, noise_floor: float | None = None) -> Future[AudioSettingsResponse | AudioCalibrationSessionResponse]:
        return cast(Future[AudioSettingsResponse | AudioCalibrationSessionResponse], self._submit("audio_calibration_finish", _AudioCalibrationFinishCommand(session_id, apply, target_level, noise_floor)))

    def set_mode(
        self,
        mode: PlaybackMode,
        *,
        solid_color: str | None = None,
        stripe_id: str | None = None,
        music_recognition_enabled: bool | None = None,
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit("mode", _ModeCommand(mode, solid_color, stripe_id, music_recognition_enabled)),
        )

    def set_brightness(
        self, brightness: float, *, stripe_id: str | None = None
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit("brightness", _TargetCommand(float(brightness), stripe_id)),
        )

    def select_animation(
        self, name: str, *, stripe_id: str | None = None
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit("animation", _TargetCommand(name, stripe_id)),
        )

    def set_blackout(
        self, enabled: bool, *, stripe_id: str | None = None
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit("blackout", _TargetCommand(bool(enabled), stripe_id)),
        )

    def apply_stripe_topology(
        self, topology: StripeTopologySettings
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit("stripe_topology", _StripeTopologyCommand(topology)),
        )

    def test_stripe(
        self,
        stripe_id: str,
        pattern: str,
        topology: StripeTopologySettings | None = None,
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "stripe_test", _StripeTestCommand(stripe_id, pattern, topology)
            ),
        )

    def start_calibration(
        self, output_index: int
    ) -> Future[CalibrationSessionResponse]:
        return cast(
            Future[CalibrationSessionResponse],
            self._submit("calibration_start", _CalibrationStartCommand(output_index)),
        )

    def update_calibration(
        self,
        session_id: str,
        correction: ColorCorrection,
        pattern: CalibrationPattern,
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "calibration_update",
                _CalibrationUpdateCommand(session_id, correction, pattern),
            ),
        )

    def finish_calibration(
        self, session_id: str, *, save: bool
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "calibration_finish",
                _CalibrationFinishCommand(session_id, save),
            ),
        )

    def _submit(self, name: str, value: object) -> Future[object]:
        future: Future[object] = Future()
        thread = self._thread
        if thread is None or not thread.is_alive() or not self.healthy:
            future.set_exception(
                RuntimeUnavailableError(self._fatal_error or "runtime is not running")
            )
            return future
        self._commands.put(_Command(name, value, future))
        return future

    def _run(self) -> None:
        try:
            if self._uses_default_controller_factory:
                built = self._build_topology(self._topology)
                self._install_topology(built)
            else:
                physical_controller = self._controller_factory(self.settings)
                self._raw_controller = self._with_color_correction(physical_controller)
                self._controller = OutputGateController(self._raw_controller)
            self._started_at_s = time.monotonic()
            self._fps_window_started_s = self._started_at_s
            self._bluetooth.start()
            self._initialize_audio_monitor()
            self._apply_remembered_startup()
            self._publish(running=True)
            self._started_event.set()
            self._frame_loop()
        except Exception as exc:  # noqa: BLE001 - a thread boundary must publish all failures
            self._fatal_error = str(exc)
            self._publish(running=False, error=self._fatal_error)
            self._started_event.set()
        finally:
            self._cleanup()
            self._reject_pending()
            self._publish(running=False, error=self._fatal_error)

    def _build_topology(self, topology: StripeTopologySettings) -> _BuiltTopology:
        physical: list[Controller] = []
        corrected: list[ColorCorrectionController] = []
        gates: list[OutputGateController] = []
        try:
            for index, output in enumerate(topology.outputs):
                if not self.settings.hardware:
                    child: Controller = Stripe(output.pixels)
                elif output.backend == "gpio":
                    child = GPIOStripe(
                        Config(
                            chip=output.chip,
                            gpio_data=output.data_pin,
                            gpio_clock=output.clock_pin,
                            consumer=f"lumistripe-web-{output.id}",
                        ),
                        output.pixels,
                    )
                else:
                    child = SPIStripe(
                        SPIConfig(
                            device=output.spi_device, speed_hz=output.spi_speed_hz
                        ),
                        output.pixels,
                    )
                physical.append(child)
                oriented = ReversedController(child) if output.reversed else child
                fallback = self._saved_corrections.get(
                    _profile_name(index), ColorCorrection()
                )
                correction = ColorCorrectionController(
                    oriented,
                    self._saved_corrections.get(output.id, fallback),
                )
                corrected.append(correction)
                gates.append(OutputGateController(correction))

            if not gates:
                raw: Controller = NullController()
                shared = OutputGateController(raw)
            elif topology.layout == "continuous":
                raw = CompositeController(gates)
                shared = OutputGateController(raw)
            elif topology.layout == "mirrored":
                raw = ScaledMultiController(gates)
                shared = OutputGateController(raw)
            else:
                # Independent outputs are stepped individually; this aggregate owns cleanup.
                raw = CompositeController(gates)
                shared = OutputGateController(raw)
            return _BuiltTopology(raw, shared, tuple(gates), tuple(corrected))
        except Exception:
            for child in physical:
                try:
                    child.close()
                except Exception:  # noqa: BLE001, S110 - preserve the original build error
                    pass
            raise

    def _install_topology(self, built: _BuiltTopology) -> None:
        self._raw_controller = built.raw
        self._controller = built.shared
        self._output_gates = built.outputs
        self._correction_controllers = built.corrections
        self._sync_independent_playbacks()

    def _sync_independent_playbacks(self) -> None:
        active_ids = {output.id for output in self._topology.outputs}
        self._playbacks = {
            stripe_id: value
            for stripe_id, value in self._playbacks.items()
            if stripe_id in active_ids
        }
        for output in self._topology.outputs:
            if output.id in self._playbacks:
                continue
            player = AnimationPlayer.party()
            player.set_brightness(self.player.brightness)
            engine = PlaybackEngine(
                player,
                PlaybackConfig(
                    mode=self.playback.mode,
                    solid_color=self.playback.solid_color,
                    activity=self._audio_profile.activity_config(),
                    dynamic_response=self._audio_profile.dynamic_response,
                ),
            )
            current = self.player.name_at(self.player.current_index())
            if current and player.index_of(current) is not None:
                engine.select_animation(current)
            self._playbacks[output.id] = (player, engine)

    def _frame_loop(self) -> None:
        next_frame_at = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            timeout = max(0.0, min(next_frame_at - now, 0.05))
            try:
                command = self._commands.get(timeout=timeout)
            except queue.Empty:
                command = None
            if command is not None:
                self._execute(command)
                continue
            if self._stop_event.is_set():
                break
            now = time.monotonic()
            if now < next_frame_at:
                continue
            next_frame_at = now + self._step()

    def _execute(self, command: _Command) -> None:
        if command.future.cancelled():
            return
        try:
            self._last_command_error = None
            if self._calibration is not None and not command.name.startswith(
                "calibration_"
            ):
                raise RuntimeCommandError(
                    "finish or cancel color calibration before changing the lights"
                )
            result: object
            if command.name == "calibration_start":
                start = _expect(command.value, _CalibrationStartCommand)
                result = self._start_calibration(start.output_index)
            elif command.name == "calibration_update":
                update = _expect(command.value, _CalibrationUpdateCommand)
                self._update_calibration(update)
                result = self._publish(running=True)
            elif command.name == "calibration_finish":
                finish = _expect(command.value, _CalibrationFinishCommand)
                self._finish_calibration(finish.session_id, save=finish.save)
                result = self._publish(running=True)
            elif command.name == "mode":
                mode_command = _expect(command.value, _ModeCommand)
                if (
                    mode_command.color is not None
                    and mode_command.mode is not PlaybackMode.SOLID
                ):
                    raise RuntimeCommandError("color can only be set for solid mode")
                color = (
                    _rgb_from_hex(mode_command.color)
                    if mode_command.color is not None
                    else None
                )
                self._apply_mode(mode_command.mode, color, mode_command.stripe_id, mode_command.music_recognition_enabled)
                if mode_command.stripe_id is None:
                    self._remember_shared_playback(
                        mode=mode_command.mode,
                        solid_color=_color_to_hex(color) if color is not None else None,
                    )
                result = self._publish(running=True)
            elif command.name == "brightness":
                target = _expect(command.value, _TargetCommand)
                value = _expect(target.value, float)
                for player, _ in self._target_playbacks(target.stripe_id):
                    player.set_brightness(value)
                if target.stripe_id is None:
                    self._remember_shared_playback(brightness=value)
                result = self._publish(running=True)
            elif command.name == "animation":
                target = _expect(command.value, _TargetCommand)
                name = _expect(target.value, str)
                if self.player.index_of(name) is None:
                    raise UnknownAnimationError(f"unknown animation: {name}")
                self._apply_mode(PlaybackMode.STATIC, None, target.stripe_id)
                for _, playback in self._target_playbacks(target.stripe_id):
                    playback.select_animation(name)
                if target.stripe_id is None:
                    self._remember_shared_playback(
                        mode=PlaybackMode.STATIC, animation=name
                    )
                result = self._publish(running=True)
            elif command.name == "blackout":
                target = _expect(command.value, _TargetCommand)
                enabled = _expect(target.value, bool)
                for gate in self._target_gates(target.stripe_id):
                    gate.set_blackout(enabled)
                if target.stripe_id is None:
                    self._remember_shared_playback(blackout=enabled)
                result = self._publish(running=True)
            elif command.name == "audio_settings":
                audio_update = _expect(command.value, _AudioSettingsCommand)
                self._apply_audio_settings(audio_update.device, audio_update.profile)
                self._publish(running=True)
                result = self.audio_settings()
            elif command.name == "audio_device":
                audio_device = _expect(command.value, _AudioDeviceCommand)
                self._select_audio_device(audio_device.device)
                self._publish(running=True)
                result = self.audio_settings()
            elif command.name == "audio_source":
                audio_source = _expect(command.value, _AudioSourceCommand)
                self._set_audio_source(audio_source.source)
                self._publish(running=True)
                result = self.audio_settings()
            elif command.name == "audio_calibration_start":
                calibration = _expect(command.value, _AudioCalibrationStartCommand)
                result = self._start_audio_calibration(calibration.device, calibration.duration_seconds)
            elif command.name == "audio_calibration_finish":
                calibration_finish = _expect(
                    command.value, _AudioCalibrationFinishCommand
                )
                result = self._finish_audio_calibration(calibration_finish)
            elif command.name == "startup_settings":
                startup = _expect(command.value, _StartupSettingsCommand)
                self._set_startup_restore(startup.restore_last_state)
                result = self.startup_settings()
            elif command.name == "stripe_topology":
                topology_update = _expect(command.value, _StripeTopologyCommand)
                self._apply_stripe_topology(topology_update.topology)
                result = self._publish(running=True)
            elif command.name == "stripe_test":
                test = _expect(command.value, _StripeTestCommand)
                self._test_stripe(test.stripe_id, test.pattern, test.topology)
                result = self._publish(running=True)
            else:
                raise RuntimeCommandError(f"unknown runtime command: {command.name}")
            if not command.future.done():
                command.future.set_result(result)
        except Exception as exc:  # noqa: BLE001 - every command must resolve its future
            self._last_command_error = str(exc)
            self._publish(running=True)
            if not command.future.done():
                command.future.set_exception(exc)

    def _target_playbacks(
        self, stripe_id: str | None
    ) -> tuple[tuple[AnimationPlayer, PlaybackEngine], ...]:
        if self._topology.layout != "independent":
            if stripe_id is not None:
                raise RuntimeCommandError(
                    "a stripe target is only available in independent layout"
                )
            return ((self.player, self.playback),)
        if stripe_id is None:
            return tuple(
                self._playbacks[output.id] for output in self._topology.outputs
            )
        target = self._playbacks.get(stripe_id)
        if target is None:
            raise RuntimeCommandError(f"unknown stripe: {stripe_id}")
        return (target,)

    def _target_gates(self, stripe_id: str | None) -> tuple[OutputGateController, ...]:
        assert self._controller is not None
        if self._topology.layout != "independent":
            if stripe_id is not None:
                raise RuntimeCommandError(
                    "a stripe target is only available in independent layout"
                )
            return (self._controller,)
        if stripe_id is None:
            return self._output_gates
        for output, gate in zip(
            self._topology.outputs, self._output_gates, strict=True
        ):
            if output.id == stripe_id:
                return (gate,)
        raise RuntimeCommandError(f"unknown stripe: {stripe_id}")

    def _apply_mode(
        self, mode: PlaybackMode, color: Color | None, stripe_id: str | None,
        music_recognition_enabled: bool | None = None,
    ) -> None:
        if mode is PlaybackMode.DYNAMIC:
            source = self._configured_audio_source
            if source is AudioSource.OFF:
                raise RuntimeCommandError(
                    "dynamic mode requires demo or microphone audio"
                )
            if source in {AudioSource.MIC, AudioSource.BLUETOOTH} and self._audio_input is None:
                raise RuntimeCommandError(
                    self._audio_monitor_error
                    or "the selected audio input is unavailable"
                )
            self._active_audio_source = source
            self._audio_status = (
                "Using internal demo beat."
                if source is AudioSource.DEMO
                else (
                    (
                        f"Bluetooth: {self._active_audio_device_name}"
                        if self._monitor_audio_source is AudioSource.BLUETOOTH
                        else f"Input: {self._audio_input.device_name()}"
                    )
                    if self._audio_input is not None
                    else self._audio_status
                )
            )
        for _, playback in self._target_playbacks(stripe_id):
            if color is not None:
                playback.set_solid_color(color)
            playback.set_mode(mode)
            if mode is PlaybackMode.DYNAMIC:
                playback.set_music_recognition_enabled(True)
            elif music_recognition_enabled is not None:
                playback.set_music_recognition_enabled(music_recognition_enabled)
        if mode is not PlaybackMode.DYNAMIC and not any(
            engine.mode is PlaybackMode.DYNAMIC
            for _, engine in self._target_playbacks(None)
        ):
            self._active_audio_source = AudioSource.OFF
            self._audio_status = "No audio source active."

    def _apply_stripe_topology(self, topology: StripeTopologySettings) -> None:
        if not self._uses_default_controller_factory:
            raise RuntimeCommandError(
                "live stripe configuration is unavailable with a custom controller"
            )
        previous = self._topology
        self._stripe_test = None
        old_raw = self._raw_controller
        if old_raw is not None:
            try:
                old_raw.clear()
                old_raw.force_flush()
            finally:
                old_raw.close()
        try:
            built = self._build_topology(topology)
        except Exception as exc:
            try:
                self._topology = previous
                self._install_topology(self._build_topology(previous))
            except Exception as rollback_exc:
                self._fatal_error = (
                    f"configuration failed: {exc}; rollback failed: {rollback_exc}"
                )
                raise RuntimeCommandError(self._fatal_error) from rollback_exc
            raise RuntimeCommandError(f"configuration was not applied: {exc}") from exc
        if previous.layout == "independent" and topology.layout != "independent":
            first = previous.outputs[0].id if previous.outputs else None
            source = self._playbacks.get(first) if first is not None else None
            if source is not None:
                self._copy_playback_state(source, (self.player, self.playback))
        elif previous.layout != "independent" and topology.layout == "independent":
            self._playbacks.clear()
        self._topology = topology
        self._install_topology(built)
        try:
            with self._settings_io_lock:
                self._settings_store.save_stripes(topology)
        except OSError as exc:
            # Hardware is usable, but make the persistence failure explicit.
            raise RuntimeCommandError(
                f"configuration is active but could not be saved: {exc}"
            ) from exc

    @staticmethod
    def _copy_playback_state(
        source: tuple[AnimationPlayer, PlaybackEngine],
        target: tuple[AnimationPlayer, PlaybackEngine],
    ) -> None:
        source_player, source_engine = source
        target_player, target_engine = target
        target_player.set_brightness(source_player.brightness)
        animation = source_player.name_at(source_player.current_index())
        if animation and target_player.index_of(animation) is not None:
            target_engine.select_animation(animation)
        target_engine.set_solid_color(source_engine.solid_color)
        target_engine.set_mode(source_engine.mode)

    def _test_stripe(
        self,
        stripe_id: str,
        pattern: str,
        topology: StripeTopologySettings | None,
    ) -> None:
        colors = {
            "identify": Rgb(255, 255, 255),
            "white": Rgb(255, 255, 255),
            "red": Rgb(255, 0, 0),
            "green": Rgb(0, 255, 0),
            "blue": Rgb(0, 0, 255),
        }
        if pattern not in colors:
            raise RuntimeCommandError(f"unknown test pattern: {pattern}")
        if self._stripe_test is not None:
            self._finish_stripe_test()
        restore_topology: StripeTopologySettings | None = None
        if topology is not None and topology != self._topology:
            restore_topology = self._topology
            self._replace_topology(topology)
        if not any(output.id == stripe_id for output in self._topology.outputs):
            if restore_topology is not None:
                self._replace_topology(restore_topology)
            raise RuntimeCommandError(f"unknown stripe: {stripe_id}")
        self._stripe_test = _StripeTestSession(
            stripe_id, pattern, time.monotonic() + 3.0, restore_topology
        )
        self._render_stripe_test()

    def _render_stripe_test(self) -> None:
        session = self._stripe_test
        if session is None:
            return
        stripe_id, pattern = session.stripe_id, session.pattern
        colors = {
            "white": Rgb(255, 255, 255),
            "red": Rgb(255, 0, 0),
            "green": Rgb(0, 255, 0),
            "blue": Rgb(0, 0, 255),
        }
        for output, controller in zip(
            self._topology.outputs, self._correction_controllers, strict=True
        ):
            controller.clear()
            if output.id == stripe_id:
                if pattern == "identify":
                    position = int(time.monotonic() * 8) % controller.length
                    controller.set_pixel(position, Rgb(255, 255, 255))
                else:
                    controller.fill(colors[pattern])
            controller.force_flush()

    def _finish_stripe_test(self) -> None:
        session = self._stripe_test
        self._stripe_test = None
        if session is not None and session.restore_topology is not None:
            self._replace_topology(session.restore_topology)

    def _replace_topology(self, topology: StripeTopologySettings) -> None:
        previous = self._topology
        old_raw = self._raw_controller
        if old_raw is not None:
            try:
                old_raw.clear()
                old_raw.force_flush()
            finally:
                old_raw.close()
        try:
            built = self._build_topology(topology)
        except Exception as exc:
            try:
                self._topology = previous
                self._install_topology(self._build_topology(previous))
            except Exception as rollback_exc:
                self._fatal_error = (
                    f"temporary configuration failed: {exc}; "
                    f"rollback failed: {rollback_exc}"
                )
                raise RuntimeCommandError(self._fatal_error) from rollback_exc
            raise RuntimeCommandError(f"temporary configuration failed: {exc}") from exc
        self._topology = topology
        self._install_topology(built)

    def _initialize_audio_monitor(self) -> None:
        self._maintain_audio_monitor(force=True)

    def _maintain_audio_monitor(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now < self._next_audio_route_check_at:
            return
        self._next_audio_route_check_at = now + AUDIO_ROUTE_POLL_SECONDS

        if self._configured_audio_source is AudioSource.DEMO:
            if self._audio_input is not None:
                self._close_audio_input()
            self._monitor_audio_source = AudioSource.DEMO
            self._active_audio_device_name = None
            self._audio_status = "Using internal demo beat."
            self._audio_monitor_error = None
            return
        if self._configured_audio_source is AudioSource.OFF:
            if self._audio_input is not None:
                self._close_audio_input()
            self._monitor_audio_source = AudioSource.OFF
            self._audio_status = "No audio source active."
            self._audio_monitor_error = None
            return

        bluetooth = self._bluetooth.status()
        bluetooth_ready = bluetooth.streaming and bluetooth.input_source is not None
        wants_bluetooth = self._configured_audio_source is AudioSource.BLUETOOTH
        use_bluetooth = wants_bluetooth and bluetooth_ready
        if self._audio_source_setting == "auto":
            use_bluetooth = bluetooth_ready

        if use_bluetooth:
            connected_address = (
                bluetooth.connected_device.address
                if bluetooth.connected_device is not None
                else None
            )
            if (
                self._audio_input is not None
                and self._monitor_audio_source is AudioSource.BLUETOOTH
                and self._active_bluetooth_address == connected_address
            ):
                return
            if self._audio_input is not None:
                self._close_audio_input()
            try:
                self._open_bluetooth_monitor(bluetooth)
            except (BluetoothCommandError, RuntimeError) as exc:
                self._audio_monitor_error = str(exc)
                self._audio_status = f"Bluetooth audio unavailable: {exc}"
            return

        if self._audio_source_setting == "bluetooth":
            if self._audio_input is not None:
                self._close_audio_input()
            self._monitor_audio_source = AudioSource.BLUETOOTH
            self._active_audio_device_name = None
            self._audio_status = (
                "Bluetooth is connected but its PipeWire input is not ready."
                if bluetooth.connected_device is not None
                else "Connect a Bluetooth audio source to LumiStripe."
            )
            self._audio_monitor_error = bluetooth.error
            return

        if (
            self._audio_input is not None
            and self._monitor_audio_source is AudioSource.MIC
        ):
            return
        if self._audio_input is not None:
            self._close_audio_input()
        try:
            self._open_microphone_monitor()
        except RuntimeError as exc:
            self._audio_monitor_error = str(exc)
            self._audio_status = f"Microphone unavailable: {exc}"

    def _open_microphone_monitor(self) -> None:
        profile = self._audio_profiles.get(
            self._selected_audio_device or "", AudioTuningProfile()
        )
        audio_input = self._audio_factory(
            self._selected_audio_device,
            profile.audio_config(),
        )
        device_name = audio_input.device_name()
        saved_profile = self._audio_profiles.get(device_name)
        if saved_profile is not None and saved_profile != profile:
            audio_input.reconfigure(saved_profile.audio_config())
            profile = saved_profile
        self._selected_audio_device = device_name
        self._audio_profile = profile
        self._audio_input = audio_input
        self._monitor_audio_source = AudioSource.MIC
        self._active_audio_device_name = device_name
        self._active_bluetooth_address = None
        self._hardware_gain = HardwareGainController(device_name)
        if profile.hardware_gain_target is not None:
            self._hardware_gain.set_normalized(profile.hardware_gain_target)
        self._apply_audio_profile(profile)
        self._audio_status = f"Input: {device_name}"
        self._audio_monitor_error = None

    def _open_bluetooth_monitor(self, status: BluetoothStatus) -> None:
        device = status.connected_device
        source = status.input_source
        if device is None or source is None:
            raise BluetoothCommandError("the connected Bluetooth input has no PipeWire audio source")
        selector = capture_device_selector(list_input_device_details())
        previous_source = self._bluetooth.default_source()
        self._bluetooth.set_default_source(source)
        profile = self._audio_profiles.get(BLUETOOTH_PROFILE_KEY, AudioTuningProfile())
        try:
            audio_input = self._audio_factory(selector, profile.audio_config())
        except Exception:
            self._bluetooth.restore_default_source(previous_source)
            raise
        self._audio_input = audio_input
        self._audio_profile = profile
        self._monitor_audio_source = AudioSource.BLUETOOTH
        self._active_audio_device_name = device.name
        self._active_bluetooth_address = device.address
        self._bluetooth_previous_default_source = previous_source
        self._hardware_gain = None
        self._apply_audio_profile(profile)
        self._audio_status = f"Bluetooth: {device.name}"
        self._audio_monitor_error = None

    def _apply_audio_profile(self, profile: AudioTuningProfile) -> None:
        self.playback.set_activity_config(profile.activity_config())
        self.playback.set_dynamic_response(profile.dynamic_response)
        for _, playback in self._playbacks.values():
            playback.set_activity_config(profile.activity_config())
            playback.set_dynamic_response(profile.dynamic_response)
        self._monitor_detector.config = profile.activity_config()

    def _apply_audio_settings(self, device: str, profile: AudioTuningProfile) -> None:
        device_name = self._device_name_for_selector(device)
        candidate: AudioInput | None = None
        current = self._audio_input
        target_is_bluetooth = device_name == BLUETOOTH_PROFILE_KEY
        current_name = current.device_name() if current is not None else None
        target_is_active_mic = (
            not target_is_bluetooth
            and self._monitor_audio_source is AudioSource.MIC
            and current_name == device_name
        )
        active_bluetooth = (
            target_is_bluetooth
            and self._monitor_audio_source is AudioSource.BLUETOOTH
            and current is not None
        )
        should_open_mic = (
            not target_is_bluetooth
            and not target_is_active_mic
            and (
                self._configured_audio_source is AudioSource.MIC
                or (
                    self._audio_source_setting == "auto"
                    and self._monitor_audio_source is AudioSource.MIC
                )
            )
        )
        try:
            if should_open_mic:
                candidate = self._audio_factory(device, profile.audio_config())
                device_name = candidate.device_name()
                target_is_active_mic = True

            next_profiles = dict(self._audio_profiles)
            next_profiles[device_name] = profile
            selected_device = (
                device_name
                if not target_is_bluetooth
                else self._selected_audio_device or ""
            )
            with self._settings_io_lock:
                self._settings_store.save_audio(selected_device, next_profiles)

            if candidate is not None:
                self._audio_input = candidate
            elif active_bluetooth or target_is_active_mic:
                assert current is not None
                current.reconfigure(profile.audio_config())

            self._audio_profiles = next_profiles
            if not target_is_bluetooth:
                self._selected_audio_device = device_name
            applies_to_active_input = (
                candidate is not None or active_bluetooth or target_is_active_mic
            )
            if applies_to_active_input or (target_is_bluetooth and current is None):
                self._audio_profile = profile
                self._hardware_gain = (
                    None
                    if target_is_bluetooth
                    else HardwareGainController(device_name)
                )
                if (
                    profile.hardware_gain_target is not None
                    and self._hardware_gain is not None
                ):
                    self._hardware_gain.set_normalized(profile.hardware_gain_target)
                self._apply_audio_profile(profile)
                self._monitor_detector.reset()
                self._noise_samples.clear()
                self._audio_monitor_error = None
                self._audio_status = (
                    (
                        f"Bluetooth: {self._active_audio_device_name}"
                        if self._monitor_audio_source is AudioSource.BLUETOOTH
                        else f"Input: {device_name}"
                    )
                    if self._audio_input is not None
                    else (
                        self._audio_status
                        if target_is_bluetooth
                        else "Microphone monitoring is disabled by the audio source."
                    )
                )
            if candidate is not None and current is not None:
                try:
                    current.close()
                except Exception as exc:  # noqa: BLE001 - new monitor remains usable
                    self._audio_monitor_error = (
                        f"Previous input did not close cleanly: {exc}"
                    )
        except Exception as exc:
            if candidate is not None:
                candidate.close()
                if self._audio_input is candidate:
                    self._audio_input = current
            if isinstance(exc, RuntimeCommandError):
                raise
            raise RuntimeCommandError(str(exc)) from exc

    def _start_audio_calibration(self, device: str, duration_seconds: float) -> AudioCalibrationSessionResponse:
        if self._audio_calibration is not None:
            raise RuntimeCommandError("an audio calibration session is already active")
        if self._audio_input is None:
            raise RuntimeCommandError(self._audio_monitor_error or "microphone input is unavailable")
        device_name = self._device_name_for_selector(device)
        if device_name == BLUETOOTH_PROFILE_KEY:
            if self._monitor_audio_source is not AudioSource.BLUETOOTH:
                raise RuntimeCommandError(
                    "connect and select the active Bluetooth stream before calibrating"
                )
        elif self._audio_input.device_name() != device_name:
            raise RuntimeCommandError("select the active microphone before calibrating")
        session = _AudioCalibrationSession(uuid4().hex, device_name, time.monotonic(), duration_seconds)
        self._audio_calibration = session
        return self.audio_calibration_status(session.session_id)

    def _finish_audio_calibration(self, command: _AudioCalibrationFinishCommand) -> AudioSettingsResponse | AudioCalibrationSessionResponse:
        session = self._audio_calibration
        if session is None or session.session_id != command.session_id:
            raise RuntimeCommandError("audio calibration session not found")
        if session.result is None:
            raise RuntimeCommandError("audio calibration is still capturing")
        if not command.apply:
            self._audio_calibration = None
            return self.audio_settings()
        result = session.result
        profile = self._audio_profiles.get(session.device, AudioTuningProfile())
        hardware_target = profile.hardware_gain_target
        if self._hardware_gain is not None and self._hardware_gain.status.writable:
            raw_level = max(self._audio_health.processor.input_rms, 1e-4)
            desired_raw = min(0.3, max(0.05, (command.target_level if command.target_level is not None else float(result["recommended_target_level"])) * 0.5))
            current_hardware = self._hardware_gain.status.value if self._hardware_gain.status.value is not None else 0.5
            hardware_target = min(1.0, max(0.0, current_hardware * desired_raw / raw_level))
        result["recommended_hardware_gain"] = hardware_target
        next_profile = replace(
            profile,
            target_level=command.target_level if command.target_level is not None else float(result["recommended_target_level"]),
            noise_floor=command.noise_floor if command.noise_floor is not None else float(result["recommended_noise_floor"]),
            hardware_gain_target=hardware_target,
        )
        self._apply_audio_settings(session.device, next_profile)
        self._audio_calibration = None
        return self.audio_settings()

    def _select_audio_device(self, device: str) -> None:
        if device == "bluetooth":
            self._set_audio_source("bluetooth")
            return
        device_name = self._device_name_for_selector(device)
        profile = self._audio_profiles.get(device_name, AudioTuningProfile())
        self._apply_audio_settings(device, profile)

    def _set_audio_source(self, source: str) -> None:
        if source not in {"auto", "off", "demo", "mic", "bluetooth"}:
            raise RuntimeCommandError(f"invalid audio source: {source}")
        if self.playback.mode is PlaybackMode.DYNAMIC and source == "off":
            raise RuntimeCommandError("dynamic mode requires demo or microphone audio")
        self._audio_source_setting = source
        if source == "auto":
            self._configured_audio_source = (
                AudioSource.BLUETOOTH if self.settings.hardware else AudioSource.DEMO
            )
        else:
            self._configured_audio_source = AudioSource(source)
        self._close_audio_input()
        self._active_audio_source = AudioSource.OFF
        self._monitor_audio_source = AudioSource.OFF
        self._audio_monitor_error = None
        self._next_audio_route_check_at = 0.0
        self._maintain_audio_monitor(force=True)
        self._active_audio_source = (
            AudioSource.DEMO
            if (
                self.playback.mode is PlaybackMode.DYNAMIC
                and self._monitor_audio_source is AudioSource.DEMO
            )
            else AudioSource.OFF
        )

    def _set_startup_restore(self, enabled: bool) -> None:
        if enabled:
            self._remembered_playback = self._capture_shared_playback()
        self._restore_last_state = enabled
        self._cancel_startup_persist_timer()
        try:
            with self._settings_io_lock:
                self._settings_store.save_startup(
                    self._restore_last_state, self._remembered_playback
                )
            self._settings_warning = None
        except OSError as exc:
            raise RuntimeCommandError(f"could not save startup behavior: {exc}") from exc

    def _capture_shared_playback(self) -> StartupPlaybackSettings:
        if self._topology.layout == "independent" and self._topology.outputs:
            player, playback = self._playbacks[self._topology.outputs[0].id]
            blackout = bool(self._output_gates and self._output_gates[0].blackout)
        else:
            player, playback = self.player, self.playback
            blackout = self._controller.blackout if self._controller else False
        return StartupPlaybackSettings(
            mode=playback.mode,
            solid_color=_color_to_hex(playback.solid_color),
            animation=player.name_at(player.current_index()) or "",
            brightness=player.brightness,
            blackout=blackout,
        )

    def _remember_shared_playback(
        self,
        *,
        mode: PlaybackMode | None = None,
        solid_color: str | None = None,
        animation: str | None = None,
        brightness: float | None = None,
        blackout: bool | None = None,
    ) -> None:
        if not self._restore_last_state:
            return
        current = self._remembered_playback
        self._remembered_playback = StartupPlaybackSettings(
            mode=current.mode if mode is None else mode,
            solid_color=current.solid_color if solid_color is None else solid_color,
            animation=current.animation if animation is None else animation,
            brightness=current.brightness if brightness is None else brightness,
            blackout=current.blackout if blackout is None else blackout,
        )
        self._schedule_startup_persist()

    def _schedule_startup_persist(self) -> None:
        with self._startup_persist_lock:
            if self._startup_persist_timer is not None:
                self._startup_persist_timer.cancel()
            timer = threading.Timer(0.5, self._flush_startup_settings)
            timer.daemon = True
            self._startup_persist_timer = timer
            timer.start()

    def _cancel_startup_persist_timer(self) -> None:
        with self._startup_persist_lock:
            timer = self._startup_persist_timer
            self._startup_persist_timer = None
            if timer is not None:
                timer.cancel()

    def _flush_startup_settings(self) -> None:
        with self._startup_persist_lock:
            self._startup_persist_timer = None
        try:
            with self._settings_io_lock:
                self._settings_store.save_startup(
                    self._restore_last_state, self._remembered_playback
                )
            self._settings_warning = None
        except OSError as exc:
            self._settings_warning = f"Could not save startup behavior: {exc}"

    def _apply_remembered_startup(self) -> None:
        if not self._restore_last_state:
            return
        remembered = self._remembered_playback
        for player, playback in self._target_playbacks(None):
            player.set_brightness(remembered.brightness)
            if remembered.animation:
                if player.index_of(remembered.animation) is None:
                    self._settings_warning = (
                        f'Saved startup animation "{remembered.animation}" is unavailable; '
                        "using the default animation."
                    )
                else:
                    playback.select_animation(remembered.animation)
            playback.set_solid_color(_rgb_from_hex(remembered.solid_color))
        try:
            self._apply_mode(remembered.mode, None, None)
        except RuntimeCommandError as exc:
            self._apply_mode(PlaybackMode.STATIC, None, None)
            self._settings_warning = f"Could not restore startup mode: {exc}"
        for gate in self._target_gates(None):
            gate.set_blackout(remembered.blackout)

    def _device_name_for_selector(self, selector: str) -> str:
        if selector == "bluetooth":
            return BLUETOOTH_PROFILE_KEY
        try:
            devices = list_input_device_details()
        except RuntimeError as exc:
            raise RuntimeCommandError(str(exc)) from exc
        lowered = selector.lower()
        for device in devices:
            if str(device.index) == selector or device.name == selector:
                return device.name
        for device in devices:
            if lowered in device.name.lower():
                return device.name
        raise RuntimeCommandError(f'no input device matching "{selector}"')

    def _set_mode(self, mode: PlaybackMode) -> None:
        if mode is self.playback.mode:
            return
        next_source = AudioSource.OFF
        if mode is PlaybackMode.DYNAMIC:
            next_source = self._configured_audio_source
            if next_source is AudioSource.OFF:
                raise RuntimeCommandError(
                    "dynamic mode requires demo or microphone audio"
                )
            if next_source in {AudioSource.MIC, AudioSource.BLUETOOTH} and self._audio_input is None:
                raise RuntimeCommandError(
                    self._audio_monitor_error
                    or "the selected audio input is unavailable"
                )

        self._active_audio_source = next_source
        self._demo_tick = 0
        self._audio_frame = AudioFrame()
        self._music_features = MusicFeatures()
        if self._audio_input is not None:
            self._audio_status = (
                f"Bluetooth: {self._active_audio_device_name}"
                if self._monitor_audio_source is AudioSource.BLUETOOTH
                else f"Input: {self._audio_input.device_name()}"
            )
        elif next_source is AudioSource.DEMO:
            self._audio_status = "Using internal demo beat."
        else:
            self._audio_status = "No audio source active."
        self.playback.set_mode(mode)

    def _with_color_correction(self, physical: Controller) -> Controller:
        children = (
            physical.controllers
            if isinstance(physical, MultiController)
            else (physical,)
        )
        corrected = tuple(
            ColorCorrectionController(
                child,
                self._saved_corrections[_profile_name(index)],
            )
            for index, child in enumerate(children)
        )
        self._correction_controllers = corrected
        return MultiController(corrected) if len(corrected) > 1 else corrected[0]

    def _start_calibration(self, output_index: int) -> CalibrationSessionResponse:
        if self._calibration is not None:
            raise RuntimeCommandError("a color calibration session is already active")
        if not 0 <= output_index < len(self._correction_controllers):
            raise RuntimeCommandError(f"unknown output index: {output_index}")
        assert self._controller is not None
        session = _CalibrationSession(
            session_id=uuid4().hex,
            output_index=output_index,
            pattern="white",
            original_corrections=tuple(
                controller.correction for controller in self._correction_controllers
            ),
            original_frames=tuple(
                controller.pixels().copy()
                for controller in self._correction_controllers
            ),
            original_blackout=self._controller.blackout,
            last_activity_at=time.monotonic(),
        )
        if session.original_blackout:
            self._controller.set_blackout(False)
        self._calibration = session
        self._render_calibration_pattern()
        return CalibrationSessionResponse(
            session_id=session.session_id,
            state=self._publish(running=True),
        )

    def _update_calibration(self, command: _CalibrationUpdateCommand) -> None:
        session = self._require_calibration(command.session_id)
        controller = self._correction_controllers[session.output_index]
        controller.set_correction(command.correction)
        session.pattern = command.pattern
        session.last_activity_at = time.monotonic()
        self._render_calibration_pattern()

    def _finish_calibration(self, session_id: str, *, save: bool) -> None:
        session = self._require_calibration(session_id)
        if save:
            profiles = dict(self._saved_corrections)
            for index, controller in enumerate(self._correction_controllers):
                profile_id = (
                    self._topology.outputs[index].id
                    if index < len(self._topology.outputs)
                    else _profile_name(index)
                )
                profiles[profile_id] = controller.correction
            try:
                with self._settings_io_lock:
                    self._settings_store.save(profiles)
            except OSError as exc:
                raise RuntimeCommandError(
                    f"could not save color calibration: {exc}"
                ) from exc
            self._saved_corrections = profiles
            self._settings_warning = None
        else:
            for controller, correction in zip(
                self._correction_controllers,
                session.original_corrections,
                strict=True,
            ):
                controller.set_correction(correction)

        self._calibration = None
        assert self._controller is not None
        if session.original_blackout:
            self._controller.set_blackout(True)
        for controller, frame in zip(
            self._correction_controllers,
            session.original_frames,
            strict=True,
        ):
            controller.set_pixels(frame)
        if not session.original_blackout:
            for controller in self._correction_controllers:
                controller.force_flush()

    def _require_calibration(self, session_id: str) -> _CalibrationSession:
        session = self._calibration
        if session is None:
            raise RuntimeCommandError("no color calibration session is active")
        if session.session_id != session_id:
            raise RuntimeCommandError("color calibration session is stale")
        return session

    def _render_calibration_pattern(self) -> None:
        session = self._calibration
        if session is None:
            return
        pattern = {
            "white": Rgb(255, 255, 255),
            "red": Rgb(255, 0, 0),
            "green": Rgb(0, 255, 0),
            "blue": Rgb(0, 0, 255),
        }[session.pattern]
        for index, controller in enumerate(self._correction_controllers):
            if index == session.output_index:
                controller.fill(pattern)
            else:
                controller.clear()
            controller.force_flush()

    def _step(self) -> float:
        assert self._controller is not None
        self._maintain_audio_monitor()
        session = self._calibration
        if session is not None:
            if (
                time.monotonic() - session.last_activity_at
                >= CALIBRATION_TIMEOUT_SECONDS
            ):
                self._finish_calibration(session.session_id, save=False)
            return CALIBRATION_FRAME_SECONDS
        if self._stripe_test is not None:
            if time.monotonic() < self._stripe_test.expires_at:
                self._render_stripe_test()
                return CALIBRATION_FRAME_SECONDS
            self._finish_stripe_test()
        snapshot: AudioSnapshot | None = None
        if self._audio_input is not None:
            self._audio_frame = self._audio_input.read()
            self._audio_health = self._audio_input.health()
            self._music_features = (
                self._audio_input.read_features()
                if self._audio_frame.fresh
                else MusicFeatures()
            )
            snapshot = (
                AudioSnapshot.from_parts(
                    self._audio_frame,
                    self._music_features,
                    self._audio_health,
                )
                if self._audio_frame.fresh
                else AudioSnapshot.silence(
                    frame=self._audio_frame,
                    health=self._audio_health,
                )
            )
            if self._audio_frame.fresh:
                self._monitor_detector.update(self._music_features)
                self._noise_samples.append(
                    min(1.0, max(0.0, self._audio_health.processor.input_rms))
                )
                audio_session = self._audio_calibration
                if audio_session is not None and audio_session.result is None:
                    audio_session.frames.append(self._audio_frame)
                    audio_session.features.append(self._music_features)
                    if time.monotonic() - audio_session.started_at >= audio_session.duration_seconds:
                        recommendation = recommend_audio_calibration(
                            audio_session.frames,
                            audio_session.features,
                            duration=audio_session.duration_seconds,
                        )
                        audio_session.result = {
                            "duration_seconds": recommendation.duration,
                            "samples": recommendation.samples,
                            "measured_floor": recommendation.measured_floor,
                            "measured_peak": recommendation.measured_peak,
                            "recommended_noise_floor": recommendation.recommended_noise_floor,
                            "recommended_target_level": recommendation.recommended_target_level,
                            "recommended_hardware_gain": None,
                            "recommended_idle_threshold_scale": recommendation.recommended_idle_threshold_scale,
                        }
        elif self._active_audio_source is AudioSource.DEMO:
            snapshot = demo_snapshot(self._demo_tick)
            self._demo_tick += 1
            self._audio_frame = snapshot.frame
            self._music_features = snapshot.features
        else:
            self._audio_frame = AudioFrame()
            self._music_features = MusicFeatures()
            self._audio_health = AudioInputHealth()

        if self._topology.layout == "independent" and self._output_gates:
            delays = [
                self._playbacks[output.id][1].step(gate, snapshot=snapshot)
                for output, gate in zip(
                    self._topology.outputs, self._output_gates, strict=True
                )
            ]
            delay = min(delays)
        else:
            delay = self.playback.step(self._controller, snapshot=snapshot)
        self._update_audio_telemetry()
        self._record_frame()
        self._publish(running=True)
        return max(delay, MIN_FRAME_SECONDS)

    def _update_audio_telemetry(self) -> None:
        preview = self.playback.mode is not PlaybackMode.DYNAMIC
        detector = (
            self._monitor_detector if preview else self.playback.activity_detector
        )
        cfg = detector.config
        threshold_scale = 1.0 if detector.active else cfg.activation_hysteresis_ratio
        spectral_balance = min(self._music_features.bass_energy, self._music_features.treble_energy) / max(self._music_features.mid_energy, 1e-6)
        checks = (
            {"id": "energy", "label": "Energy", "value": detector.energy, "threshold": cfg.energy_threshold * threshold_scale, "passed": detector.energy >= cfg.energy_threshold * threshold_scale},
            {"id": "onset", "label": "Onset", "value": detector.onset, "threshold": cfg.onset_threshold * threshold_scale, "passed": detector.onset >= cfg.onset_threshold * threshold_scale},
            {"id": "beat_density", "label": "Beat density", "value": detector.beat_density, "threshold": cfg.beat_density_threshold * threshold_scale, "passed": detector.beat_density >= cfg.beat_density_threshold * threshold_scale},
            {"id": "brightness", "label": "Brightness", "value": detector.brightness, "threshold": cfg.brightness_threshold * threshold_scale, "passed": detector.brightness >= cfg.brightness_threshold * threshold_scale},
            {"id": "spectral_balance", "label": "Spectral balance", "value": min(1.0, spectral_balance), "threshold": cfg.spectral_balance_ratio, "passed": spectral_balance >= cfg.spectral_balance_ratio},
        )
        failed_checks = [str(check["label"]) for check in checks if not check["passed"]]
        gate_reason = "All activation checks passed." if not failed_checks else "Blocked by " + ", ".join(failed_checks) + "."
        ordered_noise = sorted(self._noise_samples)
        noise_floor = 0.0
        if ordered_noise:
            noise_floor = ordered_noise[int((len(ordered_noise) - 1) * 0.2)]
        processor = self._audio_health.processor
        telemetry = AudioTelemetry(
            sequence=self._audio_frame.sequence,
            fresh=self._audio_frame.fresh,
            input_level=min(1.0, max(0.0, processor.input_rms)),
            processed_level=min(1.0, max(0.0, self._audio_frame.rms)),
            bands=cast(
                BandTuple,
                tuple(min(1.0, max(0.0, value)) for value in self._audio_frame.bands),
            ),
            beat=self._audio_frame.beat or self._music_features.beat,
            beat_strength=min(
                1.0,
                max(
                    0.0,
                    self._audio_frame.beat_strength,
                    self._music_features.beat_strength,
                ),
            ),
            bpm=max(0.0, self._music_features.bpm),
            estimated_noise_floor=noise_floor,
            configured_noise_floor=self._audio_profile.audio_config().smoothing.noise_floor,
            normalization_gain=max(0.0, processor.normalization_gain),
            hardware_gain_value=(self._hardware_gain.status.value if self._hardware_gain else None),
            program_loudness=min(
                1.0,
                max(
                    0.0,
                    processor.program_loudness,
                    self._music_features.program_loudness,
                ),
            ),
            musical_impact=min(
                1.0,
                max(0.0, processor.musical_impact, self._music_features.musical_impact),
            ),
            gate=detector.state.value,
            gate_preview=preview,
            gate_energy=min(1.0, max(0.0, detector.energy)),
            gate_onset=min(1.0, max(0.0, detector.onset)),
            gate_beat_density=min(1.0, max(0.0, detector.beat_density)),
            gate_brightness=min(1.0, max(0.0, detector.brightness)),
            gate_spectral_balance=min(1.0, max(0.0, spectral_balance)),
            gate_reason=gate_reason,
            gate_checks=checks,
            health=self._audio_health_status(self._uptime_seconds()),
        )
        with self._snapshot_lock:
            self._audio_telemetry = telemetry

    def _publish(
        self,
        *,
        running: bool,
        error: str | None = None,
    ) -> DashboardState:
        self._capture_preview_frame()
        self._revision += 1
        controller = self._controller
        uptime_seconds = self._uptime_seconds()
        state = DashboardState(
            revision=self._revision,
            runtime=self.settings.kind,
            output_backend=self._output_backend_label(),
            output_devices=self._output_device_paths(),
            spi_speed_hz=self._common_spi_speed(),
            running=running,
            mode=self.playback.mode,
            solid_color=_color_to_hex(self.playback.solid_color),
            animation=self._current_animation(),
            brightness=self.player.brightness,
            blackout=controller.blackout if controller is not None else False,
            music_active=self.playback.music_active,
            music_recognition_enabled=self.playback.music_recognition_enabled,
            music_gate=self.playback.music_gate_state.value,
            bpm=self._music_features.bpm,
            audio_status=self._audio_status,
            active_effects=self.playback.active_effect_names,
            uptime_seconds=uptime_seconds,
            frame_rate=self._frame_rate,
            audio_health=self._audio_health_status(uptime_seconds),
            audio_callback_age_seconds=self._audio_health.last_callback_age,
            audio_frame_age_seconds=self._audio_health.last_frame_age,
            last_output_at=(self._last_output_at()),
            last_output_age_seconds=(self._last_output_age()),
            application_version=APPLICATION_VERSION,
            color_corrections=self._color_correction_profiles(),
            calibration=self._calibration_status(),
            stripe_topology=self._stripe_topology_state(),
            stripe_playback=self._stripe_playback_state(),
            diagnostic_issues=self._diagnostic_issues(
                running=running,
                uptime_seconds=uptime_seconds,
            ),
            error=error,
        )
        with self._snapshot_lock:
            self._snapshot = state
        return state

    def _capture_preview_frame(self) -> None:
        """Copy output pixels before publishing a frame to WebSocket clients.

        The animation thread owns the live NumPy buffers. Keeping immutable byte
        copies here lets the async API serve a frame without racing a render or
        holding the runtime thread while a network client is slow.
        """
        outputs: list[bytes] = []
        gates = self._output_gates
        if gates:
            for index, gate in enumerate(gates):
                if gate.blackout:
                    outputs.append(bytes(gate.length * 4))
                    continue
                pixels = gate.pixels().copy()
                if index < len(self._correction_controllers):
                    correction = self._correction_controllers[index].correction
                    channels = np.array(
                        (correction.red, correction.green, correction.blue),
                        dtype=np.uint16,
                    )
                    scaled = pixels[:, :3].astype(np.uint16) * channels
                    pixels[:, :3] = (scaled // 255).astype(np.uint8)
                if index < len(self._topology.outputs) and self._topology.outputs[index].reversed:
                    pixels = pixels[::-1]
                outputs.append(pixels.tobytes())
        elif self._controller is not None:
            pixels = self._controller.pixels().copy()
            if self._controller.blackout:
                pixels[:] = 0
            outputs.append(pixels.tobytes())

        with self._preview_lock:
            self._preview_sequence += 1
            self._preview_outputs = tuple(outputs)

    def _output_backend_label(self) -> str:
        if not self.settings.hardware:
            return "simulation"
        backends = {output.backend for output in self._topology.outputs}
        if not backends:
            return "none"
        return next(iter(backends)) if len(backends) == 1 else "mixed"

    def _output_device_paths(self) -> tuple[str, ...]:
        if not self.settings.hardware:
            return ()
        return tuple(
            output.spi_device
            if output.backend == "spi"
            else f"{output.chip}:{output.data_pin}/{output.clock_pin}"
            for output in self._topology.outputs
        )

    def _common_spi_speed(self) -> int | None:
        if not self.settings.hardware or not self._topology.outputs:
            return None
        speeds = {
            output.spi_speed_hz
            for output in self._topology.outputs
            if output.backend == "spi"
        }
        if len(speeds) == 1 and all(
            output.backend == "spi" for output in self._topology.outputs
        ):
            return next(iter(speeds))
        return None

    def _color_correction_profiles(self) -> tuple[ColorCorrectionProfile, ...]:
        devices = self._output_device_paths()
        profiles: list[ColorCorrectionProfile] = []
        for index, controller in enumerate(self._correction_controllers):
            correction = controller.correction
            configured = (
                self._topology.outputs[index]
                if index < len(self._topology.outputs)
                else None
            )
            device = (
                devices[index]
                if index < len(devices)
                else (
                    "Simulation"
                    if not self.settings.hardware
                    else f"Output {index + 1}"
                )
            )
            profiles.append(
                ColorCorrectionProfile(
                    output_index=index,
                    name=(
                        configured.name
                        if configured
                        else ("Primary" if index == 0 else "Secondary")
                    ),
                    device=device,
                    red=correction.red,
                    green=correction.green,
                    blue=correction.blue,
                )
            )
        return tuple(profiles)

    def _stripe_topology_state(self) -> StripeTopology:
        return StripeTopology(
            layout=self._topology.layout,
            outputs=tuple(
                StripeOutputConfig(
                    id=output.id,
                    name=output.name,
                    pixels=output.pixels,
                    backend=output.backend,
                    reversed=output.reversed,
                    spi_device=output.spi_device,
                    spi_speed_hz=output.spi_speed_hz,
                    chip=output.chip,
                    data_pin=output.data_pin,
                    clock_pin=output.clock_pin,
                    last_output_at=(
                        self._output_gates[index].last_successful_update_at
                        if index < len(self._output_gates)
                        else None
                    ),
                )
                for index, output in enumerate(self._topology.outputs)
            ),
        )

    def _stripe_playback_state(self) -> tuple[StripePlaybackState, ...]:
        states: list[StripePlaybackState] = []
        for index, output in enumerate(self._topology.outputs):
            player, playback = (
                self._playbacks[output.id]
                if self._topology.layout == "independent"
                else (self.player, self.playback)
            )
            gate = (
                self._output_gates[index] if index < len(self._output_gates) else None
            )
            states.append(
                StripePlaybackState(
                    stripe_id=output.id,
                    mode=playback.mode,
                    solid_color=_color_to_hex(playback.solid_color),
                    animation=player.name_at(player.current_index()) or "",
                    brightness=player.brightness,
                    blackout=gate.blackout if gate is not None else False,
                    music_active=playback.music_active,
                    music_recognition_enabled=playback.music_recognition_enabled,
                )
            )
        return tuple(states)

    def _last_output_at(self) -> datetime | None:
        values = [
            gate.last_successful_update_at
            for gate in self._output_gates
            if gate.last_successful_update_at is not None
        ]
        if values:
            return max(values)
        return self._controller.last_successful_update_at if self._controller else None

    def _last_output_age(self) -> float | None:
        values = [
            gate.last_successful_update_age_seconds
            for gate in self._output_gates
            if gate.last_successful_update_age_seconds is not None
        ]
        if values:
            return max(values)
        return (
            self._controller.last_successful_update_age_seconds
            if self._controller
            else None
        )

    def _calibration_status(self) -> CalibrationStatus:
        session = self._calibration
        if session is None:
            return CalibrationStatus()
        remaining = max(
            0.0,
            CALIBRATION_TIMEOUT_SECONDS - (time.monotonic() - session.last_activity_at),
        )
        return CalibrationStatus(
            active=True,
            output_index=session.output_index,
            pattern=session.pattern,
            expires_in_seconds=remaining,
        )

    def _current_animation(self) -> str:
        return self.player.name_at(self.player.current_index()) or ""

    def _uptime_seconds(self) -> float:
        if self._started_at_s is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at_s)

    def _record_frame(self) -> None:
        now = time.monotonic()
        if self._fps_window_started_s is None:
            self._fps_window_started_s = now
        self._fps_window_frames += 1
        elapsed = now - self._fps_window_started_s
        if elapsed < FPS_SAMPLE_SECONDS:
            return
        self._frame_rate = self._fps_window_frames / elapsed
        self._fps_window_frames = 0
        self._fps_window_started_s = now

    def _audio_health_status(self, uptime_seconds: float) -> str:
        if self._audio_source_setting == "off":
            return "inactive"
        if self._monitor_audio_source is AudioSource.DEMO:
            if self.playback.mode is not PlaybackMode.DYNAMIC and self._active_audio_source is not AudioSource.DEMO:
                return "inactive"
            return "demo"
        if self._audio_input is None:
            return "unavailable"
        if self._audio_health.status_count > 0:
            return "degraded"
        callback_age = self._audio_health.last_callback_age
        if callback_age is None:
            return "starting" if uptime_seconds < 3.0 else "unavailable"
        if callback_age > 0.5:
            return "unavailable"
        frame_age = self._audio_health.last_frame_age
        if frame_age is not None and frame_age > 0.5:
            return "degraded"
        return "healthy"

    def _diagnostic_issues(
        self, *, running: bool, uptime_seconds: float
    ) -> tuple[DiagnosticIssue, ...]:
        issues: list[DiagnosticIssue] = []
        if self._fatal_error:
            issues.append(
                DiagnosticIssue(
                    severity="critical",
                    title="Controller unavailable",
                    message=self._fatal_error,
                    action="Check the service logs, GPIO permissions, wiring, and configured pins.",
                )
            )
        elif not running:
            issues.append(
                DiagnosticIssue(
                    severity="critical",
                    title="Runtime stopped",
                    message="The lighting runtime is not processing frames.",
                    action="Restart the LumiStripe service and inspect its logs.",
                )
            )

        if self._last_command_error:
            issues.append(
                DiagnosticIssue(
                    severity="warning",
                    title="Last command failed",
                    message=self._last_command_error,
                    action="Correct the selected setting and try the command again.",
                )
            )

        if self._settings_warning:
            issues.append(
                DiagnosticIssue(
                    severity="warning",
                    title="Dashboard settings were not fully loaded",
                    message=self._settings_warning,
                    action=(
                        "Check the dashboard settings file, then save the affected profile again."
                    ),
                )
            )

        audio_health = self._audio_health_status(uptime_seconds)
        if audio_health in {"degraded", "unavailable"}:
            detail = (
                self._audio_monitor_error
                or self._audio_health.last_status
                or "Audio frames are not arriving reliably."
            )
            issues.append(
                DiagnosticIssue(
                    severity="warning",
                    title="Audio input needs attention",
                    message=detail,
                    action="Reconnect the microphone or select a working input on the Audio page.",
                )
            )

        controller = self._controller
        output_age = self._last_output_age()
        outputs_blacked_out = (
            all(gate.blackout for gate in self._output_gates)
            if self._topology.layout == "independent" and self._output_gates
            else controller is not None and controller.blackout
        )
        if (
            self.settings.hardware
            and running
            and self._calibration is None
            and not outputs_blacked_out
            and uptime_seconds > 3.0
            and (controller is None or output_age is None or output_age > 2.0)
        ):
            issues.append(
                DiagnosticIssue(
                    severity="warning",
                    title="No recent hardware update",
                    message="The GPIO controller has not completed an output update recently.",
                    action="Check strip power, GPIO permissions, and the data/clock pin configuration.",
                )
            )

        if running and uptime_seconds > 3.0 and self._frame_rate < 8.0:
            issues.append(
                DiagnosticIssue(
                    severity="warning",
                    title="Low frame rate",
                    message=f"The renderer is currently running at {self._frame_rate:.1f} FPS.",
                    action="Check CPU load and audio stability, or choose a less demanding animation.",
                )
            )
        return tuple(issues)

    def _cleanup(self) -> None:
        self._cancel_startup_persist_timer()
        if self._restore_last_state:
            self._flush_startup_settings()
        try:
            self._close_audio_input()
        except Exception as exc:  # noqa: BLE001 - cleanup continues after individual failures
            self._fatal_error = self._fatal_error or str(exc)
        self._bluetooth.stop()
        controller = self._raw_controller
        if controller is None:
            return
        try:
            controller.clear()
            controller.force_flush()
        except Exception as exc:  # noqa: BLE001 - cleanup continues after individual failures
            self._fatal_error = self._fatal_error or str(exc)
        finally:
            close = getattr(controller, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - cleanup records close failures
                    self._fatal_error = self._fatal_error or str(exc)

    def _close_audio_input(self) -> None:
        audio_input = self._audio_input
        previous_source = self._bluetooth_previous_default_source
        self._audio_input = None
        self._active_audio_source = AudioSource.OFF
        self._monitor_audio_source = AudioSource.OFF
        self._active_audio_device_name = None
        self._active_bluetooth_address = None
        self._bluetooth_previous_default_source = None
        try:
            if audio_input is not None:
                audio_input.close()
        finally:
            if previous_source is not None:
                self._bluetooth.restore_default_source(previous_source)

    def _reject_pending(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if command is not None and not command.future.done():
                command.future.set_exception(
                    RuntimeUnavailableError(self._fatal_error or "runtime stopped")
                )


def _expect[T](value: object, expected: type[T]) -> T:
    if not isinstance(value, expected):
        raise TypeError(f"expected {expected.__name__}, got {type(value).__name__}")
    return value


def _rgb_from_hex(value: str) -> Rgb:
    normalized = value.removeprefix("#")
    if len(normalized) != 6:
        raise RuntimeCommandError("solid color must use #RRGGBB format")
    try:
        encoded = int(normalized, 16)
    except ValueError as exc:
        raise RuntimeCommandError("solid color must use #RRGGBB format") from exc
    return Rgb((encoded >> 16) & 0xFF, (encoded >> 8) & 0xFF, encoded & 0xFF)


def _color_to_hex(color: Color) -> str:
    red, green, blue, _ = color.to_rgba()
    return f"#{red:02X}{green:02X}{blue:02X}"


def _profile_name(output_index: int) -> str:
    if output_index == 0:
        return "primary"
    if output_index == 1:
        return "secondary"
    raise ValueError(f"unsupported output index: {output_index}")


def _profile_values(profile: AudioTuningProfile) -> AudioTuningValues:
    return AudioTuningValues(
        **{
            name: getattr(profile, name)
            for name in AudioTuningProfile.__dataclass_fields__
        }
    )


class _HardwareGainValues(TypedDict):
    hardware_gain_supported: bool
    hardware_gain_writable: bool
    hardware_gain_backend: str | None
    hardware_gain_control: str | None
    hardware_gain_value: float | None
    hardware_gain_error: str | None


def _hardware_gain_values(
    controller: HardwareGainController | None,
) -> _HardwareGainValues:
    status = controller.status if controller is not None else None
    return {
        "hardware_gain_supported": bool(status and status.supported),
        "hardware_gain_writable": bool(status and status.writable),
        "hardware_gain_backend": status.backend if status else None,
        "hardware_gain_control": status.control if status else None,
        "hardware_gain_value": status.value if status else None,
        "hardware_gain_error": status.error if status else None,
    }
