import lumistripe.gpio as gpio_module
import lumistripe.gpio.spi as spi_module
import numpy as np
import pytest
from lumistripe import (
    BrightnessController,
    CompositeController,
    Config,
    DualController,
    GPIOStripe,
    MultiController,
    ReversedController,
    Rgb,
    Rgba,
    SPIConfig,
    SPIStripe,
    Stripe,
    SubStripe,
)
from lumistripe.gpio import encode_legacy_frame


class FakeLineWriter:
    def __init__(self) -> None:
        self.writes: list[tuple[bool, bool]] = []

    def set_values(self, data: bool, clock: bool) -> None:
        self.writes.append((data, clock))


class ClosableFakeLineWriter(FakeLineWriter):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class FakeSPIDevice:
    def __init__(self) -> None:
        self.mode = -1
        self.max_speed_hz = 0
        self.bits_per_word = 0
        self.lsbfirst = True
        self.no_cs = False
        self.transfers: list[tuple[list[int], int, int, int]] = []
        self.close_count = 0

    def open_path(self, path: str) -> None:
        self.path = path

    def xfer2(
        self,
        values: list[int],
        speed_hz: int = 0,
        delay_usec: int = 0,
        bits_per_word: int = 0,
    ) -> list[int]:
        self.transfers.append((values, speed_hz, delay_usec, bits_per_word))
        return [0] * len(values)

    def close(self) -> None:
        self.close_count += 1


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


def test_gpio_stripe_custom_writer_reports_custom_backend() -> None:
    stripe = GPIOStripe(Config(), 1, _line_writer=FakeLineWriter())
    assert stripe.gpio_backend_label == "custom"


def test_gpio_stripe_close_is_idempotent() -> None:
    writer = ClosableFakeLineWriter()
    stripe = GPIOStripe(Config(), 1, _line_writer=writer)

    stripe.close()
    stripe.close()

    assert writer.close_count == 1


def test_gpio_stripe_custom_writer_can_report_backend_label() -> None:
    class LabeledWriter(FakeLineWriter):
        backend_label = "test-writer"

    stripe = GPIOStripe(Config(), 1, _line_writer=LabeledWriter())
    assert stripe.gpio_backend_label == "test-writer"


def test_gpio_stripe_reports_gpiomem_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeGPIOMemLineWriter(FakeLineWriter):
        backend_label = "gpiomem"

        def flush_pixels(self, pixels: np.ndarray) -> None:
            del pixels

    monkeypatch.setattr(gpio_module, "_GPIOMemLineWriter", lambda config: FakeGPIOMemLineWriter())
    stripe = GPIOStripe(Config(), 1)
    assert stripe.gpio_backend_label == "gpiomem"


def test_gpio_stripe_reports_libgpiod_fallback_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeGPIODLineWriter(FakeLineWriter):
        backend_label = "libgpiod"

    def fail_gpiomem(config: Config) -> FakeLineWriter:
        del config
        raise RuntimeError("no gpiomem")

    monkeypatch.setattr(gpio_module, "_GPIOMemLineWriter", fail_gpiomem)
    monkeypatch.setattr(gpio_module, "_GPIODLineWriter", lambda config: FakeGPIODLineWriter())
    stripe = GPIOStripe(Config(), 1)
    assert stripe.gpio_backend_label == "libgpiod"


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


def test_legacy_spi_encoder_matches_gpio_pulse_bits() -> None:
    pixels = np.array(
        [[255, 0, 16, 127], [2, 128, 64, 255]],
        dtype=np.uint8,
    )
    writer = FakeLineWriter()
    gpio = GPIOStripe(Config(), 2, _line_writer=writer)
    gpio.set_pixels(pixels)
    gpio.flush()

    pulse_bits = [
        int(writer.writes[index][0]) for index in range(1, len(writer.writes), 3)
    ]
    encoded = encode_legacy_frame(pixels)
    encoded_bits = np.unpackbits(encoded, bitorder="big").tolist()

    assert encoded_bits[: len(pulse_bits)] == pulse_bits
    assert encoded_bits[len(pulse_bits) :] == [0] * (
        len(encoded_bits) - len(pulse_bits)
    )


def test_spi_stripe_configures_mode_and_sends_one_transfer() -> None:
    device = FakeSPIDevice()
    stripe = SPIStripe(
        SPIConfig(device="/dev/spidev9.2", speed_hz=750_000),
        2,
        _device=device,
    )
    stripe.set_pixel(0, Rgba(255, 0, 0, 0.5))
    stripe.flush()

    assert device.mode == 0
    assert device.max_speed_hz == 750_000
    assert device.bits_per_word == 8
    assert device.lsbfirst is False
    assert device.no_cs is True
    assert stripe.gpio_backend_label == "spi"
    assert stripe.spi_device_path == "/dev/spidev9.2"
    assert len(device.transfers) == 1
    values, speed_hz, delay_usec, bits_per_word = device.transfers[0]
    assert values == encode_legacy_frame(stripe.pixels()).tolist()
    assert (speed_hz, delay_usec, bits_per_word) == (750_000, 0, 8)


def test_spi_stripe_skips_clean_flush_and_force_flushes() -> None:
    device = FakeSPIDevice()
    stripe = SPIStripe(SPIConfig(max_transfer_bytes=4096), 1, _device=device)

    stripe.flush()
    stripe.flush()
    stripe.force_flush()

    assert len(device.transfers) == 2


def test_spi_stripe_rejects_frame_larger_than_single_transfer() -> None:
    stripe = SPIStripe(
        SPIConfig(max_transfer_bytes=1),
        1,
        _device=FakeSPIDevice(),
    )

    with pytest.raises(RuntimeError, match="single-transfer limit"):
        stripe.flush()


def test_spi_stripe_close_is_idempotent_and_prevents_flush() -> None:
    device = FakeSPIDevice()
    stripe = SPIStripe(SPIConfig(), 1, _device=device)

    stripe.close()
    stripe.close()

    assert device.close_count == 1
    with pytest.raises(RuntimeError, match="closed"):
        stripe.force_flush()


def test_spi_config_accepts_defaults() -> None:
    config = SPIConfig(device="/dev/spidev0.0")
    assert config.speed_hz == 1_000_000


def test_spi_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="path"):
        SPIConfig(device="")
    with pytest.raises(ValueError, match="speed"):
        SPIConfig(speed_hz=0)
    with pytest.raises(ValueError, match="transfer"):
        SPIConfig(max_transfer_bytes=0)


def test_spi_stripe_missing_dependency_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_import(name: str):
        raise ImportError(name)

    monkeypatch.setattr(spi_module.importlib, "import_module", raising_import)
    with pytest.raises(RuntimeError, match=r"lumistripe-core\[spi\]"):
        SPIStripe(SPIConfig(), 1)


def test_spi_stripe_open_failure_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    class UnavailableSPIDevice(FakeSPIDevice):
        def open_path(self, path: str) -> None:
            raise PermissionError(13, "Permission denied", path)

    class FakeSPIModule:
        @staticmethod
        def SpiDev() -> UnavailableSPIDevice:
            return UnavailableSPIDevice()

    monkeypatch.setattr(spi_module.importlib, "import_module", lambda name: FakeSPIModule)
    with pytest.raises(RuntimeError, match='cannot open SPI device "/dev/spidev0.0"'):
        SPIStripe(SPIConfig(), 1)
