"""Public runtime façade backed by the single-writer worker."""

from __future__ import annotations

import sys
from collections.abc import Callable

from lumistripe import Controller
from lumistripe.audio.devices import list_input_device_details
from lumistripe.gpio import GPIOStripe
from lumistripe.gpio.spi import SPIStripe

from ..bluetooth import BluetoothAudioBackend
from ..spotify import SpotifyBackend
from . import worker as worker_module
from .audio import default_audio_factory
from .state import RuntimeSettings, _AudioFactory, _ControllerFactory
from .topology import default_controller_factory as topology_controller_factory
from .worker import (
    APPLICATION_VERSION,
    AUDIO_ROUTE_POLL_SECONDS,
    BLUETOOTH_PROFILE_KEY,
    CALIBRATION_FRAME_SECONDS,
    CALIBRATION_TIMEOUT_SECONDS,
    FRAME_STALL_GRACE_SECONDS,
    FRAME_STALL_MAX_SECONDS,
    FRAME_STALL_MIN_SECONDS,
    MIN_FRAME_SECONDS,
    SPOTIFY_PROFILE_KEY,
    WORKER_STARTUP_GRACE_SECONDS,
    WORKER_STOP_GRACE_SECONDS,
    WORKER_WATCHDOG_INTERVAL_SECONDS,
    PreviewFrame,
    RuntimeCommandError,
    RuntimeUnavailableError,
    RuntimeWorker,
    UnknownAnimationError,
)

_default_audio_factory = default_audio_factory


def _default_controller_factory(settings: RuntimeSettings) -> Controller:
    """Build the default controller using façade-level test seams."""

    compatibility_module = sys.modules.get("lumistripe_web.runtime")
    gpio_factory = getattr(compatibility_module, "GPIOStripe", GPIOStripe)
    spi_factory = getattr(compatibility_module, "SPIStripe", SPIStripe)
    return topology_controller_factory(
        settings,
        gpio_factory=gpio_factory,
        spi_factory=spi_factory,
    )


def _sync_worker_compatibility_hooks() -> None:
    """Copy legacy module-level injection points into the worker module.

    Older integrations and the web test suite patch these names on
    ``lumistripe_web.runtime``. Keeping the synchronization here preserves
    those seams while the implementation lives in ``worker.py``.
    """

    compatibility_module = sys.modules.get("lumistripe_web.runtime")
    for name in (
        "AUDIO_ROUTE_POLL_SECONDS",
        "BLUETOOTH_PROFILE_KEY",
        "SPOTIFY_PROFILE_KEY",
        "CALIBRATION_FRAME_SECONDS",
        "CALIBRATION_TIMEOUT_SECONDS",
        "FRAME_STALL_GRACE_SECONDS",
        "FRAME_STALL_MAX_SECONDS",
        "FRAME_STALL_MIN_SECONDS",
        "MIN_FRAME_SECONDS",
        "WORKER_STARTUP_GRACE_SECONDS",
        "WORKER_STOP_GRACE_SECONDS",
        "WORKER_WATCHDOG_INTERVAL_SECONDS",
    ):
        setattr(
            worker_module,
            name,
            getattr(compatibility_module, name, globals()[name]),
        )
    setattr(  # noqa: B010 - compatibility injection into the worker module
        worker_module,
        "GPIOStripe",
        getattr(compatibility_module, "GPIOStripe", GPIOStripe),
    )
    setattr(  # noqa: B010 - compatibility injection into the worker module
        worker_module,
        "SPIStripe",
        getattr(compatibility_module, "SPIStripe", SPIStripe),
    )
    setattr(  # noqa: B010 - compatibility injection into the worker module
        worker_module,
        "list_input_device_details",
        getattr(
            compatibility_module,
            "list_input_device_details",
            list_input_device_details,
        ),
    )


class LumiStripeRuntime(RuntimeWorker):
    """Stable application API that delegates mutation to ``RuntimeWorker``."""

    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        *,
        controller_factory: _ControllerFactory | None = None,
        audio_factory: _AudioFactory | None = None,
        bluetooth_manager: BluetoothAudioBackend | None = None,
        spotify_manager: SpotifyBackend | None = None,
        fatal_exit: Callable[[int], None] | None = None,
    ) -> None:
        _sync_worker_compatibility_hooks()
        uses_default_controller_factory = controller_factory is None
        super().__init__(
            settings,
            controller_factory=(
                _default_controller_factory
                if controller_factory is None
                else controller_factory
            ),
            audio_factory=(
                _default_audio_factory if audio_factory is None else audio_factory
            ),
            bluetooth_manager=bluetooth_manager,
            spotify_manager=spotify_manager,
            fatal_exit=fatal_exit,
        )
        if uses_default_controller_factory:
            # The worker uses identity to distinguish topology-aware default
            # construction from a custom controller factory. The façade's
            # compatibility wrapper must retain that semantic.
            self._uses_default_controller_factory = True


__all__ = [
    "APPLICATION_VERSION",
    "AUDIO_ROUTE_POLL_SECONDS",
    "BLUETOOTH_PROFILE_KEY",
    "CALIBRATION_FRAME_SECONDS",
    "CALIBRATION_TIMEOUT_SECONDS",
    "FRAME_STALL_GRACE_SECONDS",
    "FRAME_STALL_MAX_SECONDS",
    "FRAME_STALL_MIN_SECONDS",
    "MIN_FRAME_SECONDS",
    "SPOTIFY_PROFILE_KEY",
    "WORKER_STARTUP_GRACE_SECONDS",
    "WORKER_STOP_GRACE_SECONDS",
    "WORKER_WATCHDOG_INTERVAL_SECONDS",
    "LumiStripeRuntime",
    "PreviewFrame",
    "RuntimeCommandError",
    "RuntimeSettings",
    "RuntimeUnavailableError",
    "UnknownAnimationError",
    "_default_controller_factory",
]
