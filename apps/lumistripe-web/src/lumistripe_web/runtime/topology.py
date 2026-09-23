"""Controller and persisted topology construction for the runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

from lumistripe import (
    Config,
    Controller,
    GPIOStripe,
    MultiController,
    SPIConfig,
    SPIStripe,
    Stripe,
)

from ..settings import StripeOutputSettings, StripeTopologySettings
from .state import RuntimeSettings


def default_controller_factory(
    settings: RuntimeSettings,
    *,
    gpio_factory: Callable[..., Controller] = GPIOStripe,
    spi_factory: Callable[..., Controller] = SPIStripe,
) -> Controller:
    if not settings.hardware:
        return Stripe(settings.pixels)
    if settings.output_backend == "gpio":
        return gpio_factory(
            Config(
                chip=settings.chip,
                gpio_data=settings.data_pin,
                gpio_clock=settings.clock_pin,
                consumer="lumistripe-web",
            ),
            settings.pixels,
        )
    primary = spi_factory(
        SPIConfig(device=settings.spi_device, speed_hz=settings.spi_speed_hz),
        settings.pixels,
    )
    if settings.spi_device_2 is None:
        return primary
    try:
        secondary = spi_factory(
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


def topology_from_runtime(settings: RuntimeSettings) -> StripeTopologySettings:
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


__all__ = ["default_controller_factory", "topology_from_runtime"]
