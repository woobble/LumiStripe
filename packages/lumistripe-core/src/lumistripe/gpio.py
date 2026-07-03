from __future__ import annotations

import importlib
from typing import Protocol

import numpy as np

from .color import Rgba
from .stripe import Config, Stripe, SubStripe


class _LineWriter(Protocol):
    def set_values(self, data: bool, clock: bool) -> None: ...


class _GPIODLineWriter:
    def __init__(self, config: Config) -> None:
        try:
            gpiod = importlib.import_module("gpiod")
        except ImportError as exc:
            raise RuntimeError(
                "gpiod is required for GPIOStripe; install lumistripe-core[gpio]"
            ) from exc

        self._gpiod = gpiod
        self._data_pin = config.gpio_data
        self._clock_pin = config.gpio_clock
        self._request = self._open_request(config)

    def _open_request(self, config: Config):
        gpiod = self._gpiod
        try:
            if self._supports_modern_api(gpiod) and hasattr(gpiod, "request_lines"):
                direction = self._direction_enum(gpiod).OUTPUT
                inactive = self._value_enum(gpiod).INACTIVE
                settings = gpiod.LineSettings(direction=direction, output_value=inactive)
                return gpiod.request_lines(
                    config.chip,
                    consumer=config.consumer,
                    config={
                        config.gpio_data: settings,
                        config.gpio_clock: settings,
                    },
                )

            if hasattr(gpiod, "Chip"):
                chip = gpiod.Chip(config.chip)
            else:
                chip = None
            if chip is not None and self._supports_modern_api(gpiod) and hasattr(chip, "request_lines"):
                direction = self._direction_enum(gpiod).OUTPUT
                inactive = self._value_enum(gpiod).INACTIVE
                settings = gpiod.LineSettings(direction=direction, output_value=inactive)
                return chip.request_lines(
                    consumer=config.consumer,
                    config={
                        config.gpio_data: settings,
                        config.gpio_clock: settings,
                    },
                )
        except PermissionError as exc:
            raise RuntimeError(
                f'permission denied while opening GPIO chip "{config.chip}". '
                "Add your user to the gpio group or run with appropriate permissions."
            ) from exc

        raise RuntimeError(
            "unsupported gpiod Python API; expected libgpiod 2.x bindings with Direction/Value enums"
        )

    def set_values(self, data: bool, clock: bool) -> None:
        gpiod = self._gpiod
        active = self._value_enum(gpiod).ACTIVE
        inactive = self._value_enum(gpiod).INACTIVE
        values = {
            self._data_pin: active if data else inactive,
            self._clock_pin: active if clock else inactive,
        }
        self._request.set_values(values)

    def _supports_modern_api(self, gpiod: object) -> bool:
        if not hasattr(gpiod, "LineSettings"):
            return False
        try:
            self._direction_enum(gpiod)
            self._value_enum(gpiod)
        except RuntimeError:
            return False
        return True

    def _direction_enum(self, gpiod: object):
        if hasattr(gpiod, "Direction"):
            return getattr(gpiod, "Direction")
        line = getattr(gpiod, "line", None)
        if line is not None and hasattr(line, "Direction"):
            return getattr(line, "Direction")
        raise RuntimeError(
            "unsupported gpiod Python API; expected libgpiod 2.x bindings with Direction/Value enums"
        )

    def _value_enum(self, gpiod: object):
        if hasattr(gpiod, "Value"):
            return getattr(gpiod, "Value")
        line = getattr(gpiod, "line", None)
        if line is not None and hasattr(line, "Value"):
            return getattr(line, "Value")
        raise RuntimeError(
            "unsupported gpiod Python API; expected libgpiod 2.x bindings with Direction/Value enums"
        )


class GPIOStripe(Stripe):
    def __init__(
        self,
        config: Config,
        length: int,
        *,
        _line_writer: _LineWriter | None = None,
    ) -> None:
        super().__init__(length, default_color=config.default_color)
        if config.default_color is None:
            self._pixels[:] = np.array([0, 0, 0, 0], dtype=np.uint8)
            self._dirty[:] = True
        self._config = config
        self._scaled = np.zeros((length, 3), dtype=np.uint8)
        self._line_writer = _line_writer or _GPIODLineWriter(config)

    def flush(self) -> None:
        if not bool(np.any(self._dirty)):
            return
        self.force_flush()

    def force_flush(self) -> None:
        dirty_indices = np.nonzero(self._dirty)[0]
        for index in dirty_indices:
            rgba = self._pixels[index]
            self._scaled[index] = Rgba(
                int(rgba[0]),
                int(rgba[1]),
                int(rgba[2]),
                float(rgba[3]) / 255.0,
            ).as_scaled_rgb_array()
            self._dirty[index] = False

        self._write(False, False)

        for _ in range(50):
            self._pulse(False)

        for r, g, b in self._scaled:
            self._pulse(True)
            for byte in (int(r), int(g), int(b)):
                for bit_index in range(7, -1, -1):
                    self._pulse(((byte >> bit_index) & 1) == 1)

        for _ in range(self.length):
            self._pulse(False)

    def clear(self) -> None:
        self._pixels[:] = np.array([0, 0, 0, 0], dtype=np.uint8)
        self._dirty[:] = True

    def sub_stripe(self, start: int, end: int) -> SubStripe:
        return SubStripe(self, start, end)

    def _write(self, data: bool, clock: bool) -> None:
        self._line_writer.set_values(data, clock)

    def _pulse(self, data: bool) -> None:
        self._write(data, False)
        self._write(data, True)
        self._write(data, False)
