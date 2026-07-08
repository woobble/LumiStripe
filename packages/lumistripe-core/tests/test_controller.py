import numpy as np
import pytest

from lumistripe import (
    BrightnessController,
    CompositeController,
    MultiController,
    ReversedController,
    Rgb,
    Rgba,
    Stripe,
)


class TrackingStripe(Stripe):
    def __init__(self, length: int) -> None:
        super().__init__(length)
        self.flush_count = 0
        self.force_flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()

    def force_flush(self) -> None:
        self.force_flush_count += 1
        super().force_flush()


def test_brightness_controller_clamps_and_scales_single_pixel() -> None:
    dark_inner = Stripe(1)
    bright_inner = Stripe(1)

    BrightnessController(dark_inner, -1.0).set_pixel(0, Rgba(10, 20, 30, 1.0))
    BrightnessController(bright_inner, 2.0).set_pixel(0, Rgba(10, 20, 30, 0.5))

    np.testing.assert_array_equal(dark_inner.pixels()[0], np.array([10, 20, 30, 0]))
    np.testing.assert_array_equal(bright_inner.pixels()[0], np.array([10, 20, 30, 127]))


def test_brightness_controller_scales_color_sequence_and_array_pixels() -> None:
    sequence_inner = Stripe(2)
    array_inner = Stripe(2)

    BrightnessController(sequence_inner, 0.5).set_pixels(
        [Rgba(10, 20, 30, 1.0), Rgba(40, 50, 60, 0.25)]
    )
    BrightnessController(array_inner, 0.5).set_pixels(
        np.array([[10, 20, 30, 255], [40, 50, 60, 128]], dtype=np.uint8)
    )

    np.testing.assert_array_equal(
        sequence_inner.pixels(),
        np.array([[10, 20, 30, 127], [40, 50, 60, 31]], dtype=np.uint8),
    )
    np.testing.assert_array_equal(
        array_inner.pixels(),
        np.array([[10, 20, 30, 127], [40, 50, 60, 63]], dtype=np.uint8),
    )


def test_brightness_controller_fill_clear_and_flush_delegate() -> None:
    inner = TrackingStripe(2)
    controller = BrightnessController(inner, 0.25)

    controller.fill(Rgba(8, 9, 10, 1.0))
    np.testing.assert_array_equal(
        inner.pixels(),
        np.array([[8, 9, 10, 63], [8, 9, 10, 63]], dtype=np.uint8),
    )

    controller.clear()
    np.testing.assert_array_equal(
        inner.pixels(),
        np.array([[0, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )

    controller.flush()
    controller.force_flush()

    assert inner.flush_count == 1
    assert inner.force_flush_count == 1


def test_brightness_controller_reads_from_inner_controller() -> None:
    inner = Stripe(2)
    inner.set_pixel(1, Rgb(1, 2, 3))
    controller = BrightnessController(inner, 0.5)

    assert controller.length == 2
    np.testing.assert_array_equal(controller.pixels(), inner.pixels())
    assert controller.pixel(1).to_rgba() == (1, 2, 3, 1.0)


def test_reversed_controller_reads_and_writes_from_the_end() -> None:
    inner = Stripe(3)
    inner.set_pixels(np.array([[1, 0, 0, 255], [2, 0, 0, 255], [3, 0, 0, 255]]))
    controller = ReversedController(inner)

    np.testing.assert_array_equal(
        controller.pixels(),
        np.array([[3, 0, 0, 255], [2, 0, 0, 255], [1, 0, 0, 255]], dtype=np.uint8),
    )
    assert controller.pixel(0).to_rgba() == (3, 0, 0, 1.0)

    controller.set_pixel(1, Rgb(9, 8, 7))

    np.testing.assert_array_equal(inner.pixels()[1], np.array([9, 8, 7, 255], dtype=np.uint8))


def test_reversed_controller_rejects_invalid_indices_and_overflow() -> None:
    controller = ReversedController(Stripe(2))

    with pytest.raises(IndexError, match="out of bounds"):
        controller.pixel(-1)
    with pytest.raises(IndexError, match="out of bounds"):
        controller.set_pixel(2, Rgb(1, 2, 3))
    with pytest.raises(ValueError, match="reversed controller length"):
        controller.set_pixels(
            np.array([[1, 0, 0, 255], [2, 0, 0, 255], [3, 0, 0, 255]], dtype=np.uint8)
        )


def test_reversed_controller_fill_clear_and_flush_delegate() -> None:
    inner = TrackingStripe(2)
    controller = ReversedController(inner)

    controller.fill(Rgb(1, 2, 3))
    np.testing.assert_array_equal(
        inner.pixels(),
        np.array([[1, 2, 3, 255], [1, 2, 3, 255]], dtype=np.uint8),
    )

    controller.clear()
    np.testing.assert_array_equal(
        inner.pixels(),
        np.array([[0, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )

    controller.flush()
    controller.force_flush()

    assert inner.flush_count == 1
    assert inner.force_flush_count == 1


def test_composite_controller_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one controller"):
        CompositeController([])


def test_composite_controller_single_child_pixels_fast_path() -> None:
    inner = Stripe(2)
    controller = CompositeController([inner])

    assert controller.pixels() is inner.pixels()


def test_composite_controller_locates_children_for_pixel_operations() -> None:
    first = Stripe(2)
    second = Stripe(3)
    controller = CompositeController([first, second])

    controller.set_pixel(3, Rgb(9, 8, 7))

    assert controller.length == 5
    assert controller.pixel(3).to_rgba() == (9, 8, 7, 1.0)
    np.testing.assert_array_equal(second.pixels()[1], np.array([9, 8, 7, 255], dtype=np.uint8))


def test_composite_controller_partial_set_pixels_stops_after_supplied_pixels() -> None:
    first = Stripe(2)
    second = Stripe(2)
    controller = CompositeController([first, second])

    controller.set_pixels(np.array([[1, 0, 0, 255], [2, 0, 0, 255], [3, 0, 0, 255]]))

    np.testing.assert_array_equal(
        first.pixels(),
        np.array([[1, 0, 0, 255], [2, 0, 0, 255]], dtype=np.uint8),
    )
    np.testing.assert_array_equal(
        second.pixels(),
        np.array([[3, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )


def test_composite_controller_partial_set_pixels_leaves_later_children_untouched() -> None:
    first = Stripe(1)
    second = Stripe(1)
    third = Stripe(1)
    controller = CompositeController([first, second, third])

    controller.set_pixels(np.array([[1, 0, 0, 255]], dtype=np.uint8))

    np.testing.assert_array_equal(first.pixels()[0], np.array([1, 0, 0, 255], dtype=np.uint8))
    np.testing.assert_array_equal(second.pixels()[0], np.array([0, 0, 0, 255], dtype=np.uint8))
    np.testing.assert_array_equal(third.pixels()[0], np.array([0, 0, 0, 255], dtype=np.uint8))


def test_composite_controller_rejects_invalid_indices_and_overflow() -> None:
    controller = CompositeController([Stripe(1), Stripe(1)])

    with pytest.raises(IndexError, match="out of bounds"):
        controller.pixel(-1)
    with pytest.raises(IndexError, match="out of bounds"):
        controller.set_pixel(2, Rgb(1, 2, 3))
    with pytest.raises(ValueError, match="composite controller length"):
        controller.set_pixels(
            np.array([[1, 0, 0, 255], [2, 0, 0, 255], [3, 0, 0, 255]], dtype=np.uint8)
        )


def test_composite_controller_fill_clear_and_flush_delegate_to_all_children() -> None:
    first = TrackingStripe(1)
    second = TrackingStripe(2)
    controller = CompositeController([first, second])

    controller.fill(Rgb(1, 2, 3))
    np.testing.assert_array_equal(first.pixels()[0], np.array([1, 2, 3, 255], dtype=np.uint8))
    np.testing.assert_array_equal(
        second.pixels(),
        np.array([[1, 2, 3, 255], [1, 2, 3, 255]], dtype=np.uint8),
    )

    controller.clear()
    np.testing.assert_array_equal(first.pixels()[0], np.array([0, 0, 0, 255], dtype=np.uint8))
    np.testing.assert_array_equal(
        second.pixels(),
        np.array([[0, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )

    controller.flush()
    controller.force_flush()

    assert (first.flush_count, second.flush_count) == (1, 1)
    assert (first.force_flush_count, second.force_flush_count) == (1, 1)


def test_multi_controller_reads_from_first_child_and_exposes_controllers_tuple() -> None:
    first = Stripe(2)
    second = Stripe(2)
    controller = MultiController([first, second])
    first.set_pixel(1, Rgb(4, 5, 6))

    assert controller.controllers == (first, second)
    assert controller.length == 2
    assert controller.pixels() is first.pixels()
    assert controller.pixel(1).to_rgba() == (4, 5, 6, 1.0)


def test_multi_controller_mirrors_writes_and_flushes() -> None:
    first = TrackingStripe(2)
    second = TrackingStripe(2)
    controller = MultiController([first, second])

    controller.set_pixel(0, Rgb(1, 2, 3))
    controller.set_pixels(np.array([[4, 0, 0, 255], [5, 0, 0, 255]], dtype=np.uint8))
    controller.fill(Rgb(7, 8, 9))

    np.testing.assert_array_equal(first.pixels(), second.pixels())
    np.testing.assert_array_equal(
        first.pixels(),
        np.array([[7, 8, 9, 255], [7, 8, 9, 255]], dtype=np.uint8),
    )

    controller.clear()
    np.testing.assert_array_equal(first.pixels(), second.pixels())
    np.testing.assert_array_equal(
        first.pixels(),
        np.array([[0, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )

    controller.flush()
    controller.force_flush()

    assert (first.flush_count, second.flush_count) == (1, 1)
    assert (first.force_flush_count, second.force_flush_count) == (1, 1)
