from __future__ import annotations

import numpy as np
import pytest
from lumistripe.render import (
    apply_alpha_scale_into,
    composite_screen_rgba_into,
    correct_rgba_into,
    kernels,
)
from lumistripe.render.kernels import (
    _apply_alpha_scale_numpy,
    _correct_rgba_numpy,
)


def _frames(length: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(20261025)
    source = rng.integers(0, 256, size=(length, 4), dtype=np.uint8)
    corrected = np.empty_like(source)
    destination = np.empty_like(source)
    correction = np.array((128, 255, 37), dtype=np.uint16)
    return source, corrected, destination, correction


@pytest.mark.parametrize("length", [0, 1, 150, 600])
def test_rgba_correction_matches_integer_reference(length: int) -> None:
    source, corrected, _, correction = _frames(length)
    rgb_scratch = np.empty((length, 3), dtype=np.uint16)
    expected = source.copy()
    expected[:, :3] = (
        source[:, :3].astype(np.uint16) * correction // 255
    ).astype(np.uint8)

    _correct_rgba_numpy(source, corrected, correction, rgb_scratch)
    np.testing.assert_array_equal(corrected, expected)

    output = np.empty_like(source)
    correct_rgba_into(source, output, correction, rgb_scratch)
    np.testing.assert_array_equal(output, expected)


@pytest.mark.parametrize("scale", [0.0, 0.1, 0.25, 0.5, 0.999, 1.0])
def test_alpha_scale_matches_numpy_floor_semantics(scale: float) -> None:
    source, corrected, destination, correction = _frames(256)
    source[:, 3] = np.arange(256, dtype=np.uint8)
    rgb_scratch = np.empty((source.shape[0], 3), dtype=np.uint16)
    _correct_rgba_numpy(source, corrected, correction, rgb_scratch)
    expected_corrected = corrected.copy()
    expected_destination = source.copy()
    expected_alpha = np.floor(source[:, 3].astype(np.float64) * scale).astype(np.uint8)
    expected_corrected[:, 3] = expected_alpha
    expected_destination[:, 3] = expected_alpha
    alpha_scratch = np.empty(source.shape[0], dtype=np.float64)

    _apply_alpha_scale_numpy(
        source, corrected, destination, scale, alpha_scratch
    )
    np.testing.assert_array_equal(corrected, expected_corrected)
    np.testing.assert_array_equal(destination, expected_destination)

    corrected = expected_corrected.copy()
    destination.fill(0)
    apply_alpha_scale_into(
        source, corrected, destination, scale, alpha_scratch
    )
    np.testing.assert_array_equal(corrected, expected_corrected)
    np.testing.assert_array_equal(destination, expected_destination)


def test_kernel_boundaries_reject_invalid_buffers() -> None:
    source = np.zeros((2, 4), dtype=np.uint8)
    destination = np.empty_like(source)
    correction = np.array((255, 255, 255), dtype=np.uint16)
    with pytest.raises(ValueError, match="same shape"):
        correct_rgba_into(source, np.empty((1, 4), dtype=np.uint8), correction, np.empty((2, 3), dtype=np.uint16))
    with pytest.raises(ValueError, match="between zero and one"):
        apply_alpha_scale_into(
            source,
            np.empty_like(source),
            destination,
            1.1,
            np.empty(2, dtype=np.float64),
        )


def test_public_kernels_use_numpy_fallback_without_native_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kernels, "_native_module", None)
    source, corrected, destination, correction = _frames(150)
    rgb_scratch = np.empty((source.shape[0], 3), dtype=np.uint16)
    alpha_scratch = np.empty(source.shape[0], dtype=np.float64)

    correct_rgba_into(source, corrected, correction, rgb_scratch)
    apply_alpha_scale_into(source, corrected, destination, 0.63, alpha_scratch)

    expected_corrected = source.copy()
    expected_corrected[:, :3] = (
        source[:, :3].astype(np.uint16) * correction // 255
    ).astype(np.uint8)
    expected_alpha = np.floor(source[:, 3].astype(np.float64) * 0.63).astype(np.uint8)
    expected_corrected[:, 3] = expected_alpha
    expected_destination = source.copy()
    expected_destination[:, 3] = expected_alpha
    np.testing.assert_array_equal(corrected, expected_corrected)
    np.testing.assert_array_equal(destination, expected_destination)


def _composition_reference(
    base: np.ndarray,
    intensity: float,
    overlays: tuple[tuple[np.ndarray, float], ...],
) -> np.ndarray:
    normalized = base.astype(np.float32) / 255.0
    composed = normalized[:, :3] * normalized[:, 3:4] * intensity
    for pixels, strength in overlays:
        if strength <= 0.0:
            continue
        normalized_overlay = pixels.astype(np.float32) / 255.0
        visible = normalized_overlay[:, :3] * normalized_overlay[:, 3:4]
        overlay = visible * strength
        composed = 1.0 - (1.0 - composed) * (1.0 - overlay)
    expected = np.empty_like(base)
    expected[:, :3] = np.clip(composed * 255.0, 0.0, 255.0).astype(np.uint8)
    expected[:, 3] = 255
    return expected


@pytest.mark.parametrize("length", [0, 1, 150, 300, 600])
@pytest.mark.parametrize("overlay_count", [0, 1, 2])
def test_screen_composition_preserves_numpy_pixel_semantics(
    length: int, overlay_count: int
) -> None:
    rng = np.random.default_rng(20261025 + length)
    base = rng.integers(0, 256, size=(length, 4), dtype=np.uint8)
    overlays = [
        rng.integers(0, 256, size=(length, 4), dtype=np.uint8)
        for _ in range(overlay_count)
    ]
    if length:
        alpha = np.resize(np.array([0, 1, 127, 254, 255], dtype=np.uint8), length)
        base[:, 3] = alpha
        for overlay in overlays:
            overlay[:, 3] = alpha[::-1]
    strengths = (0.37, 0.41)
    active = tuple((pixels, strengths[index]) for index, pixels in enumerate(overlays))
    expected = _composition_reference(base, 0.83, active)
    destination = np.empty_like(base)

    composite_screen_rgba_into(
        base,
        destination,
        0.83,
        overlays[0] if overlays else None,
        strengths[0] if overlays else 0.0,
        overlays[1] if len(overlays) > 1 else None,
        strengths[1] if len(overlays) > 1 else 0.0,
    )

    np.testing.assert_array_equal(destination, expected)


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("length", [0, 1, 150, 300, 600])
@pytest.mark.parametrize("overlay_count", [0, 1, 2])
def test_native_screen_composition_matches_numpy_reference(
    length: int, overlay_count: int
) -> None:
    rng = np.random.default_rng(881 + length)
    base = rng.integers(0, 256, size=(length, 4), dtype=np.uint8)
    overlays = [
        rng.integers(0, 256, size=(length, 4), dtype=np.uint8)
        for _ in range(overlay_count)
    ]
    if length:
        alpha = np.resize(np.arange(256, dtype=np.uint8), length)
        base[:, 3] = alpha
        for overlay in overlays:
            overlay[:, 3] = alpha[::-1]
    strengths = (0.0, 0.75)
    active = tuple((pixels, strengths[index]) for index, pixels in enumerate(overlays))
    expected = _composition_reference(base, 1.25, active)
    destination = np.empty_like(base)

    composite_screen_rgba_into(
        base,
        destination,
        1.25,
        overlays[0] if overlays else None,
        strengths[0] if overlays else 0.0,
        overlays[1] if len(overlays) > 1 else None,
        strengths[1] if len(overlays) > 1 else 0.0,
    )

    np.testing.assert_array_equal(destination, expected)


def test_screen_composition_uses_numpy_fallback_without_native_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kernels, "_native_module", None)
    base, _, _, _ = _frames(150)
    overlay_1, _, _, _ = _frames(150)
    overlay_2, _, _, _ = _frames(150)
    destination = np.empty_like(base)
    expected = _composition_reference(
        base, 0.65, ((overlay_1, 0.25), (overlay_2, 0.5))
    )

    composite_screen_rgba_into(
        base, destination, 0.65, overlay_1, 0.25, overlay_2, 0.5
    )

    np.testing.assert_array_equal(destination, expected)


def test_screen_composition_rejects_invalid_strength_and_overlay_shape() -> None:
    base = np.zeros((2, 4), dtype=np.uint8)
    destination = np.empty_like(base)
    with pytest.raises(ValueError, match="non-negative"):
        composite_screen_rgba_into(base, destination, 1.0, strength_1=-0.1)
    with pytest.raises(ValueError, match="same shape"):
        composite_screen_rgba_into(
            base,
            destination,
            1.0,
            np.zeros((1, 4), dtype=np.uint8),
            0.5,
        )
    with pytest.raises(ValueError, match="intensity must be finite"):
        composite_screen_rgba_into(base, destination, float("nan"))


def test_screen_composition_accepts_read_only_overlay_frames() -> None:
    base = np.zeros((2, 4), dtype=np.uint8)
    overlay = np.full((2, 4), 255, dtype=np.uint8)
    overlay.flags.writeable = False
    destination = np.empty_like(base)

    composite_screen_rgba_into(base, destination, 1.0, overlay, 0.5)

    np.testing.assert_array_equal(destination[:, :3], np.full((2, 3), 127))
    np.testing.assert_array_equal(destination[:, 3], np.full(2, 255))
