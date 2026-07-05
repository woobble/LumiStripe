from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from lumistripe_cli.encoder import BUTTON_DEBOUNCE_NS, EncoderPins, GPIODEncoderBackend, build_encoder_backend


class FakeEdge(Enum):
    BOTH = 1


class FakeBias(Enum):
    PULL_UP = 1


class FakeEdgeEventType(Enum):
    FALLING_EDGE = 1
    RISING_EDGE = 2


class FakeLineSettings:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


@dataclass(frozen=True)
class FakeEdgeEvent:
    event_type: FakeEdgeEventType
    timestamp_ns: int
    line_offset: int


class FakeRequest:
    def __init__(self, initial_state: dict[int, int], batches: list[tuple[dict[int, int], list[FakeEdgeEvent]]]) -> None:
        self.state = dict(initial_state)
        self.batches = list(batches)
        self.released = False

    def wait_edge_events(self, timeout: float | None = None) -> bool:
        return bool(self.batches)

    def read_edge_events(self, max_events: int | None = None) -> list[FakeEdgeEvent]:
        state_update, events = self.batches.pop(0)
        self.state.update(state_update)
        return events

    def get_value(self, line: int) -> int:
        return self.state[line]

    def release(self) -> None:
        self.released = True


class FakeGpiod:
    LineSettings = FakeLineSettings
    EdgeEvent = type("EdgeEvent", (), {"Type": FakeEdgeEventType})
    line = type("line", (), {"Edge": FakeEdge, "Bias": FakeBias})

    def __init__(self, request: FakeRequest) -> None:
        self._request = request

    def request_lines(self, chip: str, consumer: str, config: dict[int, object]) -> FakeRequest:
        return self._request


def test_gpiod_encoder_backend_decodes_clockwise_rotation(monkeypatch) -> None:
    request = FakeRequest(
        initial_state={5: 0, 6: 0, 13: 1},
        batches=[
            ({5: 0, 6: 1}, [FakeEdgeEvent(FakeEdgeEventType.RISING_EDGE, 1, 6)]),
            ({5: 1, 6: 1}, [FakeEdgeEvent(FakeEdgeEventType.RISING_EDGE, 2, 5)]),
            ({5: 1, 6: 0}, [FakeEdgeEvent(FakeEdgeEventType.FALLING_EDGE, 3, 6)]),
            ({5: 0, 6: 0}, [FakeEdgeEvent(FakeEdgeEventType.FALLING_EDGE, 4, 5)]),
        ],
    )
    monkeypatch.setattr("lumistripe_cli.encoder.importlib.import_module", lambda name: FakeGpiod(request))
    backend = GPIODEncoderBackend("/dev/gpiochip0", [type("Spec", (), {"source": "encoder1", "pins": EncoderPins(5, 6, 13)})()])

    events = backend.read_events()

    assert [(event.kind, event.source, event.value) for event in events] == [("rotate", "encoder1", 1)]
    backend.close()
    assert request.released is True


def test_gpiod_encoder_backend_debounces_button_press(monkeypatch) -> None:
    request = FakeRequest(
        initial_state={5: 0, 6: 0, 13: 1},
        batches=[
            ({13: 0}, [FakeEdgeEvent(FakeEdgeEventType.FALLING_EDGE, 1_000_000_000, 13)]),
            ({13: 1}, [FakeEdgeEvent(FakeEdgeEventType.RISING_EDGE, 1_010_000_000, 13)]),
            ({13: 0}, [FakeEdgeEvent(FakeEdgeEventType.FALLING_EDGE, 1_000_000_000 + BUTTON_DEBOUNCE_NS - 1, 13)]),
        ],
    )
    monkeypatch.setattr("lumistripe_cli.encoder.importlib.import_module", lambda name: FakeGpiod(request))
    backend = GPIODEncoderBackend("/dev/gpiochip0", [type("Spec", (), {"source": "encoder1", "pins": EncoderPins(5, 6, 13)})()])

    events = backend.read_events()

    assert [(event.kind, event.source, event.value) for event in events] == [("press", "encoder1", 0)]


def test_build_encoder_backend_returns_null_backend_when_no_encoders() -> None:
    backend = build_encoder_backend("/dev/gpiochip0", encoder1=None, encoder2=None)
    assert backend.read_events() == []
