import numpy as np
import pytest
from lumistripe import (
    BrightnessController,
    ColorCorrection,
    ColorCorrectionController,
    CompositeController,
    MultiController,
    NullController,
    ReversedController,
    Rgb,
    Rgba,
    ScaledMultiController,
    Stripe,
    ZoneMappingController,
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


class CloseFailingStripe(TrackingStripe):
    def __init__(self, length: int, error: Exception | None = None) -> None:
        super().__init__(length)
        self.close_count = 0
        self.close_error = error

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


def test_color_correction_validates_channel_gains() -> None:
    assert ColorCorrection() == ColorCorrection(255, 255, 255)
    with pytest.raises(ValueError, match="red correction"):
        ColorCorrection(red=-1)
    with pytest.raises(ValueError, match="blue correction"):
        ColorCorrection(blue=256)


def test_color_correction_controller_preserves_logical_pixels_and_alpha() -> None:
    inner = TrackingStripe(2)
    controller = ColorCorrectionController(
        inner,
        ColorCorrection(red=255, green=128, blue=64),
    )
    logical = np.array(
        [[100, 100, 100, 127], [255, 128, 64, 255]],
        dtype=np.uint8,
    )

    controller.set_pixels(logical)
    controller.flush()

    np.testing.assert_array_equal(controller.pixels(), logical)
    np.testing.assert_array_equal(
        inner.pixels(),
        np.array([[100, 50, 25, 127], [255, 64, 16, 255]], dtype=np.uint8),
    )
    assert inner.flush_count == 1


def test_color_correction_controller_reapplies_without_compounding() -> None:
    inner = TrackingStripe(1)
    controller = ColorCorrectionController(inner, ColorCorrection(128, 128, 128))
    controller.set_pixel(0, Rgba(200, 100, 50, 0.5))

    controller.force_flush()
    first = inner.pixels().copy()
    controller.force_flush()

    np.testing.assert_array_equal(first, inner.pixels())
    np.testing.assert_array_equal(
        first[0], np.array([100, 50, 25, 127], dtype=np.uint8)
    )
    assert controller.pixel(0).to_rgba() == (200, 100, 50, pytest.approx(127 / 255))
    assert inner.force_flush_count == 2


def test_color_correction_can_change_live_and_delegates_close() -> None:
    inner = TrackingStripe(1)
    controller = ColorCorrectionController(inner)
    controller.fill(Rgb(120, 80, 40))
    controller.set_correction(ColorCorrection(64, 128, 255))
    controller.force_flush()

    np.testing.assert_array_equal(inner.pixels()[0], np.array([30, 40, 40, 255]))
    assert controller.correction == ColorCorrection(64, 128, 255)


def test_color_correction_rejects_invalid_indices_and_overflow() -> None:
    controller = ColorCorrectionController(Stripe(1))
    with pytest.raises(IndexError, match="out of bounds"):
        controller.pixel(1)
    with pytest.raises(ValueError, match="color-correction controller length"):
        controller.set_pixels(np.zeros((2, 4), dtype=np.uint8))


def test_brightness_controller_clamps_and_scales_single_pixel() -> None:
    dark_inner = Stripe(1)
    bright_inner = Stripe(1)

    BrightnessController(dark_inner, -1.0).set_pixel(0, Rgba(10, 20, 30, 1.0))
    BrightnessController(bright_inner, 2.0).set_pixel(0, Rgba(10, 20, 30, 0.5))

    np.testing.assert_array_equal(dark_inner.pixels()[0], np.array([10, 20, 30, 0]))
    np.testing.assert_array_equal(bright_inner.pixels()[0], np.array([10, 20, 30, 127]))


@pytest.mark.parametrize("brightness", [float("nan"), float("inf"), -float("inf")])
def test_brightness_controller_rejects_nonfinite_values(brightness: float) -> None:
    with pytest.raises(ValueError, match="brightness must be finite"):
        BrightnessController(Stripe(1), brightness)


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

    np.testing.assert_array_equal(
        inner.pixels()[1], np.array([9, 8, 7, 255], dtype=np.uint8)
    )


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


def test_aggregate_controller_close_attempts_all_children_and_preserves_first_error() -> None:
    factories = (
        lambda children: CompositeController(children),
        lambda children: MultiController(children),
        lambda children: ScaledMultiController(children),
    )
    for factory in factories:
        first = CloseFailingStripe(2, RuntimeError("first close failed"))
        second = CloseFailingStripe(2, RuntimeError("second close failed"))
        controller = factory([first, second])

        with pytest.raises(RuntimeError, match="first close failed"):
            controller.close()

        assert first.close_count == 1
        assert second.close_count == 1


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
    np.testing.assert_array_equal(
        second.pixels()[1], np.array([9, 8, 7, 255], dtype=np.uint8)
    )


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


def test_composite_controller_partial_set_pixels_leaves_later_children_untouched() -> (
    None
):
    first = Stripe(1)
    second = Stripe(1)
    third = Stripe(1)
    controller = CompositeController([first, second, third])

    controller.set_pixels(np.array([[1, 0, 0, 255]], dtype=np.uint8))

    np.testing.assert_array_equal(
        first.pixels()[0], np.array([1, 0, 0, 255], dtype=np.uint8)
    )
    np.testing.assert_array_equal(
        second.pixels()[0], np.array([0, 0, 0, 255], dtype=np.uint8)
    )
    np.testing.assert_array_equal(
        third.pixels()[0], np.array([0, 0, 0, 255], dtype=np.uint8)
    )


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
    np.testing.assert_array_equal(
        first.pixels()[0], np.array([1, 2, 3, 255], dtype=np.uint8)
    )
    np.testing.assert_array_equal(
        second.pixels(),
        np.array([[1, 2, 3, 255], [1, 2, 3, 255]], dtype=np.uint8),
    )

    controller.clear()
    np.testing.assert_array_equal(
        first.pixels()[0], np.array([0, 0, 0, 255], dtype=np.uint8)
    )
    np.testing.assert_array_equal(
        second.pixels(),
        np.array([[0, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )

    controller.flush()
    controller.force_flush()

    assert (first.flush_count, second.flush_count) == (1, 1)
    assert (first.force_flush_count, second.force_flush_count) == (1, 1)


def test_multi_controller_reads_from_first_child_and_exposes_controllers_tuple() -> (
    None
):
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


def test_scaled_multi_controller_maps_endpoints_across_unequal_lengths() -> None:
    short = Stripe(3)
    long = Stripe(5)
    controller = ScaledMultiController([short, long])
    frame = np.array([[index, 0, 0, 255] for index in range(5)], dtype=np.uint8)

    controller.set_pixels(frame)
    controller.flush()

    np.testing.assert_array_equal(long.pixels(), frame)
    np.testing.assert_array_equal(short.pixels()[:, 0], [0, 2, 4])


def test_scaled_multi_controller_maps_into_reusable_destinations() -> None:
    controller = ScaledMultiController([Stripe(3), Stripe(5)])
    frame = np.array([[index, 0, 0, 255] for index in range(5)], dtype=np.uint8)
    destinations = (
        np.empty((3, 4), dtype=np.uint8),
        np.empty((5, 4), dtype=np.uint8),
    )
    controller.set_pixels(frame)

    mapped = controller.output_pixels_into(destinations)
    assert mapped[0] is destinations[0]
    assert mapped[1] is destinations[1]
    np.testing.assert_array_equal(mapped[0][:, 0], [0, 2, 4])
    np.testing.assert_array_equal(mapped[1], frame)

    controller.set_pixels(frame + np.array([10, 0, 0, 0], dtype=np.uint8))
    controller.output_pixels_into(destinations)
    np.testing.assert_array_equal(destinations[0][:, 0], [10, 12, 14])
    with pytest.raises(ValueError, match="destination count"):
        controller.output_pixels_into(destinations[:1])


def test_null_controller_keeps_runtime_frame_without_output() -> None:
    controller = NullController()
    controller.fill(Rgb(3, 4, 5))
    controller.flush()

    assert controller.pixel(0).to_rgba() == (3, 4, 5, 1.0)


def test_zone_mapping_preserves_one_zone_controller_behavior() -> None:
    inner = TrackingStripe(3)
    controller = ZoneMappingController(inner)
    frame = np.array(
        [[10, 0, 0, 255], [20, 0, 0, 255], [30, 0, 0, 255]],
        dtype=np.uint8,
    )

    controller.set_pixels(frame)
    controller.flush()

    assert controller.length == inner.length == 3
    np.testing.assert_array_equal(controller.pixels(), frame)
    np.testing.assert_array_equal(inner.pixels(), frame)
    assert inner.flush_count == 1


def test_zone_mapping_repeats_logical_frame_into_equal_zones() -> None:
    inner = Stripe(8)
    controller = ZoneMappingController(inner, zone_count=4)
    controller.set_pixels(
        np.array([[10, 0, 0, 255], [20, 0, 0, 255]], dtype=np.uint8)
    )

    controller.flush()

    np.testing.assert_array_equal(
        inner.pixels()[:, 0], [10, 20, 10, 20, 10, 20, 10, 20]
    )


def test_zone_mapping_alternates_orientation_on_every_second_zone() -> None:
    inner = Stripe(8)
    controller = ZoneMappingController(inner, zone_count=4, zone_pattern="alternate")
    controller.set_pixels(
        np.array([[10, 0, 0, 255], [20, 0, 0, 255]], dtype=np.uint8)
    )

    controller.flush()

    np.testing.assert_array_equal(
        inner.pixels()[:, 0], [10, 20, 20, 10, 10, 20, 20, 10]
    )


def test_zone_mapping_distributes_remainder_and_resamples_shorter_zones() -> None:
    inner = Stripe(10)
    controller = ZoneMappingController(inner, zone_count=3)
    controller.set_pixels(
        np.array(
            [[10, 0, 0, 255], [20, 0, 0, 255], [30, 0, 0, 255], [40, 0, 0, 255]],
            dtype=np.uint8,
        )
    )

    controller.flush()

    # The first zone receives the remainder; shorter zones sample the full pattern.
    np.testing.assert_array_equal(
        inner.pixels()[:, 0], [10, 20, 30, 40, 10, 30, 40, 10, 30, 40]
    )


@pytest.mark.parametrize(
    ("zone_count", "zone_pattern", "error"),
    [
        (0, "repeat", ValueError),
        (4, "repeat", ValueError),
        (True, "repeat", TypeError),
        (2, "shift", ValueError),
    ],
)
def test_zone_mapping_rejects_invalid_configuration(
    zone_count: int, zone_pattern: str, error: type[Exception]
) -> None:
    with pytest.raises(error):
        ZoneMappingController(Stripe(3), zone_count, zone_pattern)
