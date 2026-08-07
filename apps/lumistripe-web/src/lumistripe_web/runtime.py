from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

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
    Config,
    Controller,
    GPIOStripe,
    MultiController,
    MusicActivityDetector,
    MusicFeatures,
    PixelBuffer,
    PlaybackConfig,
    PlaybackEngine,
    PlaybackMode,
    Rgb,
    SPIConfig,
    SPIStripe,
    Stripe,
    demo_snapshot,
    list_input_device_details,
)
from lumistripe.audio import BandTuple

from .models import (
    AnimationOption,
    AudioDeviceOption,
    AudioSettingsResponse,
    AudioTelemetry,
    AudioTuningValues,
    CalibrationSessionResponse,
    CalibrationStatus,
    ColorCorrectionProfile,
    DashboardState,
    DiagnosticIssue,
    RuntimeKind,
)
from .settings import (
    AudioTuningProfile,
    CalibrationSettingsStore,
    default_settings_path,
)

MIN_FRAME_SECONDS = 0.016
FPS_SAMPLE_SECONDS = 1.0
CALIBRATION_FRAME_SECONDS = 0.05
CALIBRATION_TIMEOUT_SECONDS = 300.0
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
        if self.audio_source not in {"auto", "off", "demo", "mic"}:
            raise ValueError(f"invalid audio source: {self.audio_source}")

    @property
    def kind(self) -> RuntimeKind:
        return RuntimeKind.HARDWARE if self.hardware else RuntimeKind.SIMULATION

    def dynamic_audio_source(self) -> AudioSource:
        if self.audio_source == "auto":
            return AudioSource.MIC if self.hardware else AudioSource.DEMO
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


@dataclass(slots=True)
class _Command:
    name: str
    value: object
    future: Future[object]


@dataclass(frozen=True, slots=True)
class _ModeCommand:
    mode: PlaybackMode
    color: str | None = None


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


@dataclass(slots=True)
class _CalibrationSession:
    session_id: str
    output_index: int
    pattern: CalibrationPattern
    original_corrections: tuple[ColorCorrection, ...]
    original_frames: tuple[PixelBuffer, ...]
    original_blackout: bool
    last_activity_at: float


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


class LumiStripeRuntime:
    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        *,
        controller_factory: _ControllerFactory = _default_controller_factory,
        audio_factory: _AudioFactory = _default_audio_factory,
    ) -> None:
        self.settings = settings or RuntimeSettings()
        self._controller_factory = controller_factory
        self._audio_factory = audio_factory
        self._settings_store = CalibrationSettingsStore(self.settings.settings_file)
        loaded_settings, self._settings_warning = self._settings_store.load_all()
        self._saved_corrections = loaded_settings.color_corrections
        self._audio_profiles = loaded_settings.audio_profiles
        self._selected_audio_device = (
            self.settings.audio_device or loaded_settings.selected_audio_device
        )
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
        self._revision = 0
        self._snapshot = DashboardState(
            runtime=self.settings.kind,
            mode=PlaybackMode.STATIC,
            animation=self._current_animation(),
        )

        self._raw_controller: Controller | None = None
        self._controller: OutputGateController | None = None
        self._correction_controllers: tuple[ColorCorrectionController, ...] = ()
        self._calibration: _CalibrationSession | None = None
        self._audio_input: AudioInput | None = None
        self._configured_audio_source = self.settings.dynamic_audio_source()
        self._active_audio_source = AudioSource.OFF
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
            options = tuple(
                AudioDeviceOption(
                    selector=str(device.index),
                    name=device.name,
                    settings=_profile_values(
                        self._audio_profiles.get(device.name, AudioTuningProfile())
                    ),
                )
                for device in devices
            )
            enumeration_error = None
        except RuntimeError as exc:
            options = ()
            enumeration_error = str(exc)
        active_name = self._audio_input.device_name() if self._audio_input else None
        selected_name = active_name or self._selected_audio_device
        active_selector = next(
            (option.selector for option in options if option.name == selected_name),
            selected_name,
        )
        return AudioSettingsResponse(
            source=self._configured_audio_source.value,
            monitoring=self._audio_input is not None,
            active_device=active_selector,
            active_device_name=active_name,
            devices=options,
            settings=_profile_values(self._audio_profile),
            configured_noise_floor=self._audio_profile.audio_config().smoothing.noise_floor,
            error=self._audio_monitor_error or enumeration_error,
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

    def set_mode(
        self, mode: PlaybackMode, *, solid_color: str | None = None
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit("mode", _ModeCommand(mode, solid_color)),
        )

    def set_brightness(self, brightness: float) -> Future[DashboardState]:
        return cast(Future[DashboardState], self._submit("brightness", float(brightness)))

    def select_animation(self, name: str) -> Future[DashboardState]:
        return cast(Future[DashboardState], self._submit("animation", name))

    def set_blackout(self, enabled: bool) -> Future[DashboardState]:
        return cast(Future[DashboardState], self._submit("blackout", bool(enabled)))

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
            physical_controller = self._controller_factory(self.settings)
            self._raw_controller = self._with_color_correction(physical_controller)
            self._controller = OutputGateController(self._raw_controller)
            self._started_at_s = time.monotonic()
            self._fps_window_started_s = self._started_at_s
            self._initialize_audio_monitor()
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
                if mode_command.color is not None:
                    if mode_command.mode is not PlaybackMode.SOLID:
                        raise RuntimeCommandError(
                            "color can only be set for solid mode"
                        )
                    self.playback.set_solid_color(_rgb_from_hex(mode_command.color))
                self._set_mode(mode_command.mode)
                result = self._publish(running=True)
            elif command.name == "brightness":
                self.player.set_brightness(_expect(command.value, float))
                result = self._publish(running=True)
            elif command.name == "animation":
                name = _expect(command.value, str)
                if self.player.index_of(name) is None:
                    raise UnknownAnimationError(f"unknown animation: {name}")
                self._set_mode(PlaybackMode.STATIC)
                self.playback.select_animation(name)
                result = self._publish(running=True)
            elif command.name == "blackout":
                assert self._controller is not None
                self._controller.set_blackout(_expect(command.value, bool))
                result = self._publish(running=True)
            elif command.name == "audio_settings":
                audio_update = _expect(command.value, _AudioSettingsCommand)
                self._apply_audio_settings(audio_update.device, audio_update.profile)
                self._publish(running=True)
                result = self.audio_settings()
            else:
                raise RuntimeCommandError(f"unknown runtime command: {command.name}")
            if not command.future.done():
                command.future.set_result(result)
        except Exception as exc:  # noqa: BLE001 - every command must resolve its future
            self._last_command_error = str(exc)
            self._publish(running=True)
            if not command.future.done():
                command.future.set_exception(exc)

    def _initialize_audio_monitor(self) -> None:
        if self._configured_audio_source is not AudioSource.MIC:
            return
        try:
            audio_input = self._audio_factory(
                self._selected_audio_device,
                self._audio_profile.audio_config(),
            )
            device_name = audio_input.device_name()
            saved_profile = self._audio_profiles.get(device_name)
            if saved_profile is not None and saved_profile != self._audio_profile:
                audio_input.reconfigure(saved_profile.audio_config())
                self._audio_profile = saved_profile
            self._selected_audio_device = device_name
            self._audio_input = audio_input
            self.playback.set_activity_config(self._audio_profile.activity_config())
            self.playback.set_dynamic_response(self._audio_profile.dynamic_response)
            self._monitor_detector.config = self._audio_profile.activity_config()
            self._audio_status = f"Input: {device_name}"
            self._audio_monitor_error = None
        except RuntimeError as exc:
            self._audio_input = None
            self._audio_monitor_error = str(exc)
            self._audio_status = f"Microphone unavailable: {exc}"

    def _apply_audio_settings(
        self, device: str, profile: AudioTuningProfile
    ) -> None:
        device_name = self._device_name_for_selector(device)
        candidate: AudioInput | None = None
        current = self._audio_input
        try:
            if (
                self._configured_audio_source is AudioSource.MIC
                and (current is None or current.device_name() != device_name)
            ):
                candidate = self._audio_factory(device, profile.audio_config())
                device_name = candidate.device_name()

            next_profiles = dict(self._audio_profiles)
            next_profiles[device_name] = profile
            self._settings_store.save_audio(device_name, next_profiles)

            if candidate is not None:
                self._audio_input = candidate
            elif current is not None:
                current.reconfigure(profile.audio_config())

            self._audio_profiles = next_profiles
            self._selected_audio_device = device_name
            self._audio_profile = profile
            activity = profile.activity_config()
            self.playback.set_activity_config(activity)
            self.playback.set_dynamic_response(profile.dynamic_response)
            self._monitor_detector.config = activity
            self._monitor_detector.reset()
            self._noise_samples.clear()
            self._audio_monitor_error = None
            self._audio_status = (
                f"Input: {device_name}"
                if self._audio_input is not None
                else "Microphone monitoring is disabled by the audio source."
            )
            if candidate is not None and current is not None:
                try:
                    current.close()
                except Exception as exc:  # noqa: BLE001 - new monitor remains usable
                    self._audio_monitor_error = f"Previous input did not close cleanly: {exc}"
        except Exception as exc:
            if candidate is not None:
                candidate.close()
                if self._audio_input is candidate:
                    self._audio_input = current
            if isinstance(exc, RuntimeCommandError):
                raise
            raise RuntimeCommandError(str(exc)) from exc

    def _device_name_for_selector(self, selector: str) -> str:
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
            if next_source is AudioSource.MIC and self._audio_input is None:
                raise RuntimeCommandError(
                    self._audio_monitor_error or "microphone input is unavailable"
                )

        self._active_audio_source = next_source
        self._demo_tick = 0
        self._audio_frame = AudioFrame()
        self._music_features = MusicFeatures()
        if self._audio_input is not None:
            self._audio_status = f"Input: {self._audio_input.device_name()}"
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
                profiles[_profile_name(index)] = controller.correction
            try:
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
        session = self._calibration
        if session is not None:
            if time.monotonic() - session.last_activity_at >= CALIBRATION_TIMEOUT_SECONDS:
                self._finish_calibration(session.session_id, save=False)
            return CALIBRATION_FRAME_SECONDS
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
        elif self._active_audio_source is AudioSource.DEMO:
            snapshot = demo_snapshot(self._demo_tick)
            self._demo_tick += 1
            self._audio_frame = snapshot.frame
            self._music_features = snapshot.features
        else:
            self._audio_frame = AudioFrame()
            self._music_features = MusicFeatures()
            self._audio_health = AudioInputHealth()

        delay = self.playback.step(self._controller, snapshot=snapshot)
        self._update_audio_telemetry()
        self._record_frame()
        self._publish(running=True)
        return max(delay, MIN_FRAME_SECONDS)

    def _update_audio_telemetry(self) -> None:
        preview = self.playback.mode is not PlaybackMode.DYNAMIC
        detector = self._monitor_detector if preview else self.playback.activity_detector
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
                tuple(
                    min(1.0, max(0.0, value))
                    for value in self._audio_frame.bands
                ),
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
            program_loudness=min(
                1.0,
                max(0.0, processor.program_loudness, self._music_features.program_loudness),
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
        self._revision += 1
        controller = self._controller
        uptime_seconds = self._uptime_seconds()
        state = DashboardState(
            revision=self._revision,
            runtime=self.settings.kind,
            output_backend=self._output_backend_label(),
            output_devices=self._output_device_paths(),
            spi_speed_hz=(
                self.settings.spi_speed_hz
                if self.settings.hardware and self.settings.output_backend == "spi"
                else None
            ),
            running=running,
            mode=self.playback.mode,
            solid_color=_color_to_hex(self.playback.solid_color),
            animation=self._current_animation(),
            brightness=self.player.brightness,
            blackout=controller.blackout if controller is not None else False,
            music_active=self.playback.music_active,
            music_gate=self.playback.music_gate_state.value,
            bpm=self._music_features.bpm,
            audio_status=self._audio_status,
            active_effects=self.playback.active_effect_names,
            uptime_seconds=uptime_seconds,
            frame_rate=self._frame_rate,
            audio_health=self._audio_health_status(uptime_seconds),
            audio_callback_age_seconds=self._audio_health.last_callback_age,
            audio_frame_age_seconds=self._audio_health.last_frame_age,
            last_output_at=(
                controller.last_successful_update_at if controller is not None else None
            ),
            last_output_age_seconds=(
                controller.last_successful_update_age_seconds
                if controller is not None
                else None
            ),
            application_version=APPLICATION_VERSION,
            color_corrections=self._color_correction_profiles(),
            calibration=self._calibration_status(),
            diagnostic_issues=self._diagnostic_issues(
                running=running,
                uptime_seconds=uptime_seconds,
            ),
            error=error,
        )
        with self._snapshot_lock:
            self._snapshot = state
        return state

    def _output_backend_label(self) -> str:
        if not self.settings.hardware:
            return "simulation"
        return self.settings.output_backend

    def _output_device_paths(self) -> tuple[str, ...]:
        if not self.settings.hardware:
            return ()
        if self.settings.output_backend == "gpio":
            return (self.settings.chip,)
        devices = [self.settings.spi_device]
        if self.settings.spi_device_2 is not None:
            devices.append(self.settings.spi_device_2)
        return tuple(devices)

    def _color_correction_profiles(self) -> tuple[ColorCorrectionProfile, ...]:
        devices = self._output_device_paths()
        profiles: list[ColorCorrectionProfile] = []
        for index, controller in enumerate(self._correction_controllers):
            correction = controller.correction
            device = (
                devices[index]
                if index < len(devices)
                else ("Simulation" if not self.settings.hardware else f"Output {index + 1}")
            )
            profiles.append(
                ColorCorrectionProfile(
                    output_index=index,
                    name="Primary" if index == 0 else "Secondary",
                    device=device,
                    red=correction.red,
                    green=correction.green,
                    blue=correction.blue,
                )
            )
        return tuple(profiles)

    def _calibration_status(self) -> CalibrationStatus:
        session = self._calibration
        if session is None:
            return CalibrationStatus()
        remaining = max(
            0.0,
            CALIBRATION_TIMEOUT_SECONDS
            - (time.monotonic() - session.last_activity_at),
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
        if self._configured_audio_source is AudioSource.DEMO:
            if self._active_audio_source is not AudioSource.DEMO:
                return "inactive"
            return "demo"
        if self._configured_audio_source is AudioSource.OFF:
            return "inactive"
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
        if (
            self.settings.hardware
            and running
            and self._calibration is None
            and not (controller is not None and controller.blackout)
            and uptime_seconds > 3.0
            and (
                controller is None
                or controller.last_successful_update_age_seconds is None
                or controller.last_successful_update_age_seconds > 2.0
            )
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
        try:
            self._close_audio_input()
        except Exception as exc:  # noqa: BLE001 - cleanup continues after individual failures
            self._fatal_error = self._fatal_error or str(exc)
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
        self._audio_input = None
        self._active_audio_source = AudioSource.OFF
        if audio_input is not None:
            audio_input.close()

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
