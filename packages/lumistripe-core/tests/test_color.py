import numpy as np

from lumistripe import ColorBatch, Hex, Hsl, Hsla, Rgb, Rgba


def test_rgb_and_rgba_scaling() -> None:
    assert Rgb(255, 20, 10).scaled() == (255, 20, 10)
    assert Rgba(100, 50, 25, 0.5).scaled() == (50, 25, 12)


def test_hsl_and_hex_conversion() -> None:
    assert Hex(0xFF00AA).to_rgba() == (255, 0, 170, 1.0)
    assert Hsl(0, 100, 50).to_rgba() == (255, 0, 0, 1.0)
    assert Hsla(120, 100, 50, 0.25).to_rgba() == (0, 255, 0, 0.25)


def test_color_batch_scaled() -> None:
    batch = ColorBatch([[255, 100, 0, 1.0], [10, 40, 100, 0.5]])
    np.testing.assert_array_equal(batch.scaled(), np.array([[255, 100, 0], [5, 20, 50]], dtype=np.uint8))
