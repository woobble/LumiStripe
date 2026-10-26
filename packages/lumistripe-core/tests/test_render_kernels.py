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


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_kernels_reject_partially_overlapping_buffers() -> None:
    native = kernels._native_module
    assert native is not None
    storage = np.zeros((5, 4), dtype=np.uint8)
    correction = np.full(3, 255, dtype=np.uint16)
    with pytest.raises(ValueError, match="overlap"):
        native.correct_rgba_into(storage[:4], storage[1:], correction)

    source = np.zeros((5, 4), dtype=np.uint8)
    corrected_storage = np.zeros((6, 4), dtype=np.uint8)
    with pytest.raises(ValueError, match="overlap"):
        native.apply_alpha_scale_into(
            source, corrected_storage[:5], corrected_storage[1:], 0.5
        )

    with pytest.raises(ValueError, match="overlap"):
        native.composite_screen_rgba_into(
            storage[:4], storage[1:], 1.0, None, 0.0, None, 0.0
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_kernels_allow_exact_in_place_aliases() -> None:
    native = kernels._native_module
    assert native is not None
    frame = np.array([[100, 80, 60, 255], [20, 40, 60, 127]], dtype=np.uint8)
    expected = frame.copy()
    expected[:, :3] = (expected[:, :3].astype(np.uint16) * 128 // 255).astype(np.uint8)
    native.correct_rgba_into(frame, frame, np.array([128, 128, 128], dtype=np.uint16))
    np.testing.assert_array_equal(frame, expected)

    native.apply_alpha_scale_into(frame, frame, frame, 0.5)
    expected[:, 3] = np.floor(expected[:, 3].astype(np.float64) * 0.5).astype(np.uint8)
    np.testing.assert_array_equal(frame, expected)


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_native_tunnel_rejects_nonfinite_geometry(invalid: float) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((3, 4), dtype=np.uint8)
    geometry = np.array([[0.5, 1.0, 0.5, 2.0, 1.0], [0.5, 1.5, 0.5, 2.0, 1.0]])
    geometry[0, 1] = invalid
    with pytest.raises(ValueError, match="finite"):
        native.render_tunnel_rings_into(
            destination,
            geometry,
            np.zeros((2, 3), dtype=np.uint8),
            np.zeros(3, dtype=np.uint8),
            2,
            1.0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("bad_level", [float("nan"), float("inf"), -0.01, 1.01])
def test_native_confetti_rejects_invalid_mutable_state(bad_level: float) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    levels = np.array([0.25, bad_level], dtype=np.float32)
    with pytest.raises(ValueError):
        native.render_neon_confetti_into(
            destination,
            levels,
            np.array([0, 12], dtype=np.int16),
            2,
            1.0,
            1.0,
            0.2,
            0.5,
            1.0,
            0,
            0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_confetti_rejects_negative_palette_index_and_state_alias() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    levels = np.array([0.25, 0.5], dtype=np.float32)
    with pytest.raises(ValueError, match="hues"):
        native.render_neon_confetti_into(
            destination, levels, np.array([0, -1], dtype=np.int16),
            2, 1.0, 1.0, 0.2, 0.5, 1.0, 0, 0,
        )
    shared = np.zeros((2, 4), dtype=np.uint8)
    with pytest.raises(ValueError, match="overlap"):
        native.render_neon_confetti_into(
            shared, shared.view(np.float32).reshape(-1),
            np.zeros(2, dtype=np.int16), 2, 1.0, 1.0, 0.2, 0.5,
            1.0, 0, 0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_rgba_entry_points_reject_misaligned_or_readonly_buffers() -> None:
    native = kernels._native_module
    assert native is not None
    misaligned = np.ndarray((2,), dtype=np.float32, buffer=bytearray(9), offset=1)
    with pytest.raises(ValueError, match="aligned"):
        native.render_neon_confetti_into(
            np.empty((2, 4), dtype=np.uint8), misaligned,
            np.zeros(2, dtype=np.int16), 0, 1.0, 1.0, 0.2,
            0.5, 1.0, 0, 0,
        )

    readonly = np.zeros((1, 4), dtype=np.uint8)
    readonly.flags.writeable = False
    with pytest.raises(ValueError, match="writable"):
        native.render_solid_rgba_into(readonly, 1, 2, 3, 255)


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_particle_geometry_rejects_unsafe_integer_conversions() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    phases = np.zeros(12, dtype=np.float64)
    phases[0] = float(np.iinfo(np.int32).max)
    with pytest.raises(ValueError, match="phases"):
        native.render_firework_burst_into(
            destination, phases, np.zeros((12, 3), dtype=np.float64),
            1, 0, 0, 0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0,
            None, 100, 0, 0,
        )

    bursts = np.zeros((1, 4), dtype=np.float64)
    bursts[0, 2] = float(np.iinfo(np.int32).max)
    with pytest.raises(ValueError, match="hues"):
        native.render_pixel_explosion_into(destination, bursts, 1, 1.0, 1.0, 0, 0)


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_reactive_kernels_reject_invalid_bands_and_integer_seed_overflow() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    with pytest.raises(ValueError, match="bands"):
        native.render_spectrum_flash_into(
            destination, (float("nan"),) + (0.0,) * 7,
            0.5, 0.0, 0.0, 1.0, 0, 0,
        )
    with pytest.raises(ValueError, match="parameters"):
        native.render_spectrum_flash_into(
            destination, (0.0,) * 8, 0.5,
            float(np.iinfo(np.int32).max) * 16.0,
            1.0, 1.0, 0, 0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_pattern_kernels_reject_out_of_range_integer_inputs() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    with pytest.raises(ValueError, match="rainbow"):
        native.render_rainbow_pattern_into(
            destination, 0, 0, None, 1.0e300, 0.0, 100.0,
            1.0, 0.0, 0.5, 0.0, 1.0, 0, 0,
        )
    with pytest.raises(ValueError, match="TheaterChase"):
        native.render_theater_chase_into(
            destination, 4, 1.0e300, 120.0, 0.0, 1.0, 0, 0, 0,
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
