import numpy as np
import pytest

from lumistripe import (
    BrightnessController,
    Config,
    CompositeController,
    DualController,
    GPIOStripe,
    MultiController,
    Rgb,
    Rgba,
    ReversedController,
    Stripe,
    SubStripe,
)


class FakeLineWriter:
    def __init__(self) -> None:
        self.writes: list[tuple[bool, bool]] = []

    def set_values(self, data: bool, clock: bool) -> None:
        self.writes.append((data, clock))


def test_stripe_initializes_clear_pixels() -> None:
    stripe = Stripe(3)
    assert stripe.length == 3
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array([[0, 0, 0, 255], [0, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )


def test_set_pixel_and_set_pixels() -> None:
    stripe = Stripe(3)
    stripe.set_pixel(0, Rgb(255, 0, 0))
    stripe.set_pixel(1, Rgba(0, 255, 0, 0.5))
    stripe.set_pixels(np.array([[0, 0, 255, 255]], dtype=np.uint8))
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array([[0, 0, 255, 255], [0, 255, 0, 127], [0, 0, 0, 255]], dtype=np.uint8),
    )


def test_clear_and_fill() -> None:
    stripe = Stripe(2)
    stripe.fill(Rgb(255, 100, 50))
    stripe.clear()
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array([[0, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )


def test_sub_stripe_maps_to_parent() -> None:
    stripe = Stripe(5)
    sub = SubStripe(stripe, 1, 4)
    sub.set_pixel(1, Rgb(10, 20, 30))
    np.testing.assert_array_equal(stripe.pixels()[2], np.array([10, 20, 30, 255], dtype=np.uint8))


def test_dual_controller_mirrors_writes() -> None:
    left = Stripe(2)
    right = Stripe(2)
    dual = DualController(left, right)
    dual.fill(Rgb(1, 2, 3))
    np.testing.assert_array_equal(left.pixels(), right.pixels())


def test_multi_controller_mirrors_writes_to_all_children() -> None:
    stripes = [Stripe(2), Stripe(2), Stripe(2)]
    mirror = MultiController(stripes)
    mirror.fill(Rgb(1, 2, 3))
    for stripe in stripes[1:]:
        np.testing.assert_array_equal(stripes[0].pixels(), stripe.pixels())


def test_multi_controller_rejects_empty_or_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="at least one controller"):
        MultiController([])

    with pytest.raises(ValueError, match="same length"):
        MultiController([Stripe(2), Stripe(3)])


def test_reversed_controller_maps_pixels_from_the_end() -> None:
    stripe = Stripe(4)
    reversed_stripe = ReversedController(stripe)

    reversed_stripe.set_pixel(0, Rgb(1, 2, 3))
    reversed_stripe.set_pixels(
        np.array(
            [[4, 5, 6, 255], [7, 8, 9, 255]],
            dtype=np.uint8,
        )
    )

    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array(
            [[0, 0, 0, 255], [0, 0, 0, 255], [7, 8, 9, 255], [4, 5, 6, 255]],
            dtype=np.uint8,
        ),
    )


def test_composite_controller_allows_unequal_segment_lengths() -> None:
    lower = Stripe(2)
    upper = Stripe(4)
    controller = CompositeController([lower, ReversedController(upper)])

    controller.set_pixels(
        np.array(
            [
                [10, 0, 0, 255],
                [20, 0, 0, 255],
                [30, 0, 0, 255],
                [40, 0, 0, 255],
                [50, 0, 0, 255],
                [60, 0, 0, 255],
            ],
            dtype=np.uint8,
        )
    )

    np.testing.assert_array_equal(
        lower.pixels(),
        np.array([[10, 0, 0, 255], [20, 0, 0, 255]], dtype=np.uint8),
    )
    np.testing.assert_array_equal(
        upper.pixels(),
        np.array(
            [[60, 0, 0, 255], [50, 0, 0, 255], [40, 0, 0, 255], [30, 0, 0, 255]],
            dtype=np.uint8,
        ),
    )
    np.testing.assert_array_equal(
        controller.pixels(),
        np.array(
            [
                [10, 0, 0, 255],
                [20, 0, 0, 255],
                [30, 0, 0, 255],
                [40, 0, 0, 255],
                [50, 0, 0, 255],
                [60, 0, 0, 255],
            ],
            dtype=np.uint8,
        ),
    )


def test_zigzag_layout_drives_both_sides_with_reversed_upper_halves() -> None:
    left_physical = Stripe(6)
    right_physical = Stripe(6)

    left = CompositeController(
        [
            SubStripe(left_physical, 0, 2),
            ReversedController(SubStripe(left_physical, 2, 6)),
        ]
    )
    right = CompositeController(
        [
            SubStripe(right_physical, 0, 2),
            ReversedController(SubStripe(right_physical, 2, 6)),
        ]
    )
    layout = MultiController([left, right])

    layout.set_pixels(
        np.array(
            [
                [1, 0, 0, 255],
                [2, 0, 0, 255],
                [3, 0, 0, 255],
                [4, 0, 0, 255],
                [5, 0, 0, 255],
                [6, 0, 0, 255],
            ],
            dtype=np.uint8,
        )
    )

    expected = np.array(
        [
            [1, 0, 0, 255],
            [2, 0, 0, 255],
            [6, 0, 0, 255],
            [5, 0, 0, 255],
            [4, 0, 0, 255],
            [3, 0, 0, 255],
        ],
        dtype=np.uint8,
    )

    np.testing.assert_array_equal(left_physical.pixels(), expected)
    np.testing.assert_array_equal(right_physical.pixels(), expected)


def test_brightness_controller_scales_alpha() -> None:
    stripe = Stripe(1)
    bright = BrightnessController(stripe, 0.5)
    bright.set_pixel(0, Rgba(10, 20, 30, 1.0))
    np.testing.assert_array_equal(stripe.pixels()[0], np.array([10, 20, 30, 127], dtype=np.uint8))


def test_bounds_errors() -> None:
    stripe = Stripe(2)
    with pytest.raises(IndexError):
        stripe.pixel(9)


def test_sub_stripe_fill_and_clear_update_parent() -> None:
    stripe = Stripe(4)
    sub = SubStripe(stripe, 1, 3)
    sub.fill(Rgb(8, 9, 10))
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array(
            [[0, 0, 0, 255], [8, 9, 10, 255], [8, 9, 10, 255], [0, 0, 0, 255]],
            dtype=np.uint8,
        ),
    )

    sub.clear()
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array(
            [[0, 0, 0, 255], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 255]],
            dtype=np.uint8,
        ),
    )


def test_gpio_stripe_flush_writes_expected_frame_shape() -> None:
    writer = FakeLineWriter()
    stripe = GPIOStripe(Config(), 2, _line_writer=writer)
    stripe.flush()

    pulse_count = 50 + (2 * 25) + 2
    assert writer.writes[0] == (False, False)
    assert len(writer.writes) == 1 + pulse_count * 3


def test_gpio_stripe_skip_flush_when_clean() -> None:
    writer = FakeLineWriter()
    stripe = GPIOStripe(Config(), 1, _line_writer=writer)
    stripe.flush()
    first_flush_count = len(writer.writes)
    stripe.flush()
    assert len(writer.writes) == first_flush_count


def test_gpio_stripe_force_flush_always_writes() -> None:
    writer = FakeLineWriter()
    stripe = GPIOStripe(Config(), 1, _line_writer=writer)
    stripe.flush()
    first_flush_count = len(writer.writes)
    stripe.force_flush()
    assert len(writer.writes) == first_flush_count * 2


def test_gpio_stripe_clear_marks_transparent_black() -> None:
    writer = FakeLineWriter()
    stripe = GPIOStripe(Config(default_color=Rgb(10, 20, 30)), 1, _line_writer=writer)
    stripe.clear()
    np.testing.assert_array_equal(stripe.pixels()[0], np.array([0, 0, 0, 0], dtype=np.uint8))


def test_gpio_stripe_transmits_scaled_rgb_bits_msb_first() -> None:
    writer = FakeLineWriter()
    stripe = GPIOStripe(Config(), 1, _line_writer=writer)
    stripe.set_pixel(0, Rgba(255, 0, 0, 0.5))
    stripe.flush()

    pulses = [writer.writes[index : index + 3] for index in range(1, len(writer.writes), 3)]
    pixel_pulses = pulses[50 : 50 + 25]
    assert pixel_pulses[0] == [(True, False), (True, True), (True, False)]

    bits = [pulse[0][0] for pulse in pixel_pulses[1:]]
    expected = [bool((127 >> bit) & 1) for bit in range(7, -1, -1)] + [False] * 16
    assert bits == expected


def test_gpio_stripe_missing_dependency_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_import(name: str):
        if name == "gpiod":
            raise ImportError("missing")
        return __import__(name)

    monkeypatch.setattr("lumistripe.gpio.importlib.import_module", raising_import)
    with pytest.raises(RuntimeError, match="install lumistripe-core\\[gpio\\]"):
        GPIOStripe(Config(), 1)


def test_gpio_stripe_unsupported_gpiod_api_raises_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeGpiod:
        class LineSettings:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

        @staticmethod
        def request_lines(*args, **kwargs):
            raise AssertionError("should not reach request_lines for unsupported API")

    monkeypatch.setattr("lumistripe.gpio.importlib.import_module", lambda name: FakeGpiod)
    with pytest.raises(RuntimeError, match="unsupported gpiod Python API"):
        GPIOStripe(Config(), 1)


def test_gpio_stripe_permission_error_raises_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeGpiod:
        class Direction:
            OUTPUT = object()

        class Value:
            ACTIVE = object()
            INACTIVE = object()

        class LineSettings:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

        class Chip:
            def __init__(self, path: str) -> None:
                raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr("lumistripe.gpio.importlib.import_module", lambda name: FakeGpiod)
    with pytest.raises(RuntimeError, match='permission denied while opening GPIO chip'):
        GPIOStripe(Config(), 1)


def test_gpio_stripe_supports_nested_line_enums(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRequest:
        def __init__(self) -> None:
            self.values = []

        def set_values(self, values) -> None:
            self.values.append(values)

    class FakeLineModule:
        class Direction:
            OUTPUT = "output"

        class Value:
            ACTIVE = "active"
            INACTIVE = "inactive"

    class FakeGpiod:
        line = FakeLineModule

        class LineSettings:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

        @staticmethod
        def request_lines(*args, **kwargs):
            return FakeRequest()

    monkeypatch.setattr("lumistripe.gpio.importlib.import_module", lambda name: FakeGpiod)
    stripe = GPIOStripe(Config(), 1)
    stripe.flush()
