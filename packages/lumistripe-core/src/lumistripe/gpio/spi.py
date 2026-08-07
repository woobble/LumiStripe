from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt

from ..stripe import Stripe

DEFAULT_SPI_BUFFER_BYTES = 4096


class _SPIDevice(Protocol):
    mode: int
    max_speed_hz: int
    bits_per_word: int
    lsbfirst: bool
    no_cs: bool

    def open_path(self, path: str) -> None: ...

    def xfer2(
        self,
        values: list[int],
        speed_hz: int = ...,
        delay_usec: int = ...,
        bits_per_word: int = ...,
    ) -> list[int]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SPIConfig:
    device: str = "/dev/spidev0.0"
    speed_hz: int = 1_000_000
    max_transfer_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.device:
            raise ValueError("SPI device path must not be empty")
        if self.speed_hz <= 0:
            raise ValueError("SPI speed must be greater than zero")
        if self.max_transfer_bytes is not None and self.max_transfer_bytes <= 0:
            raise ValueError("SPI maximum transfer size must be greater than zero")


def encode_legacy_frame(pixels: npt.ArrayLike) -> npt.NDArray[np.uint8]:
    """Pack the existing GPIO wire format into an MSB-first byte stream."""
    normalized = np.asarray(pixels, dtype=np.uint8)
    if normalized.ndim != 2 or normalized.shape[1] != 4:
        raise ValueError("pixels must be a 2-D array of shape (n, 4)")

    pixel_count = normalized.shape[0]
    scaled = (
        normalized[:, :3].astype(np.uint16)
        * normalized[:, 3, np.newaxis].astype(np.uint16)
        // 255
    ).astype(np.uint8)
    color_bits = np.unpackbits(scaled, axis=1, bitorder="big")
    framed_pixels = np.ones((pixel_count, 25), dtype=np.uint8)
    framed_pixels[:, 1:] = color_bits
    bits = np.concatenate(
        (
            np.zeros(50, dtype=np.uint8),
            framed_pixels.reshape(-1),
            np.zeros(pixel_count, dtype=np.uint8),
        )
    )
    padding = (-bits.size) % 8
    if padding:
        bits = np.pad(bits, (0, padding), constant_values=0)
    return np.packbits(bits, bitorder="big")


class SPIStripe(Stripe):
    """LED stripe output using one uninterrupted Linux spidev transaction."""

    gpio_backend_label = "spi"

    def __init__(
        self,
        config: SPIConfig,
        length: int,
        *,
        _device: _SPIDevice | None = None,
    ) -> None:
        super().__init__(length)
        self._config = config
        self._closed = False
        self._device = _device or self._open_device(config)
        self._configure_device(self._device, config)
        self._max_transfer_bytes = (
            config.max_transfer_bytes
            if config.max_transfer_bytes is not None
            else _kernel_spi_buffer_size()
        )

    @property
    def spi_device_path(self) -> str:
        return self._config.device

    @property
    def spi_speed_hz(self) -> int:
        return self._config.speed_hz

    def flush(self) -> None:
        if not bool(np.any(self._dirty)):
            return
        self.force_flush()

    def force_flush(self) -> None:
        if self._closed:
            raise RuntimeError("SPI stripe is closed")
        payload = encode_legacy_frame(self._pixels)
        if payload.size > self._max_transfer_bytes:
            raise RuntimeError(
                f"encoded SPI frame is {payload.size} bytes, exceeding the "
                f"single-transfer limit of {self._max_transfer_bytes} bytes; "
                "increase the spidev.bufsiz kernel parameter"
            )
        self._device.xfer2(
            payload.tolist(),
            self._config.speed_hz,
            0,
            8,
        )
        self._dirty[:] = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._device.close()

    @staticmethod
    def _open_device(config: SPIConfig) -> _SPIDevice:
        try:
            spidev = importlib.import_module("spidev")
        except ImportError as exc:
            raise RuntimeError(
                "spidev is required for SPIStripe; install lumistripe-core[spi]"
            ) from exc
        device = spidev.SpiDev()
        try:
            device.open_path(config.device)
        except OSError as exc:
            device.close()
            raise RuntimeError(f'cannot open SPI device "{config.device}": {exc}') from exc
        return device

    @staticmethod
    def _configure_device(device: _SPIDevice, config: SPIConfig) -> None:
        try:
            device.mode = 0
            device.max_speed_hz = config.speed_hz
            device.bits_per_word = 8
            device.lsbfirst = False
            device.no_cs = True
        except OSError as exc:
            device.close()
            raise RuntimeError(
                f'cannot configure SPI device "{config.device}": {exc}'
            ) from exc


def _kernel_spi_buffer_size() -> int:
    path = Path("/sys/module/spidev/parameters/bufsiz")
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return DEFAULT_SPI_BUFFER_BYTES
    return value if value > 0 else DEFAULT_SPI_BUFFER_BYTES
