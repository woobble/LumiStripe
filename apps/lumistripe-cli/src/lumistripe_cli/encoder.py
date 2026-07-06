from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import importlib
from typing import Protocol, SupportsInt, cast


BUTTON_DEBOUNCE_NS = 150_000_000


@dataclass(frozen=True, slots=True)
class ControlEvent:
    kind: str
    source: str
    value: int = 0


@dataclass(frozen=True, slots=True)
class EncoderPins:
    a: int
    b: int
    button: int


@dataclass(frozen=True, slots=True)
class _EncoderSpec:
    source: str
    pins: EncoderPins


class EncoderBackend(Protocol):
    def read_events(self) -> list[ControlEvent]: ...

    def close(self) -> None: ...


class NullEncoderBackend:
    def read_events(self) -> list[ControlEvent]:
        return []

    def close(self) -> None:
        return None


class GPIODEncoderBackend:
    _TRANSITIONS = {
        (0, 1): 1,
        (1, 3): 1,
        (3, 2): 1,
        (2, 0): 1,
        (0, 2): -1,
        (2, 3): -1,
        (3, 1): -1,
        (1, 0): -1,
    }

    def __init__(self, chip: str, specs: list[_EncoderSpec]) -> None:
        self._gpiod = importlib.import_module("gpiod")
        self._specs = list(specs)
        self._button_offsets: dict[int, str] = {}
        self._quad_offsets: dict[int, str] = {}
        self._button_last_press_ns: dict[int, int] = {}
        self._quad_state: dict[str, int] = {}
        self._quad_accum: dict[str, int] = {}

        edge = self._edge_enum().BOTH
        bias = self._bias_enum().PULL_UP
        settings = self._gpiod.LineSettings(
            edge_detection=edge,
            bias=bias,
            debounce_period=timedelta(milliseconds=1),
        )

        config: dict[int, object] = {}
        for spec in self._specs:
            config[spec.pins.a] = settings
            config[spec.pins.b] = settings
            config[spec.pins.button] = settings
            self._button_offsets[spec.pins.button] = spec.source
            self._quad_offsets[spec.pins.a] = spec.source
            self._quad_offsets[spec.pins.b] = spec.source
            self._quad_accum[spec.source] = 0

        self._request = self._gpiod.request_lines(
            chip,
            consumer="lumistripe_encoder",
            config=config,
        )

        for spec in self._specs:
            self._quad_state[spec.source] = self._read_quad_state(spec)

    def read_events(self) -> list[ControlEvent]:
        events: list[ControlEvent] = []
        while self._request.wait_edge_events(0.0):
            for edge_event in self._request.read_edge_events():
                events.extend(self._decode_event(edge_event))
        return events

    def close(self) -> None:
        self._request.release()

    def _decode_event(self, edge_event: object) -> list[ControlEvent]:
        offset = int(getattr(edge_event, "line_offset"))
        timestamp_ns = int(getattr(edge_event, "timestamp_ns"))
        event_type = getattr(edge_event, "event_type")

        if offset in self._button_offsets:
            return self._decode_button(offset, timestamp_ns, event_type)
        if offset in self._quad_offsets:
            return self._decode_rotation(self._quad_offsets[offset])
        return []

    def _decode_button(self, offset: int, timestamp_ns: int, event_type: object) -> list[ControlEvent]:
        if event_type != self._edge_event_type().FALLING_EDGE:
            return []
        last = self._button_last_press_ns.get(offset, 0)
        if timestamp_ns - last < BUTTON_DEBOUNCE_NS:
            return []
        self._button_last_press_ns[offset] = timestamp_ns
        return [ControlEvent(kind="press", source=self._button_offsets[offset])]

    def _decode_rotation(self, source: str) -> list[ControlEvent]:
        spec = self._spec(source)
        prev = self._quad_state[source]
        curr = self._read_quad_state(spec)
        if curr == prev:
            return []
        self._quad_state[source] = curr
        delta = self._TRANSITIONS.get((prev, curr), 0)
        if delta == 0:
            self._quad_accum[source] = 0
            return []
        accum = self._quad_accum[source] + delta
        self._quad_accum[source] = accum
        if abs(accum) < 4:
            return []
        self._quad_accum[source] = 0
        return [ControlEvent(kind="rotate", source=source, value=1 if accum > 0 else -1)]

    def _read_quad_state(self, spec: _EncoderSpec) -> int:
        a = self._value_to_bit(self._request.get_value(spec.pins.a))
        b = self._value_to_bit(self._request.get_value(spec.pins.b))
        return (a << 1) | b

    def _spec(self, source: str) -> _EncoderSpec:
        for spec in self._specs:
            if spec.source == source:
                return spec
        raise KeyError(source)

    def _value_to_bit(self, value: object) -> int:
        raw = getattr(value, "value", value)
        return 1 if int(cast(SupportsInt, raw)) else 0

    def _edge_enum(self):
        line = getattr(self._gpiod, "line", None)
        if line is not None and hasattr(line, "Edge"):
            return getattr(line, "Edge")
        raise RuntimeError("unsupported gpiod Python API; expected libgpiod 2.x line.Edge enum")

    def _bias_enum(self):
        line = getattr(self._gpiod, "line", None)
        if line is not None and hasattr(line, "Bias"):
            return getattr(line, "Bias")
        raise RuntimeError("unsupported gpiod Python API; expected libgpiod 2.x line.Bias enum")

    def _edge_event_type(self):
        return self._gpiod.EdgeEvent.Type


def build_encoder_backend(
    chip: str,
    *,
    encoder1: EncoderPins | None,
    encoder2: EncoderPins | None,
) -> EncoderBackend:
    specs: list[_EncoderSpec] = []
    if encoder1 is not None:
        specs.append(_EncoderSpec(source="encoder1", pins=encoder1))
    if encoder2 is not None:
        specs.append(_EncoderSpec(source="encoder2", pins=encoder2))
    if not specs:
        return NullEncoderBackend()
    return GPIODEncoderBackend(chip, specs)
