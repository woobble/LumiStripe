"""Single-writer runtime worker for the web application."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import datetime
from importlib import metadata
from typing import Literal, TypedDict, cast
from uuid import uuid4

import numpy as np
from lumistripe import (
    Color,
    ColorCorrection,
    ColorCorrectionController,
    CompositeController,
    Config,
    Controller,
    MultiController,
    NullController,
    OutputPowerEstimate,
    ReversedController,
    Rgb,
    ScaledMultiController,
    Stripe,
    apply_power_budget,
    estimate_frame_power,
)
from lumistripe.animation import AnimationPlayer
from lumistripe.animation.catalog import (
    AnimationDefinition,
    animation_catalog,
    dynamic_sequence_eligible,
)
from lumistripe.audio.capture import AudioInput
from lumistripe.audio.devices import list_input_device_details
from lumistripe.audio.hardware_gain import HardwareGainController
from lumistripe.audio.timeline import MusicalTimelineConfig
from lumistripe.audio.types import (
    AudioFrame,
    AudioInputHealth,
    AudioSnapshot,
    BandTuple,
    BeatEvent,
    MusicFeatures,
)
from lumistripe.gpio import GPIOStripe
from lumistripe.gpio.spi import SPIConfig, SPIStripe
from lumistripe.playback import (
    AnimationSequence,
    AudioSource,
    MusicActivityDetector,
    PlaybackConfig,
    PlaybackEngine,
    PlaybackMode,
    QuantizedSwitchDiagnostics,
    SwitchQuantization,
    TransitionEffect,
    demo_snapshot,
)

from ..bluetooth import (
    SPOTIFY_MONITOR,
    BluetoothAudioBackend,
    BluetoothCommandError,
    BluetoothManager,
    BluetoothStatus,
    capture_device_selector,
    spotify_capture_device_selector,
)
from ..contracts.sequencer import (
    AnimationSequenceContract,
    SequenceLibraryResponse,
)
from ..models import (
    AnimationOption,
    AnimationSettingOption,
    AnimationSettingsResponse,
    AudioCalibrationSessionResponse,
    AudioDeviceOption,
    AudioOutputDeviceInfo,
    AudioSettingsResponse,
    AudioSyncCalibrationResponse,
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
    PowerBudgetState,
    PowerOutputState,
    SequenceLibraryEntry,
    SequenceLibrarySummary,
    SequencePlaybackDiagnostics,
    SequencerDiagnostics,
    SequencerSettingsResponse,
    SpotifyStatusResponse,
    SpotifyTrackInfo,
    StartupPlaybackState,
    StartupSettingsResponse,
    StripeOutputConfig,
    StripePlaybackState,
    StripeTopology,
    TransitionSettingsResponse,
)
from ..models import AudioCalibrationResult as AudioCalibrationResultModel
from ..settings import (
    MAX_SAVED_ANIMATION_SEQUENCES,
    SHARED_ANIMATION_SETTINGS_KEY,
    AudioTuningProfile,
    CalibrationSettingsStore,
    StartupPlaybackSettings,
    StripeTopologySettings,
    TransitionSettings,
)
from ..spotify import (
    SpotifyBackend,
    SpotifyClient,
    SpotifyCommandError,
)
from .audio import default_audio_factory
from .audio_service import complete_audio_calibration, complete_microphone_calibration
from .calibration_service import calibration_color
from .commands import (
    _AnimationSettingsCommand,
    _AudioCalibrationFinishCommand,
    _AudioCalibrationMusicCommand,
    _AudioCalibrationStartCommand,
    _AudioDeviceCommand,
    _AudioSettingsCommand,
    _AudioSourceCommand,
    _AudioSyncCalibrationCommand,
    _AudioSyncOffsetCommand,
    _CalibrationFinishCommand,
    _CalibrationStartCommand,
    _CalibrationUpdateCommand,
    _Command,
    _ModeCommand,
    _SequenceLibraryCommand,
    _SequencerSettingsCommand,
    _SpotifyCommand,
    _StartupSettingsCommand,
    _StripeTestCommand,
    _StripeTopologyCommand,
    _TargetCommand,
    _TransitionSettingsCommand,
)
from .outputs import OutputGateController
from .playback_service import PlaybackPolicy, PlaybackPolicyError
from .state import (
    CalibrationPattern,
    PreviewFrame,
    RuntimeSettings,
    _AudioCalibrationSession,
    _AudioFactory,
    _BuiltTopology,
    _CalibrationSession,
    _ControllerFactory,
    _StripeTestSession,
)
from .sync_calibration import AudioSyncCalibrationTone, sync_calibration_led_on
from .topology import (
    default_controller_factory as topology_controller_factory,
)
from .topology import (
    topology_from_runtime,
)

# Keep the historical private names importable while the implementation moves
# into focused runtime modules.
_default_audio_factory = default_audio_factory
_topology_from_runtime = topology_from_runtime


def _default_controller_factory(settings: RuntimeSettings) -> Controller:
    """Preserve injectable hardware symbols for existing integrations/tests."""

    return topology_controller_factory(
        settings,
        gpio_factory=GPIOStripe,
        spi_factory=SPIStripe,
    )

MIN_FRAME_SECONDS = 0.016
FPS_SAMPLE_SECONDS = 1.0
AUDIO_ROUTE_POLL_SECONDS = 1.0
CALIBRATION_FRAME_SECONDS = 0.05
CALIBRATION_TIMEOUT_SECONDS = 300.0
WORKER_STARTUP_GRACE_SECONDS = 3.0
WORKER_WATCHDOG_INTERVAL_SECONDS = 0.1
WORKER_STOP_GRACE_SECONDS = 0.5
FRAME_STALL_GRACE_SECONDS = 0.5
FRAME_STALL_MIN_SECONDS = 0.75
FRAME_STALL_MAX_SECONDS = 5.0
FRAME_TIMING_WINDOW_SECONDS = 10.0
FRAME_TIMING_MIN_MISSES = 5
FRAME_TIMING_MISS_RATE_THRESHOLD = 0.10
BLUETOOTH_PROFILE_KEY = "Bluetooth music"
SPOTIFY_PROFILE_KEY = "Spotify music"

try:
    APPLICATION_VERSION = metadata.version("lumistripe-web")
except metadata.PackageNotFoundError:
    APPLICATION_VERSION = "development"


class RuntimeCommandError(RuntimeError):
    """A command was valid but could not be applied by the runtime."""


class UnknownAnimationError(RuntimeCommandError):
    pass


class UnknownSequenceError(RuntimeCommandError):
    pass


class InvalidSequenceError(RuntimeCommandError):
    pass


class SequenceConflictError(RuntimeCommandError):
    pass


class SequencePersistenceError(RuntimeCommandError):
    pass


class InvalidAnimationSettingsError(RuntimeCommandError):
    pass


class AnimationSettingsPersistenceError(RuntimeCommandError):
    pass


class RuntimeUnavailableError(RuntimeError):
    pass


def _power_budget_state_for(
    topology: StripeTopologySettings,
    *,
    estimated_watts: float = 0.0,
    applied_scale: float = 1.0,
    limiting_output_id: str | None = None,
    outputs: tuple[PowerOutputState, ...] = (),
) -> PowerBudgetState:
    shared_supply_enabled = topology.shared_supply_enabled
    return PowerBudgetState(
        enabled=topology.power_limiting_enabled,
        budget_watts=topology.effective_power_budget_watts,
        estimated_watts=estimated_watts,
        applied_scale=applied_scale,
        limiting_output_id=limiting_output_id,
        outputs=outputs,
        shared_supply_enabled=shared_supply_enabled,
        supply_capacity_watts=(
            topology.supply_capacity_watts if shared_supply_enabled else None
        ),
        pi_reserve_watts=topology.pi_reserve_watts if shared_supply_enabled else None,
        available_led_watts=(
            topology.available_led_watts if shared_supply_enabled else None
        ),
    )


@dataclass(frozen=True, slots=True)
class _FrameTimingStats:
    frame_count: int = 0
    missed_frame_count: int = 0
    miss_rate: float = 0.0


class _FrameTimingWindow:
    """Bounded recent frame outcomes used for renderer health diagnostics."""

    def __init__(self, *, window_seconds: float = FRAME_TIMING_WINDOW_SECONDS) -> None:
        if window_seconds <= 0.0:
            raise ValueError("frame timing window must be greater than zero")
        self._window_seconds = window_seconds
        self._samples: deque[tuple[float, bool]] = deque()

    def reset(self) -> None:
        self._samples.clear()

    def record(self, completed_at_s: float, *, missed_deadline: bool) -> None:
        self._samples.append((completed_at_s, missed_deadline))
        self._prune(completed_at_s)

    def stats(self, now_s: float) -> _FrameTimingStats:
        self._prune(now_s)
        frame_count = len(self._samples)
        missed_frame_count = sum(
            missed_deadline for _, missed_deadline in self._samples
        )
        return _FrameTimingStats(
            frame_count=frame_count,
            missed_frame_count=missed_frame_count,
            miss_rate=(missed_frame_count / frame_count if frame_count else 0.0),
        )

    def _prune(self, now_s: float) -> None:
        cutoff = now_s - self._window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


def _frame_deadline_missed(
    *, due_at_s: float, completed_at_s: float, expected_delay_s: float
) -> bool:
    """Return whether a frame completed after its effective presentation budget."""

    deadline = due_at_s + max(expected_delay_s, MIN_FRAME_SECONDS)
    return completed_at_s > deadline


class RuntimeWorker:
    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        *,
        controller_factory: _ControllerFactory = _default_controller_factory,
        audio_factory: _AudioFactory = _default_audio_factory,
        bluetooth_manager: BluetoothAudioBackend | None = None,
        spotify_manager: SpotifyBackend | None = None,
        fatal_exit: Callable[[int], None] | None = None,
    ) -> None:
        self.settings = settings or RuntimeSettings()
        self._controller_factory = controller_factory
        self._uses_default_controller_factory = (
            controller_factory is _default_controller_factory
        )
        self._audio_factory = audio_factory
        self._bluetooth = bluetooth_manager or BluetoothManager(
            enabled=self.settings.hardware,
            spotify_audio_routing_enabled=(
                self.settings.hardware or self.settings.spotify_enabled
            ),
        )
        spotify_available = self.settings.hardware or self.settings.spotify_enabled
        self._spotify = spotify_manager or SpotifyClient(enabled=spotify_available)
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
        self._audio_sync_offsets_ms = loaded_settings.audio_sync_offsets_ms
        self._selected_audio_device = (
            loaded_settings.selected_audio_device or self.settings.audio_device
        )
        self._restore_last_state = loaded_settings.restore_last_state
        self._switch_quantization = loaded_settings.switch_quantization
        self._transition_settings = loaded_settings.transition_settings
        self._bars_per_phrase = cast(
            Literal[4, 8, 16], loaded_settings.bars_per_phrase
        )
        self._animation_sequences = loaded_settings.animation_sequences
        self._animation_settings = {
            scope: {name: dict(values) for name, values in animations.items()}
            for scope, animations in loaded_settings.animation_settings.items()
        }
        self._active_sequence_id = loaded_settings.active_sequence_id
        self._active_animation_sequence = next(
            (
                sequence
                for sequence in self._animation_sequences
                if sequence.id == self._active_sequence_id
            ),
            None,
        )
        self._sequence_library_snapshot = self._sequence_library_response(
            self._animation_sequences, self._active_sequence_id
        )
        self._remembered_playback = loaded_settings.remembered_playback
        self._settings_io_lock = threading.RLock()
        self._startup_persist_lock = threading.Lock()
        self._startup_persist_timer: threading.Timer | None = None
        self._audio_profile = self._audio_profiles.get(
            self._selected_audio_device or "", AudioTuningProfile()
        )
        self._playback_policy = PlaybackPolicy()
        self.player = AnimationPlayer.party()
        self._apply_saved_animation_settings(
            self.player, SHARED_ANIMATION_SETTINGS_KEY
        )
        self.playback = PlaybackEngine(
            self.player,
            PlaybackConfig(
                mode=PlaybackMode.STATIC,
                switch_quantization=self._switch_quantization,
                transition_duration_s=(
                    self._transition_settings.duration_ms / 1000.0
                    if self._transition_settings.enabled
                    else 0.0
                ),
                transition_effect=self._transition_settings.effect,
                musical_timeline=MusicalTimelineConfig(
                    bars_per_phrase=self._bars_per_phrase
                ),
                activity=self._audio_profile.activity_config(),
                dynamic_response=self._audio_profile.dynamic_response,
            ),
        )
        if self._active_animation_sequence is not None:
            try:
                self.playback.set_animation_sequence(self._active_animation_sequence)
            except ValueError as exc:
                # Keep the saved definition visible, but an animation that is
                # no longer Dynamic-eligible must not control playback.
                self._active_animation_sequence = None
                warning = f"Saved animation sequence is not available: {exc}"
                self._settings_warning = (
                    f"{self._settings_warning}; {warning}"
                    if self._settings_warning
                    else warning
                )
        self._monitor_detector = MusicActivityDetector(
            self._audio_profile.activity_config()
        )
        self.player.set_brightness(1.0)

        self._commands: queue.Queue[_Command | None] = queue.Queue()
        self._command_lock = threading.Lock()
        self._coalesced_commands: dict[tuple[str, str | None], _Command] = {}
        self._stop_event = threading.Event()
        self._watchdog_stop_event = threading.Event()
        self._started_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._snapshot_lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._revision = 0
        self._preview_sequence = 0
        self._preview_outputs: tuple[bytes, ...] = ()
        self._power_preview_override: tuple[bytes, ...] | None = None
        self._power_budget_state = _power_budget_state_for(self._topology)
        self._snapshot = DashboardState(
            runtime=self.settings.kind,
            mode=PlaybackMode.STATIC,
            animation=self._current_animation(),
            configured_audio_source=self.settings.audio_source,
            active_audio_source=AudioSource.OFF.value,
            audio_monitoring=False,
            sequencer_settings=SequencerSettingsResponse(
                switch_quantization=self._switch_quantization,
                bars_per_phrase=self._bars_per_phrase,
            ),
            transition_settings=TransitionSettingsResponse(
                enabled=self._transition_settings.enabled,
                duration_ms=self._transition_settings.duration_ms,
                effect=self._transition_settings.effect,
            ),
            sequence_library=self._sequence_library_summary(),
        )

        self._raw_controller: Controller | None = None
        self._controller: OutputGateController | None = None
        self._output_gates: tuple[OutputGateController, ...] = ()
        self._playbacks: dict[str, tuple[AnimationPlayer, PlaybackEngine]] = {}
        self._animation_settings_snapshot = self._build_animation_settings_snapshot()
        self._correction_controllers: tuple[ColorCorrectionController, ...] = ()
        self._calibration: _CalibrationSession | None = None
        self._audio_calibration: _AudioCalibrationSession | None = None
        self._audio_calibration_snapshot: AudioCalibrationSessionResponse | None = None
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
        self._spotify_last_active: bool | None = None
        self._audio_frame = AudioFrame()
        self._last_beat_event: BeatEvent | None = None
        self._sync_calibration_start_s: float | None = None
        self._sync_calibration_until_s: float | None = None
        self._sync_calibration_route: str | None = None
        self._sync_calibration_player: AudioSyncCalibrationTone | None = None
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
        self._fatal_exit = fatal_exit
        self._fatal_exit_lock = threading.Lock()
        self._fatal_exit_requested = False
        self._fatal_failure = threading.Event()
        self._worker_state_lock = threading.Lock()
        self._last_step_completed_s: float | None = None
        self._last_step_expected_delay_s = MIN_FRAME_SECONDS
        self._last_step_started_s: float | None = None
        self._missed_frame_count = 0
        self._last_render_time_ms = 0.0
        self._frame_timing = _FrameTimingWindow()

    @property
    def healthy(self) -> bool:
        state = self.snapshot()
        return state.running and state.error is None

    def start(self, *, timeout: float = 5.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._watchdog_stop_event.clear()
        self._started_event.clear()
        self._fatal_exit_requested = False
        self._fatal_failure.clear()
        self._fatal_error = None
        self._last_command_error = None
        with self._worker_state_lock:
            self._last_step_completed_s = None
            self._last_step_started_s = None
            self._last_step_expected_delay_s = MIN_FRAME_SECONDS
            self._missed_frame_count = 0
            self._last_render_time_ms = 0.0
            self._frame_timing.reset()
        self._thread = threading.Thread(
            target=self._run,
            name="lumistripe-runtime",
            daemon=False,
        )
        self._thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="lumistripe-runtime-watchdog",
            daemon=False,
        )
        self._watchdog_thread.start()
        if not self._started_event.wait(timeout):
            raise RuntimeUnavailableError("runtime startup timed out")

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._watchdog_stop_event.set()
        self._commands.put(None)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise RuntimeUnavailableError("runtime shutdown timed out")
        self._thread = None
        watchdog = self._watchdog_thread
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout)
            if watchdog.is_alive():
                raise RuntimeUnavailableError("runtime watchdog shutdown timed out")
        self._watchdog_thread = None

    def set_fatal_exit(self, callback: Callable[[int], None] | None) -> None:
        """Set the process-exit callback used after an unexpected worker failure."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeCommandError("fatal exit callback must be set before start")
        self._fatal_exit = callback

    def snapshot(self) -> DashboardState:
        with self._snapshot_lock:
            return self._snapshot

    def sequencer_settings(self) -> SequencerSettingsResponse:
        return self.snapshot().sequencer_settings

    def transition_settings(self) -> TransitionSettingsResponse:
        return self.snapshot().transition_settings

    def animation_sequences(self) -> SequenceLibraryResponse:
        with self._snapshot_lock:
            return self._sequence_library_snapshot

    def preview_frame(self) -> PreviewFrame:
        with self._preview_lock:
            return PreviewFrame(self._preview_sequence, self._preview_outputs)

    def animations(self) -> tuple[AnimationOption, ...]:
        return tuple(
            AnimationOption(
                name=entry.animation.name,
                display_name=(
                    entry.definition.display_name
                    if entry.definition is not None
                    and entry.definition.display_name is not None
                    else entry.animation.name.replace("_", " ").title()
                ),
                description=(
                    entry.animation.metadata.description
                    or f"{entry.animation.metadata.mood.replace('_', ' ').capitalize()} lighting effect."
                ),
                role=entry.animation.metadata.role.value,
                mood=entry.animation.metadata.mood,
                dynamic_safe=entry.animation.metadata.dynamic_safe,
                sequence_eligible=(
                    entry.definition is not None
                    and dynamic_sequence_eligible(entry.definition)
                ),
                settings=(
                    tuple(
                        AnimationSettingOption(
                            id=option.id,
                            label=option.label,
                            type=option.type.value,
                            default=option.default,
                            description=option.description,
                            minimum=option.minimum,
                            maximum=option.maximum,
                            step=option.step,
                            unit=option.unit,
                            choices=option.choices,
                            visible_when_option=option.visible_when_option,
                            visible_when_value=option.visible_when_value,
                        )
                        for option in entry.definition.settings
                    )
                    if entry.definition is not None
                    else ()
                ),
            )
            for entry in self.player.animations
        )

    def animation_settings(
        self, animation_name: str, stripe_id: str | None = None
    ) -> AnimationSettingsResponse:
        definition = next(
            (item for item in animation_catalog(self.player) if item.name == animation_name),
            None,
        )
        if definition is None:
            raise UnknownAnimationError(f"unknown animation: {animation_name}")
        if not definition.settings:
            raise InvalidAnimationSettingsError(
                f"animation {animation_name} has no configurable settings"
            )
        key = (animation_name, stripe_id)
        with self._snapshot_lock:
            response = self._animation_settings_snapshot.get(key)
            if response is None:
                raise InvalidAnimationSettingsError(
                    f"unknown stripe: {stripe_id}" if stripe_id else "animation settings are unavailable"
                )
            return response.model_copy(deep=True)

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
            if self.settings.hardware or self.settings.spotify_enabled:
                options_list.append(
                    AudioDeviceOption(
                        selector="spotify",
                        name=SPOTIFY_PROFILE_KEY,
                        settings=_profile_values(
                            self._audio_profiles.get(
                                SPOTIFY_PROFILE_KEY, AudioTuningProfile()
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
            else SPOTIFY_PROFILE_KEY
            if self._monitor_audio_source is AudioSource.SPOTIFY
            else (active_name or self._selected_audio_device)
        )
        active_selector = next(
            (option.selector for option in options if option.name == selected_name),
            "bluetooth"
            if self._monitor_audio_source is AudioSource.BLUETOOTH
            else "spotify"
            if self._monitor_audio_source is AudioSource.SPOTIFY
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
        sync_route = self._audio_sync_route_key()
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
                    else SPOTIFY_PROFILE_KEY
                    if self._monitor_audio_source is AudioSource.SPOTIFY
                    else (active_name or self._selected_audio_device or ""),
                    self._audio_profile,
                )
            ),
            configured_noise_floor=self._audio_profile.audio_config().smoothing.noise_floor,
            sync_route=sync_route,
            sync_offset_ms=self._audio_sync_offsets_ms.get(sync_route, 0.0),
            **_hardware_gain_values(self._hardware_gain),
            bluetooth=self._bluetooth_status_response(),
            spotify=self._spotify_status_response(),
            error=self._audio_monitor_error or enumeration_error,
        )

    def bluetooth_status(self) -> BluetoothStatusResponse:
        return self._bluetooth_status_response()

    def _spotify_status_response(self) -> SpotifyStatusResponse:
        current = self._spotify.status()
        track = current.track
        return SpotifyStatusResponse(
            configured=current.configured,
            connected=current.connected,
            logged_in=current.logged_in,
            is_active=current.is_active,
            device_name=current.device_name,
            status=current.status,
            track=(
                SpotifyTrackInfo(
                    uri=track.uri,
                    name=track.name,
                    artists=track.artists,
                    album=track.album,
                    cover_url=track.cover_url,
                    duration_ms=track.duration_ms,
                )
                if track is not None
                else None
            ),
            position_ms=current.position_ms,
            duration_ms=current.duration_ms,
            volume=current.volume,
            shuffle=current.shuffle,
            repeat=current.repeat,
            error=current.error,
        )

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

    def set_sequencer_settings(
        self, switch_quantization: SwitchQuantization, bars_per_phrase: int
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "sequencer_settings",
                _SequencerSettingsCommand(switch_quantization, bars_per_phrase),
            ),
        )

    def set_transition_settings(
        self,
        enabled: bool,
        duration_ms: int,
        effect: TransitionEffect | None = None,
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "transition_settings",
                _TransitionSettingsCommand(enabled, duration_ms, effect),
            ),
        )

    def set_animation_settings(
        self,
        animation_name: str,
        values: dict[str, object],
        *,
        stripe_id: str | None = None,
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "animation_settings",
                _AnimationSettingsCommand(animation_name, values, stripe_id),
            ),
        )

    def reset_animation_settings(
        self, animation_name: str, *, stripe_id: str | None = None
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "animation_settings",
                _AnimationSettingsCommand(
                    animation_name, None, stripe_id, reset=True
                ),
            ),
        )

    def create_animation_sequence(
        self, sequence: AnimationSequence
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "sequence_library",
                _SequenceLibraryCommand("create", sequence.id, sequence),
            ),
        )

    def update_animation_sequence(
        self, sequence_id: str, sequence: AnimationSequence
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "sequence_library",
                _SequenceLibraryCommand("update", sequence_id, sequence),
            ),
        )

    def delete_animation_sequence(
        self, sequence_id: str
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "sequence_library",
                _SequenceLibraryCommand("delete", sequence_id),
            ),
        )

    def set_active_animation_sequence(
        self, sequence_id: str | None
    ) -> Future[DashboardState]:
        return cast(
            Future[DashboardState],
            self._submit(
                "sequence_library",
                _SequenceLibraryCommand("activate", sequence_id),
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

    def set_audio_sync_offset(self, offset_ms: float) -> Future[AudioSettingsResponse]:
        return cast(
            Future[AudioSettingsResponse],
            self._submit("audio_sync_offset", _AudioSyncOffsetCommand(offset_ms)),
        )

    def set_audio_sync_calibration(
        self, active: bool
    ) -> Future[AudioSyncCalibrationResponse]:
        return cast(
            Future[AudioSyncCalibrationResponse],
            self._submit(
                "audio_sync_calibration", _AudioSyncCalibrationCommand(active)
            ),
        )

    def spotify_status(self) -> SpotifyStatusResponse:
        return self._spotify_status_response()

    def control_spotify(
        self, action: str, value: object | None = None
    ) -> Future[SpotifyStatusResponse]:
        return cast(
            Future[SpotifyStatusResponse],
            self._submit("spotify", _SpotifyCommand(action, value)),
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

    def start_audio_calibration(self, device: str, duration_seconds: float, two_step: bool = False) -> Future[AudioCalibrationSessionResponse]:
        return cast(Future[AudioCalibrationSessionResponse], self._submit("audio_calibration_start", _AudioCalibrationStartCommand(device, duration_seconds, two_step)))

    def begin_audio_calibration_music(self, session_id: str) -> Future[AudioCalibrationSessionResponse]:
        return cast(Future[AudioCalibrationSessionResponse], self._submit("audio_calibration_music", _AudioCalibrationMusicCommand(session_id)))

    def audio_calibration_status(self, session_id: str) -> AudioCalibrationSessionResponse:
        with self._snapshot_lock:
            snapshot = self._audio_calibration_snapshot
        if snapshot is None or snapshot.session_id != session_id:
            raise RuntimeCommandError("audio calibration session not found")
        return snapshot.model_copy(deep=True)

    def _publish_audio_calibration(self, session_id: str) -> AudioCalibrationSessionResponse:
        session = self._audio_calibration
        if session is None or session.session_id != session_id:
            raise RuntimeCommandError("audio calibration session not found")
        elapsed = min(session.duration_seconds, max(0.0, time.monotonic() - session.started_at))
        status = "failed" if session.error else "complete" if session.result is not None else "waiting" if session.phase == "waiting_music" else "capturing"
        result = None
        if session.result is not None:
            result = AudioCalibrationResultModel(**session.result)
        response = AudioCalibrationSessionResponse(
            session_id=session.session_id,
            phase=session.phase,
            status=status,
            elapsed_seconds=elapsed,
            remaining_seconds=max(0.0, session.duration_seconds - elapsed),
            result=result,
            error=session.error,
        )
        with self._snapshot_lock:
            self._audio_calibration_snapshot = response
        return response.model_copy(deep=True)

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
        coalesce_key = self._coalesce_key(name, value)
        with self._command_lock:
            if coalesce_key is not None:
                existing = self._coalesced_commands.get(coalesce_key)
                if existing is not None:
                    existing.value = value
                    existing.coalesced_futures.append(future)
                    return future
            command = _Command(name, value, future)
            if coalesce_key is not None:
                self._coalesced_commands[coalesce_key] = command
            self._commands.put(command)
        return future

    @staticmethod
    def _coalesce_key(name: str, value: object) -> tuple[str, str | None] | None:
        if name != "brightness" or not isinstance(value, _TargetCommand):
            return None
        return (name, value.stripe_id)

    def _take_command(self, command: _Command) -> None:
        coalesce_key = self._coalesce_key(command.name, command.value)
        if coalesce_key is None:
            return
        with self._command_lock:
            if self._coalesced_commands.get(coalesce_key) is command:
                del self._coalesced_commands[coalesce_key]

    @staticmethod
    def _resolve_command(
        command: _Command, *, result: object | None = None, error: Exception | None = None
    ) -> None:
        for future in command.futures():
            if future.done():
                continue
            if error is None:
                future.set_result(result)
            else:
                future.set_exception(error)

    def _run(self) -> None:
        unexpected_failure = False
        try:
            if self._uses_default_controller_factory:
                built = self._build_topology(self._topology)
                self._install_topology(built)
            else:
                physical_controller = self._controller_factory(self.settings)
                self._raw_controller = self._with_color_correction(physical_controller)
                self._controller = OutputGateController(self._raw_controller)
                if len(self._correction_controllers) == len(self._topology.outputs):
                    self._output_gates = tuple(
                        OutputGateController(controller)
                        for controller in self._correction_controllers
                    )
                    self._sync_independent_playbacks()
            self._started_at_s = time.monotonic()
            self._fps_window_started_s = self._started_at_s
            self._bluetooth.start()
            self._spotify.start()
            self._initialize_audio_monitor()
            self._apply_remembered_startup()
            self._publish(running=True)
            self._started_event.set()
            self._frame_loop()
        except Exception as exc:  # noqa: BLE001 - a thread boundary must publish all failures
            unexpected_failure = True
            self._fatal_failure.set()
            self._fatal_error = str(exc)
            self._publish(running=False, error=self._fatal_error)
            self._started_event.set()
        finally:
            self._cleanup()
            self._reject_pending()
            self._publish(running=False, error=self._fatal_error)
            if unexpected_failure or self._fatal_failure.is_set():
                self._request_fatal_exit()

    def _request_fatal_exit(self) -> None:
        callback = self._fatal_exit
        if callback is None:
            return
        with self._fatal_exit_lock:
            if self._fatal_exit_requested:
                return
            self._fatal_exit_requested = True
        callback(1)

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop_event.wait(WORKER_WATCHDOG_INTERVAL_SECONDS):
            thread = self._thread
            if thread is None or not thread.is_alive():
                return
            if not self._worker_is_stalled():
                continue

            self._fatal_failure.set()
            self._fatal_error = (
                "the rendering worker stopped making progress before its frame deadline"
            )
            self._stop_event.set()
            self._commands.put(None)
            thread.join(WORKER_STOP_GRACE_SECONDS)
            if thread.is_alive():
                self._request_fatal_exit()
            return

    def _worker_is_stalled(self) -> bool:
        if self._stop_event.is_set() or not self._started_event.is_set():
            return False
        started_at = self._started_at_s
        if started_at is None or time.monotonic() - started_at < WORKER_STARTUP_GRACE_SECONDS:
            return False
        if self._calibration is not None or self._stripe_test is not None:
            return False
        if self._output_gates and all(gate.blackout for gate in self._output_gates):
            return False
        with self._worker_state_lock:
            completed_at = self._last_step_completed_s
            expected_delay = self._last_step_expected_delay_s
        if completed_at is None:
            return False
        timeout = min(
            FRAME_STALL_MAX_SECONDS,
            max(
                FRAME_STALL_MIN_SECONDS,
                expected_delay * 4.0 + FRAME_STALL_GRACE_SECONDS,
            ),
        )
        return time.monotonic() - completed_at > timeout

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
            self._apply_saved_animation_settings(player, output.id)
            player.set_brightness(self.player.brightness)
            engine = PlaybackEngine(
                player,
                PlaybackConfig(
                    mode=self.playback.mode,
                    solid_color=self.playback.solid_color,
                    switch_quantization=self._switch_quantization,
                    transition_duration_s=(
                        self._transition_settings.duration_ms / 1000.0
                        if self._transition_settings.enabled
                        else 0.0
                    ),
                    transition_effect=self._transition_settings.effect,
                    musical_timeline=MusicalTimelineConfig(
                        bars_per_phrase=self._bars_per_phrase
                    ),
                    activity=self._audio_profile.activity_config(),
                    dynamic_response=self._audio_profile.dynamic_response,
                ),
            )
            if self._active_animation_sequence is not None:
                engine.set_animation_sequence(self._active_animation_sequence)
            current = self.player.name_at(self.player.current_index())
            current_index = None if current is None else player.index_of(current)
            if current_index is not None:
                player.set_index(current_index, transition_ms=0)
            engine.set_sync_offset(self.playback.sync_offset_s)
            self._playbacks[output.id] = (player, engine)

    def _frame_loop(self) -> None:
        next_frame_at = time.monotonic()
        pending_command: _Command | None = None
        with self._worker_state_lock:
            self._last_step_completed_s = next_frame_at
            self._last_step_expected_delay_s = MIN_FRAME_SECONDS
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now >= next_frame_at:
                with self._worker_state_lock:
                    self._last_step_started_s = now
                delay = self._step()
                completed_at = time.monotonic()
                missed_deadline = _frame_deadline_missed(
                    due_at_s=next_frame_at,
                    completed_at_s=completed_at,
                    expected_delay_s=delay,
                )
                with self._worker_state_lock:
                    if missed_deadline:
                        self._missed_frame_count += 1
                    self._last_render_time_ms = max(0.0, completed_at - now) * 1000.0
                    self._last_step_completed_s = completed_at
                    self._last_step_expected_delay_s = max(delay, MIN_FRAME_SECONDS)
                self._frame_timing.record(
                    completed_at,
                    missed_deadline=missed_deadline,
                )
                next_frame_at = completed_at + max(delay, MIN_FRAME_SECONDS)
                continue

            if pending_command is not None:
                self._take_command(pending_command)
                command = pending_command
                pending_command = None
                self._execute(command)
                continue

            timeout = max(0.0, min(next_frame_at - now, 0.05))
            next_command: _Command | None
            try:
                next_command = self._commands.get(timeout=timeout)
            except queue.Empty:
                continue
            if next_command is None:
                break
            pending_command = next_command

        if pending_command is not None:
            self._take_command(pending_command)
            self._resolve_command(
                pending_command,
                error=RuntimeUnavailableError(
                    self._fatal_error or "runtime stopped"
                ),
            )

    def _execute(self, command: _Command) -> None:
        if all(future.cancelled() or future.done() for future in command.futures()):
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
            elif command.name == "audio_sync_offset":
                sync_offset = _expect(command.value, _AudioSyncOffsetCommand)
                result = self._set_audio_sync_offset(sync_offset.offset_ms)
                self._publish(running=True)
            elif command.name == "sequencer_settings":
                sequencer_settings = _expect(
                    command.value, _SequencerSettingsCommand
                )
                self._set_sequencer_settings(
                    sequencer_settings.switch_quantization,
                    sequencer_settings.bars_per_phrase,
                )
                result = self._publish(running=True)
            elif command.name == "transition_settings":
                transition_settings = _expect(
                    command.value, _TransitionSettingsCommand
                )
                self._set_transition_settings(
                    transition_settings.enabled,
                    transition_settings.duration_ms,
                    transition_settings.effect,
                )
                result = self._publish(running=True)
            elif command.name == "animation_settings":
                animation_settings = _expect(
                    command.value, _AnimationSettingsCommand
                )
                self._set_animation_settings(animation_settings)
                result = self._publish(running=True)
            elif command.name == "sequence_library":
                sequence_command = _expect(
                    command.value, _SequenceLibraryCommand
                )
                self._set_sequence_library(sequence_command)
                result = self._publish(running=True)
            elif command.name == "audio_sync_calibration":
                sync_calibration = _expect(
                    command.value, _AudioSyncCalibrationCommand
                )
                result = self._set_audio_sync_calibration(sync_calibration.active)
            elif command.name == "spotify":
                spotify = _expect(command.value, _SpotifyCommand)
                try:
                    self._spotify.control(spotify.action, spotify.value)
                except SpotifyCommandError as exc:
                    raise RuntimeCommandError(str(exc)) from exc
                self._publish(running=True)
                result = self._spotify_status_response()
            elif command.name == "audio_calibration_start":
                calibration = _expect(command.value, _AudioCalibrationStartCommand)
                result = self._start_audio_calibration(calibration.device, calibration.duration_seconds, calibration.two_step)
            elif command.name == "audio_calibration_music":
                music = _expect(command.value, _AudioCalibrationMusicCommand)
                result = self._begin_audio_calibration_music(music.session_id)
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
            self._resolve_command(command, result=result)
        except Exception as exc:  # noqa: BLE001 - every command must resolve its future
            self._last_command_error = str(exc)
            self._publish(running=True)
            self._resolve_command(command, error=exc)

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
        try:
            decision = self._playback_policy.decide(
                mode,
                self._configured_audio_source,
                audio_input_available=self._audio_input is not None,
                unavailable_message=self._audio_monitor_error,
            )
        except PlaybackPolicyError as exc:
            raise RuntimeCommandError(str(exc)) from exc
        if decision.requires_audio:
            source = decision.audio_source
            self._active_audio_source = source
            self._audio_status = (
                "Using internal demo beat."
                if source is AudioSource.DEMO
                else (
                    (
                        f"Bluetooth: {self._active_audio_device_name}"
                        if self._monitor_audio_source is AudioSource.BLUETOOTH
                        else "Spotify: waiting for playback"
                        if self._monitor_audio_source is AudioSource.SPOTIFY
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
        self._power_budget_state = _power_budget_state_for(topology)
        self._power_preview_override = None
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
        self._power_budget_state = _power_budget_state_for(topology)
        self._power_preview_override = None
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

        if self._configured_audio_source is AudioSource.SPOTIFY:
            spotify = self._spotify.status()
            if not spotify.connected or not spotify.logged_in:
                self._spotify_last_active = None
                if self._audio_input is not None:
                    self._close_audio_input()
                try:
                    self._open_spotify_monitor()
                except (BluetoothCommandError, RuntimeError) as exc:
                    self._monitor_audio_source = AudioSource.SPOTIFY
                    self._active_audio_device_name = None
                    self._audio_monitor_error = str(exc)
                    self._audio_status = f"Spotify audio unavailable: {exc}"
                return

            active_changed = (
                self._spotify_last_active is not None
                and self._spotify_last_active != spotify.is_active
            )
            self._spotify_last_active = spotify.is_active
            if active_changed and not spotify.is_active:
                if self._audio_input is not None:
                    self._close_audio_input()
                self._monitor_audio_source = AudioSource.SPOTIFY
                self._active_audio_device_name = None
                self._audio_monitor_error = None
                self._audio_status = (
                    "Spotify is playing on another device; select LumiStripe in Spotify "
                    "to resume the local audio monitor."
                )
                return
            refresh_spotify_route = active_changed and spotify.is_active
            if refresh_spotify_route and self._audio_input is not None:
                self._close_audio_input()
            if (
                self._audio_input is not None
                and self._monitor_audio_source is AudioSource.SPOTIFY
            ):
                return
            if self._audio_input is not None:
                self._close_audio_input()
            try:
                self._open_spotify_monitor(refresh_route=refresh_spotify_route)
            except (BluetoothCommandError, RuntimeError) as exc:
                self._monitor_audio_source = AudioSource.SPOTIFY
                self._active_audio_device_name = None
                self._audio_monitor_error = str(exc)
                self._audio_status = f"Spotify audio unavailable: {exc}"
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
profile.audio_config(microphone=True),
        )
        device_name = audio_input.device_name()
        saved_profile = self._audio_profiles.get(device_name)
        if saved_profile is not None and saved_profile != profile:
            audio_input.reconfigure(saved_profile.audio_config(microphone=True))
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

    def _open_spotify_monitor(self, *, refresh_route: bool = False) -> None:
        status = self._spotify.status()
        if not status.connected:
            raise RuntimeError("Spotify Soloist is not connected")
        if not status.logged_in:
            raise RuntimeError("Spotify is not paired; select LumiStripe in Spotify first")
        route_method_name = (
            "refresh_spotify_route" if refresh_route else "ensure_spotify_route"
        )
        ensure_route = getattr(self._bluetooth, route_method_name, None)
        if not callable(ensure_route):
            # Keep injected development backends compatible with the optional
            # route-refresh operation.
            ensure_route = getattr(self._bluetooth, "ensure_spotify_route", None)
        if not callable(ensure_route):
            raise BluetoothCommandError("the PipeWire Spotify route is unavailable")
        ensure_route()
        devices = list_input_device_details()
        restore_source: str | None = None
        try:
            selector = spotify_capture_device_selector(devices)
        except BluetoothCommandError:
            # pipewire-alsa commonly exposes only its shared ``pulse`` or
            # ``pipewire`` capture device to PortAudio. Route that device's
            # default source to the dedicated Spotify monitor, just as the
            # Bluetooth monitor does, instead of requiring a separate device
            # entry for every PipeWire node.
            selector = capture_device_selector(devices)
            restore_source = self._bluetooth.default_source()
            self._bluetooth.set_default_source(SPOTIFY_MONITOR)
        profile = self._audio_profiles.get(SPOTIFY_PROFILE_KEY, AudioTuningProfile())
        try:
            audio_input = self._audio_factory(selector, profile.audio_config())
        except Exception:
            if restore_source is not None:
                self._bluetooth.restore_default_source(restore_source)
            raise
        self._audio_input = audio_input
        self._audio_profile = profile
        self._monitor_audio_source = AudioSource.SPOTIFY
        self._active_audio_device_name = audio_input.device_name()
        self._active_bluetooth_address = None
        self._bluetooth_previous_default_source = restore_source
        self._hardware_gain = None
        self._apply_audio_profile(profile)
        self._audio_status = "Spotify: waiting for playback"
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
        if self._audio_calibration is not None:
            raise RuntimeCommandError("discard microphone calibration before changing audio settings")
        self._stop_audio_sync_calibration()
        candidate: AudioInput | None = None
        current = self._audio_input
        target_is_bluetooth = device_name == BLUETOOTH_PROFILE_KEY
        target_is_spotify = device_name == SPOTIFY_PROFILE_KEY
        current_name = current.device_name() if current is not None else None
        target_is_active_mic = (
            not target_is_bluetooth
            and not target_is_spotify
            and self._monitor_audio_source is AudioSource.MIC
            and current_name == device_name
        )
        active_bluetooth = (
            target_is_bluetooth
            and self._monitor_audio_source is AudioSource.BLUETOOTH
            and current is not None
        )
        active_spotify = (
            target_is_spotify
            and self._monitor_audio_source is AudioSource.SPOTIFY
            and current is not None
        )
        should_open_mic = (
            not target_is_bluetooth
            and not target_is_spotify
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
                candidate = self._audio_factory(device, profile.audio_config(microphone=True))
                device_name = candidate.device_name()
                target_is_active_mic = True

            next_profiles = dict(self._audio_profiles)
            next_profiles[device_name] = profile
            selected_device = (
                device_name
                if not target_is_bluetooth and not target_is_spotify
                else self._selected_audio_device or ""
            )
            with self._settings_io_lock:
                self._settings_store.save_audio(selected_device, next_profiles)

            if candidate is not None:
                self._audio_input = candidate
            elif active_bluetooth or active_spotify or target_is_active_mic:
                assert current is not None
                current.reconfigure(profile.audio_config(microphone=target_is_active_mic))

            self._audio_profiles = next_profiles
            if not target_is_bluetooth and not target_is_spotify:
                self._selected_audio_device = device_name
            applies_to_active_input = (
                candidate is not None
                or active_bluetooth
                or active_spotify
                or target_is_active_mic
            )
            if applies_to_active_input or (target_is_bluetooth and current is None):
                self._audio_profile = profile
                self._hardware_gain = (
                    None
                    if target_is_bluetooth or target_is_spotify
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
                        else "Spotify: waiting for playback"
                        if self._monitor_audio_source is AudioSource.SPOTIFY
                        else f"Input: {device_name}"
                    )
                    if self._audio_input is not None
                    else (
                        self._audio_status
                        if target_is_bluetooth or target_is_spotify
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

    def _start_audio_calibration(self, device: str, duration_seconds: float, two_step: bool = False) -> AudioCalibrationSessionResponse:
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
        elif device_name == SPOTIFY_PROFILE_KEY:
            if self._monitor_audio_source is not AudioSource.SPOTIFY:
                raise RuntimeCommandError(
                    "pair Spotify and select the active Spotify stream before calibrating"
                )
        elif self._audio_input.device_name() != device_name:
            raise RuntimeCommandError("select the active microphone before calibrating")
        if two_step and self._monitor_audio_source is not AudioSource.MIC:
            raise RuntimeCommandError("two-step calibration requires microphone input")
        session = _AudioCalibrationSession(uuid4().hex, device_name, time.monotonic(), duration_seconds, two_step=two_step)
        session.last_sequence = self._audio_frame.sequence
        self._audio_calibration = session
        return self._publish_audio_calibration(session.session_id)

    def _tick_audio_calibration(self) -> None:
        session = self._audio_calibration
        if session is None or session.error:
            return
        active_device = (
            BLUETOOTH_PROFILE_KEY if self._monitor_audio_source is AudioSource.BLUETOOTH
            else SPOTIFY_PROFILE_KEY if self._monitor_audio_source is AudioSource.SPOTIFY
            else self._audio_input.device_name() if self._audio_input is not None else None
        )
        if self._audio_input is None or active_device != session.device:
            session.error = "Audio device changed or disconnected. Discard and measure again."
            session.result = None
            return
        if session.result is not None or session.phase == "waiting_music":
            if session.two_step and not self._audio_frame.fresh:
                session.error = "Microphone input stopped. Discard and measure again."
                session.result = None
            return
        elapsed = time.monotonic() - session.started_at
        frame = self._audio_frame
        if frame.fresh and frame.sequence > session.last_sequence:
            session.last_sequence = frame.sequence
            if session.two_step:
                samples = session.quiet_samples if session.phase == "quiet" else session.music_samples
                samples.append(self._audio_health.processor)
            else:
                session.frames.append(frame)
                session.features.append(self._music_features)
        if elapsed < session.duration_seconds:
            return
        try:
            if session.two_step:
                samples = session.quiet_samples if session.phase == "quiet" else session.music_samples
                if len(samples) < 10 or not frame.fresh:
                    raise ValueError("Not enough fresh input; check the microphone and retry.")
                if session.phase == "quiet":
                    session.phase = "waiting_music"
                else:
                    session.result = complete_microphone_calibration(
                        session.quiet_samples, session.music_samples, duration=session.duration_seconds,
                    )
                    # Preserve the independent, manually tuned output floor.
                    session.result["recommended_noise_floor"] = self._audio_profile.noise_floor
            else:
                session.result = complete_audio_calibration(
                    session.frames, session.features, duration=session.duration_seconds,
                )
        except ValueError as exc:
            session.error = str(exc)

    def _begin_audio_calibration_music(self, session_id: str) -> AudioCalibrationSessionResponse:
        session = self._audio_calibration
        if session is None or session.session_id != session_id or session.phase != "waiting_music" or session.error:
            raise RuntimeCommandError("calibration is not waiting for music")
        if self._audio_input is None or self._audio_input.device_name() != session.device or not self._audio_frame.fresh:
            raise RuntimeCommandError("microphone input is unavailable; discard and measure again")
        session.phase = "music"
        session.started_at = time.monotonic()
        session.last_sequence = self._audio_frame.sequence
        return self._publish_audio_calibration(session_id)

    def _finish_audio_calibration(self, command: _AudioCalibrationFinishCommand) -> AudioSettingsResponse | AudioCalibrationSessionResponse:
        session = self._audio_calibration
        if session is None or session.session_id != command.session_id:
            raise RuntimeCommandError("audio calibration session not found")
        if not command.apply:
            self._audio_calibration = None
            with self._snapshot_lock:
                self._audio_calibration_snapshot = None
            return self.audio_settings()
        if session.result is None or session.error:
            raise RuntimeCommandError("audio calibration has no valid recommendation")
        if session.two_step and (self._audio_input is None or self._audio_input.device_name() != session.device or not self._audio_frame.fresh):
            raise RuntimeCommandError("microphone input changed; discard and measure again")
        result = session.result
        profile = self._audio_profiles.get(session.device, AudioTuningProfile())
        hardware_target = profile.hardware_gain_target
        # Hardware gain stays fixed: recommendations use measurements at this gain.
        result["recommended_hardware_gain"] = hardware_target
        next_profile = replace(
            profile,
            target_level=command.target_level if command.target_level is not None else float(result["recommended_target_level"]),
            noise_floor=command.noise_floor if command.noise_floor is not None else float(result["recommended_noise_floor"]),
            hardware_gain_target=hardware_target,
            input_noise_floor=float(result["recommended_input_noise_floor"]) if session.two_step else profile.input_noise_floor,
        )
        self._audio_calibration = None
        try:
            self._apply_audio_settings(session.device, next_profile)
        except Exception:
            self._audio_calibration = session
            raise
        return self.audio_settings()

    def _select_audio_device(self, device: str) -> None:
        if device == "bluetooth":
            self._set_audio_source("bluetooth")
            return
        if device == "spotify":
            self._set_audio_source("spotify")
            return
        device_name = self._device_name_for_selector(device)
        profile = self._audio_profiles.get(device_name, AudioTuningProfile())
        self._apply_audio_settings(device, profile)

    def _set_audio_source(self, source: str) -> None:
        if self._audio_calibration is not None:
            raise RuntimeCommandError("discard microphone calibration before changing audio source")
        self._stop_audio_sync_calibration()
        if source not in {"auto", "off", "demo", "mic", "bluetooth", "spotify"}:
            raise RuntimeCommandError(f"invalid audio source: {source}")
        if self.playback.mode is PlaybackMode.DYNAMIC and source == "off":
            raise RuntimeCommandError("dynamic mode requires demo or microphone audio")
        self._audio_source_setting = source
        self._spotify_last_active = None
        if source == "auto":
            self._configured_audio_source = (
                AudioSource.BLUETOOTH if self.settings.hardware else AudioSource.DEMO
            )
        else:
            self._configured_audio_source = AudioSource(source)
        self._close_audio_input()
        self.playback.beat_clock_tracker.reset()
        self.playback.layered_renderer.reset()
        for _, playback in self._playbacks.values():
            playback.beat_clock_tracker.reset()
            playback.layered_renderer.reset()
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

    def _audio_sync_route_key(self) -> str:
        source = self._monitor_audio_source.value
        input_route = (
            self._active_bluetooth_address
            or self._active_audio_device_name
            or self._selected_audio_device
            or source
        )
        output_route = self._bluetooth.status().default_sink or "default"
        return f"{source}|{input_route}|{output_route}"

    def _apply_sync_offset_for_route(self) -> None:
        route = self._audio_sync_route_key()
        offset_s = self._audio_sync_offsets_ms.get(route, 0.0) / 1000.0
        self.playback.set_sync_offset(offset_s)
        for _, playback in self._playbacks.values():
            playback.set_sync_offset(offset_s)

    def _set_audio_sync_offset(self, offset_ms: float) -> AudioSettingsResponse:
        if not -500.0 <= offset_ms <= 500.0:
            raise RuntimeCommandError("audio sync offset must be between -500 and 500 ms")
        route = self._audio_sync_route_key()
        previous = self._audio_sync_offsets_ms.get(route)
        self._audio_sync_offsets_ms[route] = offset_ms
        try:
            with self._settings_io_lock:
                self._settings_store.save_audio_sync_offset(route, offset_ms)
        except OSError as exc:
            if previous is None:
                self._audio_sync_offsets_ms.pop(route, None)
            else:
                self._audio_sync_offsets_ms[route] = previous
            raise RuntimeCommandError(f"could not save audio sync offset: {exc}") from exc
        self._apply_sync_offset_for_route()
        return self.audio_settings()

    def _set_sequencer_settings(
        self, switch_quantization: SwitchQuantization, bars_per_phrase: int
    ) -> SequencerSettingsResponse:
        if not isinstance(switch_quantization, SwitchQuantization):
            raise RuntimeCommandError("unsupported animation switch timing")
        if (
            isinstance(bars_per_phrase, bool)
            or not isinstance(bars_per_phrase, int)
            or bars_per_phrase not in {4, 8, 16}
        ):
            raise RuntimeCommandError("bars per phrase must be 4, 8, or 16")
        bars_per_phrase = cast(Literal[4, 8, 16], bars_per_phrase)
        if (
            switch_quantization is self._switch_quantization
            and bars_per_phrase == self._bars_per_phrase
        ):
            return SequencerSettingsResponse(
                switch_quantization=self._switch_quantization,
                bars_per_phrase=self._bars_per_phrase,
            )

        try:
            with self._settings_io_lock:
                self._settings_store.save_sequencer_settings(
                    switch_quantization, bars_per_phrase
                )
        except OSError as exc:
            raise RuntimeCommandError(
                f"could not save sequencer settings: {exc}"
            ) from exc

        self._switch_quantization = switch_quantization
        self._bars_per_phrase = bars_per_phrase
        timeline = MusicalTimelineConfig(bars_per_phrase=bars_per_phrase)
        engines = (self.playback, *(engine for _, engine in self._playbacks.values()))
        for engine in engines:
            engine.set_sequencer_config(switch_quantization, timeline)
        return SequencerSettingsResponse(
            switch_quantization=switch_quantization,
            bars_per_phrase=bars_per_phrase,
        )

    def _set_transition_settings(
        self,
        enabled: bool,
        duration_ms: int,
        effect: TransitionEffect | None = None,
    ) -> TransitionSettingsResponse:
        if not isinstance(enabled, bool):
            raise RuntimeCommandError("transition enabled must be a boolean")
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or not 100 <= duration_ms <= 1500
        ):
            raise RuntimeCommandError("transition duration must be between 100 and 1500 ms")
        selected_effect = self._transition_settings.effect if effect is None else effect
        if not isinstance(selected_effect, TransitionEffect):
            raise RuntimeCommandError("transition effect is invalid")
        updated = TransitionSettings(
            enabled=enabled, duration_ms=duration_ms, effect=selected_effect
        )
        if updated == self._transition_settings:
            return TransitionSettingsResponse(
                enabled=updated.enabled,
                duration_ms=updated.duration_ms,
                effect=updated.effect,
            )

        try:
            with self._settings_io_lock:
                self._settings_store.save_transition_settings(updated)
        except OSError as exc:
            raise RuntimeCommandError(
                f"could not save transition settings: {exc}"
            ) from exc

        self._transition_settings = updated
        duration_s = updated.duration_ms / 1000.0 if updated.enabled else 0.0
        for engine in (self.playback, *(item for _, item in self._playbacks.values())):
            engine.set_transition_duration(duration_s)
            engine.set_transition_effect(updated.effect)
        return TransitionSettingsResponse(
            enabled=updated.enabled,
            duration_ms=updated.duration_ms,
            effect=updated.effect,
        )

    def _set_sequence_library(self, command: _SequenceLibraryCommand) -> None:
        sequences = list(self._animation_sequences)
        sequence_id = command.sequence_id
        selected_id = self._active_sequence_id

        if command.action == "create":
            sequence = command.sequence
            if sequence is None or sequence_id is None or sequence.id != sequence_id:
                raise InvalidSequenceError("create requires a matching sequence ID")
            if any(item.id == sequence_id for item in sequences):
                raise SequenceConflictError(f"sequence already exists: {sequence_id}")
            if len(sequences) >= MAX_SAVED_ANIMATION_SEQUENCES:
                raise SequenceConflictError(
                    f"at most {MAX_SAVED_ANIMATION_SEQUENCES} sequences may be saved"
                )
            self._validate_sequence_for_outputs(sequence)
            sequences.append(sequence)
        elif command.action == "update":
            sequence = command.sequence
            if sequence_id is None or sequence is None or sequence.id != sequence_id:
                raise InvalidSequenceError("update requires a matching sequence ID")
            index = next(
                (i for i, item in enumerate(sequences) if item.id == sequence_id),
                None,
            )
            if index is None:
                raise UnknownSequenceError(f"unknown sequence: {sequence_id}")
            self._validate_sequence_for_outputs(sequence)
            sequences[index] = sequence
        elif command.action == "delete":
            if sequence_id is None:
                raise UnknownSequenceError("sequence ID is required")
            remaining = [item for item in sequences if item.id != sequence_id]
            if len(remaining) == len(sequences):
                raise UnknownSequenceError(f"unknown sequence: {sequence_id}")
            sequences = remaining
            if selected_id == sequence_id:
                selected_id = None
        elif command.action == "activate":
            if sequence_id is not None and not any(
                item.id == sequence_id for item in sequences
            ):
                raise UnknownSequenceError(f"unknown sequence: {sequence_id}")
            selected_id = sequence_id
        else:
            raise RuntimeCommandError("unknown sequence library operation")

        updated_sequences = tuple(sequences)
        next_active = next(
            (item for item in updated_sequences if item.id == selected_id), None
        )
        if next_active is not None:
            self._validate_sequence_for_outputs(next_active)

        try:
            with self._settings_io_lock:
                self._settings_store.save_animation_sequences(
                    updated_sequences, selected_id
                )
        except Exception as exc:
            raise SequencePersistenceError(
                f"could not save animation sequences: {exc}"
            ) from exc

        if next_active != self._active_animation_sequence:
            for engine in self._sequence_engines():
                engine.set_animation_sequence(next_active)
        self._animation_sequences = updated_sequences
        self._active_sequence_id = selected_id
        self._active_animation_sequence = next_active

    def _set_animation_settings(self, command: _AnimationSettingsCommand) -> None:
        definition = next(
            (
                item
                for item in animation_catalog(self.player)
                if item.name == command.animation_name
            ),
            None,
        )
        if definition is None:
            raise UnknownAnimationError(
                f"unknown animation: {command.animation_name}"
            )
        if not definition.settings:
            raise InvalidAnimationSettingsError(
                f"animation {command.animation_name} has no configurable settings"
            )
        try:
            values = definition.normalize_settings(
                None if command.reset else command.values,
                allow_partial=command.reset,
            )
        except (TypeError, ValueError) as exc:
            raise InvalidAnimationSettingsError(str(exc)) from exc

        stripe_id = command.stripe_id
        if stripe_id is not None:
            if self._topology.layout != "independent":
                raise InvalidAnimationSettingsError(
                    "a stripe target is only available in independent layout"
                )
            if stripe_id not in {output.id for output in self._topology.outputs}:
                raise InvalidAnimationSettingsError(f"unknown stripe: {stripe_id}")
            targets = self._target_playbacks(stripe_id)
        else:
            targets = self._target_playbacks(None)

        updated = {
            scope: {name: dict(saved) for name, saved in animations.items()}
            for scope, animations in self._animation_settings.items()
        }
        if stripe_id is None:
            shared = updated.setdefault(SHARED_ANIMATION_SETTINGS_KEY, {})
            shared[command.animation_name] = dict(values)
            for scope, animations in updated.items():
                if scope != SHARED_ANIMATION_SETTINGS_KEY:
                    animations.pop(command.animation_name, None)
            updated = {
                scope: animations
                for scope, animations in updated.items()
                if animations or scope == SHARED_ANIMATION_SETTINGS_KEY
            }
        else:
            updated.setdefault(stripe_id, {})[command.animation_name] = dict(values)

        try:
            with self._settings_io_lock:
                self._settings_store.save_animation_settings(updated)
        except Exception as exc:
            raise AnimationSettingsPersistenceError(
                f"could not save animation settings: {exc}"
            ) from exc

        self._animation_settings = updated
        players = (
            tuple(player for player, _ in targets)
            if stripe_id is not None
            else (self.player, *(player for player, _ in targets))
        )
        for player in players:
            player.set_animation_settings(command.animation_name, values)

    def _apply_saved_animation_settings(
        self, player: AnimationPlayer, scope: str
    ) -> None:
        shared = self._animation_settings.get(SHARED_ANIMATION_SETTINGS_KEY, {})
        overrides = (
            {} if scope == SHARED_ANIMATION_SETTINGS_KEY else self._animation_settings.get(scope, {})
        )
        for definition in animation_catalog(player):
            if not definition.settings:
                continue
            values = overrides.get(definition.name, shared.get(definition.name))
            if values is not None:
                player.set_animation_settings(definition.name, values)

    def _build_animation_settings_snapshot(
        self,
    ) -> dict[tuple[str, str | None], AnimationSettingsResponse]:
        snapshot: dict[tuple[str, str | None], AnimationSettingsResponse] = {}
        shared = self._animation_settings.get(SHARED_ANIMATION_SETTINGS_KEY, {})
        for definition in animation_catalog(self.player):
            if not definition.settings:
                continue
            shared_values = definition.normalize_settings(
                shared.get(definition.name), allow_partial=True
            )
            snapshot[(definition.name, None)] = self._animation_settings_response(
                definition, shared_values, None
            )
            for output in self._topology.outputs:
                saved_values = self._animation_settings.get(output.id, {}).get(
                    definition.name, shared_values
                )
                values = definition.normalize_settings(
                    saved_values, allow_partial=True
                )
                snapshot[(definition.name, output.id)] = (
                    self._animation_settings_response(
                        definition, values, output.id
                    )
                )
        return snapshot

    @staticmethod
    def _animation_settings_response(
        definition: AnimationDefinition,
        values: Mapping[str, str | int | float | bool],
        stripe_id: str | None,
    ) -> AnimationSettingsResponse:
        return AnimationSettingsResponse(
            animation=definition.name,
            stripe_id=stripe_id,
            options=tuple(
                AnimationSettingOption(
                    id=option.id,
                    label=option.label,
                    type=option.type.value,
                    default=option.default,
                    description=option.description,
                    minimum=option.minimum,
                    maximum=option.maximum,
                    step=option.step,
                    unit=option.unit,
                    choices=option.choices,
                    visible_when_option=option.visible_when_option,
                    visible_when_value=option.visible_when_value,
                )
                for option in definition.settings
            ),
            values=dict(values),
        )

    def _sequence_engines(self) -> tuple[PlaybackEngine, ...]:
        return (self.playback, *(engine for _, engine in self._playbacks.values()))

    def _validate_sequence_for_outputs(self, sequence: AnimationSequence) -> None:
        try:
            for engine in self._sequence_engines():
                engine.validate_animation_sequence(sequence)
        except (TypeError, ValueError) as exc:
            raise InvalidSequenceError(str(exc)) from exc

    def _set_audio_sync_calibration(
        self, active: bool
    ) -> AudioSyncCalibrationResponse:
        route = self._audio_sync_route_key()
        offset_ms = self._audio_sync_offsets_ms.get(route, 0.0)
        if active:
            self._stop_audio_sync_calibration()
            expected_sink = self._bluetooth.status().default_sink
            capture_input_ids = getattr(
                self._bluetooth, "calibration_output_input_ids", None
            )
            verify_output_route = getattr(
                self._bluetooth, "verify_calibration_output_route", None
            )
            if not expected_sink or not callable(capture_input_ids) or not callable(
                verify_output_route
            ):
                raise RuntimeCommandError(
                    "could not verify the active PipeWire output for calibration"
                )
            try:
                before_input_ids = capture_input_ids()
                player = AudioSyncCalibrationTone()
                try:
                    verify_output_route(before_input_ids, expected_sink)
                    player.arm()
                except RuntimeError:
                    player.close()
                    raise
            except RuntimeError as exc:
                raise RuntimeCommandError(str(exc)) from exc
            for _, playback in self._target_playbacks(None):
                playback.cancel_transition()
            self._sync_calibration_player = player
            self._sync_calibration_start_s = player.start_at_monotonic
            self._sync_calibration_until_s = player.start_at_monotonic + 45.0
            self._sync_calibration_route = route
        else:
            self._stop_audio_sync_calibration()
        return AudioSyncCalibrationResponse(
            # Retained in the REST response for cached clients. The calibration
            # clock now lives entirely on the Pi and does not use epoch time.
            start_at_ms=None,
            sync_route=route,
            sync_offset_ms=offset_ms,
        )

    def _render_sync_calibration_flash(self, now_s: float) -> None:
        assert self._controller is not None
        assert self._sync_calibration_start_s is not None
        if sync_calibration_led_on(
            now_s,
            self._sync_calibration_start_s,
            self.playback.sync_offset_s,
        ):
            self._controller.fill(Rgb(255, 255, 255))
        else:
            self._controller.clear()
        self._flush_power_limited_outputs()

    def _stop_audio_sync_calibration(self) -> None:
        player = self._sync_calibration_player
        self._sync_calibration_player = None
        self._sync_calibration_start_s = None
        self._sync_calibration_until_s = None
        self._sync_calibration_route = None
        if player is not None:
            try:
                player.close()
            except Exception as exc:  # noqa: BLE001 - calibration stop should not block cleanup
                self._fatal_error = self._fatal_error or str(exc)
        self.playback.beat_clock_tracker.reset()

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
        if selector == "spotify":
            return SPOTIFY_PROFILE_KEY
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
        try:
            decision = self._playback_policy.decide(
                mode,
                self._configured_audio_source,
                audio_input_available=self._audio_input is not None,
                unavailable_message=self._audio_monitor_error,
            )
        except PlaybackPolicyError as exc:
            raise RuntimeCommandError(str(exc)) from exc
        next_source = decision.audio_source

        self._active_audio_source = next_source
        self._demo_tick = 0
        self._audio_frame = AudioFrame()
        self._music_features = MusicFeatures()
        if self._audio_input is not None:
            self._audio_status = (
                f"Bluetooth: {self._active_audio_device_name}"
                if self._monitor_audio_source is AudioSource.BLUETOOTH
                else "Spotify: waiting for playback"
                if self._monitor_audio_source is AudioSource.SPOTIFY
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
        for _, playback in self._target_playbacks(None):
            playback.cancel_transition()
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
        pattern = calibration_color(session.pattern)
        for index, controller in enumerate(self._correction_controllers):
            if index == session.output_index:
                controller.fill(pattern)
            else:
                controller.clear()
            controller.force_flush()

    def _step(self) -> float:
        assert self._controller is not None
        self._maintain_audio_monitor()
        self._apply_sync_offset_for_route()
        if (
            self._sync_calibration_start_s is not None
            and self._sync_calibration_route != self._audio_sync_route_key()
        ):
            self._stop_audio_sync_calibration()
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
            read_events = getattr(self._audio_input, "read_beat_events", None)
            beat_events = read_events() if callable(read_events) else ()
            beat_event_stream = callable(read_events)
            if beat_events:
                self._last_beat_event = beat_events[-1]
            self._audio_health = self._audio_input.health()
            self._music_features = (
                self._audio_input.read_features()
                if self._audio_frame.fresh or beat_events
                else MusicFeatures()
            )
            snapshot = (
                AudioSnapshot.from_parts(
                    self._audio_frame,
                    self._music_features,
                    self._audio_health,
                    beat_events=beat_events,
                    beat_event_stream=beat_event_stream,
                )
                if self._audio_frame.fresh or beat_events
                else AudioSnapshot.silence(
                    frame=self._audio_frame,
                    health=self._audio_health,
                    beat_event_stream=beat_event_stream,
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

        if self._sync_calibration_start_s is not None:
            now = time.monotonic()
            if self._sync_calibration_until_s is not None and now >= self._sync_calibration_until_s:
                self._stop_audio_sync_calibration()
            else:
                self._render_sync_calibration_flash(now)
                self._update_audio_telemetry()
                self._record_frame()
                self._publish(running=True)
                return MIN_FRAME_SECONDS

        self._tick_audio_calibration()
        if self._topology.layout == "independent" and self._output_gates:
            delays = [
                self._playbacks[output.id][1].step(
                    gate, snapshot=snapshot, flush=False
                )
                for output, gate in zip(
                    self._topology.outputs, self._output_gates, strict=True
                )
            ]
            delay = min(delays)
        else:
            delay = self.playback.step(
                self._controller, snapshot=snapshot, flush=False
            )
        self._flush_power_limited_outputs()
        self._update_audio_telemetry()
        self._record_frame()
        self._publish(running=True)
        return max(delay, MIN_FRAME_SECONDS)

    def _flush_power_limited_outputs(self) -> None:
        """Estimate the rendered frame, apply one cap, and flush each output once."""
        gates = self._output_gates
        if not gates:
            assert self._controller is not None
            self._controller.flush()
            self._power_budget_state = _power_budget_state_for(self._topology)
            self._power_preview_override = None
            return

        shared_blackout = (
            self._topology.layout != "independent"
            and self._controller is not None
            and self._controller.blackout
        )
        raw = self._raw_controller
        original_frames = [gate.pixels().copy() for gate in gates]
        if self._topology.layout == "mirrored" and isinstance(raw, ScaledMultiController):
            mapped_frames = raw.output_pixels()
            for gate, frame in zip(gates, mapped_frames, strict=True):
                gate.set_pixels(frame)
            original_frames = [gate.pixels().copy() for gate in gates]

        estimates: list[OutputPowerEstimate] = []
        corrected_frames: list[np.ndarray] = []
        for index, (output, gate) in enumerate(
            zip(self._topology.outputs, gates, strict=True)
        ):
            frame = gate.pixels().copy()
            corrected = frame.copy()
            if index < len(self._correction_controllers):
                correction = self._correction_controllers[index].correction
                channels = np.array(
                    (correction.red, correction.green, correction.blue),
                    dtype=np.uint16,
                )
                corrected[:, :3] = (
                    corrected[:, :3].astype(np.uint16) * channels // 255
                ).astype(np.uint8)
            corrected_frames.append(corrected)
            watts = 0.0
            if not shared_blackout and not gate.blackout:
                watts = estimate_frame_power(
                    corrected,
                    voltage_v=output.voltage_v,
                    full_white_current_a=output.full_white_current_a,
                )
            estimates.append(
                OutputPowerEstimate(
                    output_id=output.id,
                    watts=watts,
                    limit_watts=output.power_limit_watts,
                )
            )

        result = apply_power_budget(
            estimates,
            enabled=self._topology.power_limiting_enabled and not shared_blackout,
            budget_watts=self._topology.effective_power_budget_watts,
        )
        self._power_budget_state = _power_budget_state_for(
            self._topology,
            estimated_watts=result.estimated_watts,
            applied_scale=result.applied_scale,
            limiting_output_id=result.limiting_output_id,
            outputs=tuple(
                PowerOutputState(
                    output_id=estimate.output_id,
                    estimated_watts=estimate.watts,
                    limit_watts=estimate.limit_watts,
                    applied_scale=result.applied_scale,
                )
                for estimate in result.outputs
            ),
        )

        scaled_frames: list[np.ndarray] = []
        try:
            for index, (gate, frame) in enumerate(zip(gates, original_frames, strict=True)):
                scaled = frame.copy()
                if not shared_blackout and not gate.blackout:
                    scaled[:, 3] = np.floor(
                        scaled[:, 3].astype(np.float64) * result.applied_scale
                    ).astype(np.uint8)
                    gate.set_pixels(scaled)
                    gate.flush()
                    physical = corrected_frames[index].copy()
                    physical[:, 3] = scaled[:, 3]
                    scaled_frames.append(physical)
                else:
                    scaled_frames.append(np.zeros_like(frame))
        finally:
            for gate, frame in zip(gates, original_frames, strict=True):
                gate.set_pixels(frame)

        if shared_blackout:
            self._power_preview_override = tuple(
                bytes(output.pixels * 4) for output in self._topology.outputs
            )
        else:
            self._power_preview_override = tuple(
                self._preview_bytes(frame, index)
                for index, frame in enumerate(scaled_frames)
            )

    def _preview_bytes(self, pixels: np.ndarray, index: int) -> bytes:
        frame = pixels.copy()
        if index < len(self._topology.outputs) and self._topology.outputs[index].reversed:
            frame = frame[::-1]
        return frame.tobytes()

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
        clock = self.playback.beat_clock_state
        dynamic_playbacks = tuple(
            playback
            for _, playback in self._target_playbacks(None)
            if playback.mode is PlaybackMode.DYNAMIC
        )
        structure_engine = next(
            (playback for playback in dynamic_playbacks if playback.music_active),
            dynamic_playbacks[0] if dynamic_playbacks else self.playback,
        )
        scheduler_diagnostics = structure_engine.layered_renderer.diagnostics()
        structure_state = structure_engine.music_structure_state
        drop_accent = scheduler_diagnostics.drop_accent
        calibration_output_diagnostics = (
            self._sync_calibration_player.diagnostics()
            if self._sync_calibration_player is not None
            else None
        )
        sync_route = self._audio_sync_route_key()
        sync_offset_ms = self._audio_sync_offsets_ms.get(sync_route, 0.0)
        next_beat_in_ms = (
            None
            if clock.next_beat_at is None
            else max(0.0, (clock.next_beat_at - time.monotonic()) * 1000.0)
        )
        telemetry = AudioTelemetry(
            sequence=self._audio_frame.sequence,
            fresh=self._audio_frame.fresh,
            input_level=min(1.0, max(0.0, processor.input_rms)),
            input_peak=processor.input_peak,
            clipping_fraction=processor.clipping_fraction,
            microphone_gate_open=processor.microphone_gate_open,
            configured_input_noise_floor=self._audio_profile.input_noise_floor if self._monitor_audio_source is AudioSource.MIC else None,
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
            bpm=max(0.0, clock.bpm if clock.confidence > 0.0 else self._music_features.bpm),
            bpm_confidence=clock.confidence,
            beat_phase=clock.phase,
            beat_index=clock.beat_index,
            next_beat_in_ms=next_beat_in_ms,
            beat_event_sequence=(
                self._last_beat_event.sequence if self._last_beat_event is not None else 0
            ),
            dropped_beat_events=self._audio_health.dropped_beat_events,
            pending_beat_events=self._audio_health.pending_beat_events,
            beat_scheduler_late_ticks_dropped=(
                scheduler_diagnostics.late_ticks_dropped
            ),
            beat_scheduler_late_ticks_coalesced=(
                scheduler_diagnostics.late_ticks_coalesced
            ),
            beat_scheduler_lateness_p95_ms=(
                scheduler_diagnostics.beat_lateness_p95_ms
            ),
            calibration_output_callback_count=(
                calibration_output_diagnostics.callback_count
                if calibration_output_diagnostics is not None
                else 0
            ),
            calibration_output_sample_rate=(
                calibration_output_diagnostics.sample_rate
                if calibration_output_diagnostics is not None
                else 0.0
            ),
            calibration_output_block_duration_ms=(
                calibration_output_diagnostics.block_duration_ms
                if calibration_output_diagnostics is not None
                else 0.0
            ),
            calibration_output_callback_p50_ms=(
                calibration_output_diagnostics.callback_p50_ms
                if calibration_output_diagnostics is not None
                else 0.0
            ),
            calibration_output_callback_p95_ms=(
                calibration_output_diagnostics.callback_p95_ms
                if calibration_output_diagnostics is not None
                else 0.0
            ),
            calibration_output_callback_p99_ms=(
                calibration_output_diagnostics.callback_p99_ms
                if calibration_output_diagnostics is not None
                else 0.0
            ),
            calibration_output_interval_jitter_p95_ms=(
                calibration_output_diagnostics.interval_jitter_p95_ms
                if calibration_output_diagnostics is not None
                else 0.0
            ),
            calibration_output_underflow_count=(
                calibration_output_diagnostics.underflow_count
                if calibration_output_diagnostics is not None
                else 0
            ),
            calibration_output_overflow_count=(
                calibration_output_diagnostics.overflow_count
                if calibration_output_diagnostics is not None
                else 0
            ),
            calibration_output_callback_error_count=(
                calibration_output_diagnostics.callback_error_count
                if calibration_output_diagnostics is not None
                else 0
            ),
            sync_offset_ms=sync_offset_ms,
            sync_route=sync_route,
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
            music_structure_phase=structure_state.phase.value,
            music_structure_confidence=structure_state.confidence,
            music_structure_buildup_progress=structure_state.buildup_progress,
            music_structure_drop_count=structure_engine.confirmed_drop_count,
            music_structure_active=(
                structure_engine.mode is PlaybackMode.DYNAMIC
                and structure_engine.music_active
            ),
            music_structure_fresh=(
                structure_engine.mode is PlaybackMode.DYNAMIC
                and structure_engine.music_active
                and self._audio_frame.fresh
            ),
            drop_accent_status=drop_accent.status.value,
            drop_accent_reason=drop_accent.reason,
            health=self._audio_health_status(self._uptime_seconds()),
        )
        with self._snapshot_lock:
            self._audio_telemetry = telemetry

    def _worker_heartbeat_age(self) -> float | None:
        with self._worker_state_lock:
            completed_at = self._last_step_completed_s
        if completed_at is None:
            return None
        return max(0.0, time.monotonic() - completed_at)

    def _command_queue_depth(self) -> int:
        with self._command_lock:
            return self._commands.qsize()

    def _publish(
        self,
        *,
        running: bool,
        error: str | None = None,
    ) -> DashboardState:
        self._capture_preview_frame()
        if self._audio_calibration is not None:
            self._publish_audio_calibration(self._audio_calibration.session_id)
        else:
            with self._snapshot_lock:
                self._audio_calibration_snapshot = None
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
            configured_audio_source=self._audio_source_setting,
            active_audio_source=self._monitor_audio_source.value,
            audio_monitoring=self._audio_input is not None,
            active_effects=self.playback.active_effect_names,
            uptime_seconds=uptime_seconds,
            frame_rate=self._frame_rate,
            render_time_ms=self._last_render_time_ms,
            worker_heartbeat_age_seconds=self._worker_heartbeat_age(),
            missed_frame_count=self._missed_frame_count,
            recent_frame_miss_rate=self._frame_timing.stats(
                time.monotonic()
            ).miss_rate,
            command_queue_depth=self._command_queue_depth(),
            audio_health=self._audio_health_status(uptime_seconds),
            audio_callback_age_seconds=self._audio_health.last_callback_age,
            audio_frame_age_seconds=self._audio_health.last_frame_age,
            last_output_at=(self._last_output_at()),
            last_output_age_seconds=(self._last_output_age()),
            application_version=APPLICATION_VERSION,
            color_corrections=self._color_correction_profiles(),
            calibration=self._calibration_status(),
            stripe_topology=self._stripe_topology_state(),
            power_budget=self._power_budget_state,
            sequencer_settings=SequencerSettingsResponse(
                switch_quantization=self._switch_quantization,
                bars_per_phrase=self._bars_per_phrase,
            ),
            transition_settings=TransitionSettingsResponse(
                enabled=self._transition_settings.enabled,
                duration_ms=self._transition_settings.duration_ms,
                effect=self._transition_settings.effect,
            ),
            sequence_library=self._sequence_library_summary(),
            stripe_playback=self._stripe_playback_state(),
            diagnostic_issues=self._diagnostic_issues(
                running=running,
                uptime_seconds=uptime_seconds,
            ),
            error=error,
        )
        with self._snapshot_lock:
            self._snapshot = state
            self._sequence_library_snapshot = self._sequence_library_response(
                self._animation_sequences, self._active_sequence_id
            )
            self._animation_settings_snapshot = (
                self._build_animation_settings_snapshot()
            )
        return state

    def _capture_preview_frame(self) -> None:
        """Copy output pixels before publishing a frame to WebSocket clients.

        The animation thread owns the live NumPy buffers. Keeping immutable byte
        copies here lets the async API serve a frame without racing a render or
        holding the runtime thread while a network client is slow.
        """
        if self._power_preview_override is not None:
            override = self._power_preview_override
            self._power_preview_override = None
            with self._preview_lock:
                self._preview_sequence += 1
                self._preview_outputs = override
            return
        outputs: list[bytes] = []
        gates = self._output_gates
        if gates:
            for index, gate in enumerate(gates):
                if gate.blackout or (
                    self._topology.layout != "independent"
                    and self._controller is not None
                    and self._controller.blackout
                ):
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
            power_budget_enabled=self._topology.power_budget_enabled,
            power_budget_watts=self._topology.power_budget_watts,
            shared_supply_enabled=self._topology.shared_supply_enabled,
            supply_capacity_watts=self._topology.supply_capacity_watts,
            pi_reserve_watts=self._topology.pi_reserve_watts,
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
                    voltage_v=output.voltage_v,
                    full_white_current_a=output.full_white_current_a,
                    power_limit_watts=output.power_limit_watts,
                    last_output_at=(
                        self._output_gates[index].last_successful_update_at
                        if index < len(self._output_gates)
                        else None
                    ),
                )
                for index, output in enumerate(self._topology.outputs)
            ),
        )

    @staticmethod
    def _sequence_library_response(
        sequences: tuple[AnimationSequence, ...], active_sequence_id: str | None
    ) -> SequenceLibraryResponse:
        return SequenceLibraryResponse(
            sequences=tuple(
                AnimationSequenceContract.from_core(sequence)
                for sequence in sequences
            ),
            active_sequence_id=active_sequence_id,
        )

    def _sequence_library_summary(self) -> SequenceLibrarySummary:
        return SequenceLibrarySummary(
            sequences=tuple(
                SequenceLibraryEntry(id=sequence.id, name=sequence.name)
                for sequence in self._animation_sequences
            ),
            active_sequence_id=self._active_sequence_id,
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
                    sequencer_diagnostics=self._sequencer_diagnostics(playback),
                    sequence_playback=self._sequence_playback_diagnostics(playback),
                )
            )
        return tuple(states)

    @staticmethod
    def _sequencer_diagnostics(playback: PlaybackEngine) -> SequencerDiagnostics:
        diagnostics: QuantizedSwitchDiagnostics = playback.quantized_switch_diagnostics()
        return SequencerDiagnostics(
            mode=diagnostics.mode,
            beats_per_bar=diagnostics.beats_per_bar,
            bars_per_phrase=diagnostics.bars_per_phrase,
            beat_index=diagnostics.beat_index,
            beat_in_bar=diagnostics.beat_in_bar,
            bar_index=diagnostics.bar_index,
            bar_in_phrase=diagnostics.bar_in_phrase,
            phrase_index=diagnostics.phrase_index,
            beat_in_phrase=diagnostics.beat_in_phrase,
            bpm=diagnostics.bpm,
            beat_clock_confidence=diagnostics.beat_clock_confidence,
            alignment_estimated=diagnostics.alignment_estimated,
            pending_animation=diagnostics.pending_animation,
            next_switch_in_s=diagnostics.next_switch_in_s,
            waiting_for_clock=diagnostics.waiting_for_clock,
        )

    def _sequence_playback_diagnostics(
        self, playback: PlaybackEngine
    ) -> SequencePlaybackDiagnostics:
        sequence_state = playback.sequence_playback_state
        next_transition_in_s = (
            None
            if sequence_state.next_transition_at_s is None
            else max(0.0, sequence_state.next_transition_at_s - time.monotonic())
        )
        return SequencePlaybackDiagnostics(
            selected_sequence_id=self._active_sequence_id,
            active=sequence_state.active,
            current_step_index=sequence_state.current_step_index,
            animation_name=sequence_state.animation_name,
            remaining_bars=sequence_state.remaining_bars,
            waiting_for_clock=sequence_state.waiting_for_clock,
            next_transition_in_s=next_transition_in_s,
        )

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

        power = self._power_budget_state
        if power.enabled and power.applied_scale < 0.999999:
            limiting = (
                next(
                    (
                        output.name
                        for output in self._topology.outputs
                        if output.id == power.limiting_output_id
                    ),
                    "Global budget",
                )
                if power.limiting_output_id is not None
                else "Global budget"
            )
            cap = power.budget_watts or 0.0
            if power.limiting_output_id is not None:
                cap = next(
                    (
                        output.power_limit_watts
                        for output in self._topology.outputs
                        if output.id == power.limiting_output_id
                        and output.power_limit_watts is not None
                    ),
                    cap,
                )
            issues.append(
                DiagnosticIssue(
                    severity="warning",
                    title="Power budget is limiting brightness",
                    message=(
                        f"The rendered frame is estimated at {power.estimated_watts:.1f} W; "
                        f"{limiting} limited it to {cap:.1f} W "
                        f"({power.applied_scale * 100:.0f}% brightness)."
                    ),
                    action="Lower the effect brightness or update the power budget and electrical calibration.",
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
        queue_depth = self._command_queue_depth()
        if running and queue_depth > 8:
            issues.append(
                DiagnosticIssue(
                    severity="warning",
                    title="Dashboard command queue is backed up",
                    message=f"{queue_depth} lighting commands are waiting to be applied.",
                    action="Wait for the current command burst to finish or reduce repeated control updates.",
                )
            )
        frame_timing = self._frame_timing.stats(time.monotonic())
        if (
            running
            and uptime_seconds > WORKER_STARTUP_GRACE_SECONDS
            and frame_timing.missed_frame_count >= FRAME_TIMING_MIN_MISSES
            and frame_timing.miss_rate >= FRAME_TIMING_MISS_RATE_THRESHOLD
        ):
            issues.append(
                DiagnosticIssue(
                    severity="warning",
                    title="Renderer missed frame deadlines",
                    message=(
                        f"{frame_timing.missed_frame_count} of the last "
                        f"{frame_timing.frame_count} frames missed their deadlines "
                        f"({frame_timing.miss_rate * 100:.0f}%). The latest render "
                        f"took {self._last_render_time_ms:.1f} ms; "
                        f"{self._missed_frame_count} misses have been recorded since startup."
                    ),
                    action="Check CPU load, hardware output latency, and animation complexity.",
                )
            )
        return tuple(issues)

    def _cleanup(self) -> None:
        self._cancel_startup_persist_timer()
        self._stop_audio_sync_calibration()
        if self._restore_last_state:
            self._flush_startup_settings()
        try:
            self._close_audio_input()
        except Exception as exc:  # noqa: BLE001 - cleanup continues after individual failures
            self._fatal_error = self._fatal_error or str(exc)
        self._bluetooth.stop()
        self._spotify.stop()
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
        with self._command_lock:
            self._coalesced_commands.clear()
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if command is not None:
                self._resolve_command(
                    command,
                    error=RuntimeUnavailableError(
                        self._fatal_error or "runtime stopped"
                    ),
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
