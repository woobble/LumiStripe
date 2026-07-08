import numpy as np
import pytest

from lumistripe import (
    Rgb,
    Rgba,
    apply_brightness_rgb,
    clear,
    fade,
    fill_rgb,
    new_pixel_buffer,
)
from lumistripe.buffers import as_pixel_buffer, normalize_pixel_array


def test_new_pixel_buffer_sets_default_alpha() -> None:
    pixels = new_pixel_buffer(3)

    assert pixels.dtype == np.uint8
    assert pixels.shape == (3, 4)
    np.testing.assert_array_equal(pixels[:, :3], np.zeros((3, 3), dtype=np.uint8))
    np.testing.assert_array_equal(pixels[:, 3], np.full(3, 255, dtype=np.uint8))


def test_new_pixel_buffer_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError, match="length must be positive"):
        new_pixel_buffer(0)


def test_clear_restores_black_with_full_alpha() -> None:
    pixels = np.array([[10, 20, 30, 40], [250, 240, 230, 220]], dtype=np.uint8)

    clear(pixels)

    np.testing.assert_array_equal(
        pixels,
        np.array([[0, 0, 0, 255], [0, 0, 0, 255]], dtype=np.uint8),
    )


def test_fill_rgb_clips_channels_and_alpha() -> None:
    pixels = new_pixel_buffer(2)

    fill_rgb(pixels, -10, 128, 999, 300)

    np.testing.assert_array_equal(
        pixels,
        np.array([[0, 128, 255, 255], [0, 128, 255, 255]], dtype=np.uint8),
    )


def test_fade_scales_rgb_only() -> None:
    pixels = np.array([[100, 50, 25, 200], [255, 128, 0, 10]], dtype=np.uint8)

    fade(pixels, 128)

    np.testing.assert_array_equal(
        pixels,
        np.array([[50, 25, 12, 200], [128, 64, 0, 10]], dtype=np.uint8),
    )


def test_apply_brightness_rgb_returns_scaled_rgb_without_mutating_alpha() -> None:
    pixels = np.array([[100, 50, 25, 200], [255, 128, 0, 10]], dtype=np.uint8)

    scaled = apply_brightness_rgb(pixels, 64)

    np.testing.assert_array_equal(
        scaled,
        np.array([[25, 12, 6], [64, 32, 0]], dtype=np.uint8),
    )
    np.testing.assert_array_equal(pixels[:, 3], np.array([200, 10], dtype=np.uint8))


def test_normalize_pixel_array_accepts_rgb_and_adds_alpha() -> None:
    pixels = normalize_pixel_array([[300, -5, 12], [1, 2, 3]])

    np.testing.assert_array_equal(
        pixels,
        np.array([[255, 0, 12, 255], [1, 2, 3, 255]], dtype=np.uint8),
    )


def test_normalize_pixel_array_accepts_rgba() -> None:
    pixels = normalize_pixel_array([[1, 2, 3, 4], [300, -1, 5, 999]])

    np.testing.assert_array_equal(
        pixels,
        np.array([[1, 2, 3, 4], [255, 0, 5, 255]], dtype=np.uint8),
    )


def test_normalize_pixel_array_rejects_invalid_shape() -> None:
    with pytest.raises(ValueError, match="2-dimensional"):
        normalize_pixel_array([1, 2, 3])

    with pytest.raises(ValueError, match=r"shape \(n, 3\) or \(n, 4\)"):
        normalize_pixel_array([[1, 2], [3, 4]])


def test_as_pixel_buffer_handles_empty_sequence() -> None:
    pixels = as_pixel_buffer([])

    assert pixels.shape == (0, 4)
    assert pixels.dtype == np.uint8


def test_as_pixel_buffer_accepts_color_objects() -> None:
    pixels = as_pixel_buffer([Rgb(1, 2, 3), Rgba(4, 5, 6, 0.5)])

    np.testing.assert_array_equal(
        pixels,
        np.array([[1, 2, 3, 255], [4, 5, 6, 127]], dtype=np.uint8),
    )


def test_as_pixel_buffer_rejects_mixed_color_inputs() -> None:
    with pytest.raises(TypeError, match="mixed color inputs"):
        as_pixel_buffer([Rgb(1, 2, 3), [4, 5, 6]])


def test_as_pixel_buffer_accepts_ndarray() -> None:
    source = np.array([[1, 2, 3]], dtype=np.uint8)

    pixels = as_pixel_buffer(source)

    np.testing.assert_array_equal(pixels, np.array([[1, 2, 3, 255]], dtype=np.uint8))


def test_as_pixel_buffer_accepts_sequence_pixel_values() -> None:
    pixels = as_pixel_buffer([[1, 2, 3], [4, 5, 6]])

    np.testing.assert_array_equal(
        pixels,
        np.array([[1, 2, 3, 255], [4, 5, 6, 255]], dtype=np.uint8),
    )
