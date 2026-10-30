from __future__ import annotations

import math
import sys

import numpy as np
import pytest
from lumistripe.color import Hsl
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
def test_native_electric_storm_rejects_unsafe_geometry() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((8, 4), dtype=np.uint8)
    for invalid in (np.inf, -1.0, float(2**31)):
        geometry = np.array([[1.0, invalid, 1e300, 0.5]], dtype=np.float64)
        with pytest.raises(ValueError):
            native.render_electric_storm_into(destination, geometry, 0.5, 1.0, 1, 0)


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_electric_storm_accepts_large_finite_palette_hue() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((8, 4), dtype=np.uint8)
    geometry = np.array([[7.0, 1.0, 1e300, 0.5]], dtype=np.float64)
    native.render_electric_storm_into(destination, geometry, 0.5, 1.0, 1, 0)
    assert destination.shape == (8, 4)


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
@pytest.mark.parametrize("field", ["addition", "accent", "high", "bloom"])
@pytest.mark.parametrize("value", [-1e300, 1e300])
def test_confetti_rejects_unbounded_values_before_integer_conversion(
    field: str, value: float,
) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    intensities = np.zeros(1, dtype=np.float32)
    bands = (0.2,) * 8
    parameters = {
        "addition": 0.4,
        "accent": 0.3,
        "high": 0.2,
        "bloom": 0.2,
    }
    parameters[field] = value

    with pytest.raises(ValueError, match="Confetti render parameters"):
        native.render_confetti_into(
            destination, intensities, bands, 0, True, 0.5, 0.5, 10.0,
            20, parameters["addition"], parameters["accent"],
            parameters["high"], parameters["bloom"], 0, 1.0, 0, 0,
        )
    with pytest.raises(ValueError, match="normalized ranges"):
        kernels.render_confetti_into(
            destination, intensities, 0, bands, 0.5, 0.5, 10.0, 20,
            parameters["addition"], parameters["accent"],
            parameters["high"], parameters["bloom"], 0, 1.0,
            "rainbow", "#7C3AED",
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("value", [-0.01, 1.01, 1e300])
def test_confetti_rejects_unnormalized_audio_bands(value: float) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    intensities = np.zeros(1, dtype=np.float32)
    bands = (value,) + (0.2,) * 7

    with pytest.raises(ValueError, match="normalized finite values"):
        native.render_confetti_into(
            destination, intensities, bands, 0, True, 0.5, 0.5, 10.0,
            20, 0.4, 0.3, 0.2, 0.2, 0, 1.0, 0, 0,
        )
    with pytest.raises(ValueError, match="bands must be normalized"):
        kernels.render_confetti_into(
            destination, intensities, 0, bands, 0.5, 0.5, 10.0, 20,
            0.4, 0.3, 0.2, 0.2, 0, 1.0, "rainbow", "#7C3AED",
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("fade_multiplier", [1.01, 1e300])
def test_neon_confetti_rejects_fade_before_float_conversion(
    fade_multiplier: float,
) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    levels = np.zeros(1, dtype=np.float32)
    hues = np.zeros(1, dtype=np.int16)

    with pytest.raises(ValueError, match="NeonConfetti render parameters"):
        native.render_neon_confetti_into(
            destination, levels, hues, 0, fade_multiplier, 100.0,
            0.5, 0.5, 1.0, 0, 0,
        )
    with pytest.raises(ValueError, match="out of range"):
        kernels.render_neon_confetti_into(
            destination, levels, hues, 0, fade_multiplier, 100.0,
            0.5, 0.5, 1.0, "rainbow", "#7C3AED",
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("glow_alpha", [1.01, 1e300])
def test_glow_rush_rejects_unbounded_glow_alpha(glow_alpha: float) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="GlowRush"):
        native.render_glow_rush_into(
            destination, 0.0, 1.0, 10, 0.5, 1.0, 1.0, 0, 30,
            glow_alpha, 0, 0,
        )
    with pytest.raises(ValueError, match="valid ranges"):
        kernels.render_glow_rush_into(
            destination, 0.0, 1.0, 10, 0.5, 1.0, 1.0, False, 30,
            glow_alpha, "rainbow", "#7C3AED",
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("strength", [-1e300, 1.01, 1e300])
def test_color_burst_rejects_unnormalized_active_strength_before_writing(
    strength: float,
) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.full((1, 4), 17, dtype=np.uint8)
    bursts = np.zeros((6, 5), dtype=np.float64)
    bursts[0] = (0.0, 1.0, 10.0, strength, 1.0)

    with pytest.raises(ValueError, match="active burst hue or strength"):
        native.render_color_burst_into(destination, bursts, 1, 0, 1.0, 0)
    np.testing.assert_array_equal(destination, np.full((1, 4), 17, dtype=np.uint8))
    with pytest.raises(ValueError, match="active strengths"):
        kernels.render_color_burst_into(
            destination, bursts, 1, 1.0, "rainbow", "#7C3AED",
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("alpha", [-1e300, 1.01, 1e300])
def test_pixel_explosion_rejects_unnormalized_active_alpha_before_writing(
    alpha: float,
) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.full((1, 4), 17, dtype=np.uint8)
    bursts = np.array([[0.0, 1.0, 10.0, alpha]], dtype=np.float64)

    with pytest.raises(ValueError, match="active explosion hues or alpha"):
        native.render_pixel_explosion_into(destination, bursts, 1, 1.0, 1.0, 0, 0)
    np.testing.assert_array_equal(destination, np.full((1, 4), 17, dtype=np.uint8))
    with pytest.raises(ValueError, match="active alpha"):
        kernels.render_pixel_explosion_into(
            destination, bursts, 1, 1.0, 1.0, "rainbow", "#7C3AED",
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
@pytest.mark.parametrize("value", [-1e300, 1.01, 1e300])
def test_twinkle_rejects_unbounded_bloom(value: float) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    levels = np.zeros(2, dtype=np.float32)
    hues = np.zeros(2, dtype=np.int16)

    with pytest.raises(ValueError, match="Twinkle"):
        native.render_twinkle_into(destination, levels, hues, 0, 1, value, 1.0, 0, 0)
    with pytest.raises(ValueError, match="bloom"):
        kernels.render_twinkle_into(destination, levels, hues, 0, 1, value, 1.0, 0, 0)


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("shimmer", [-1e300, 1.01, 1e300])
def test_comet_rejects_unbounded_shimmer(shimmer: float) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="Comet"):
        native.render_comet_into(destination, 0.0, 1.0, shimmer, 0.0, 1.0, 1.0, 0, 0)
    with pytest.raises(ValueError, match="shimmer"):
        kernels.render_comet_into(
            destination, 0.0, 1.0, shimmer, 0.0, 1.0, 1.0, "rainbow", "#7C3AED",
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("hue", [-1e300, 1e300])
@pytest.mark.parametrize("palette", [(1, "neon"), (2, "fire")])
def test_comet_large_hues_keep_palette_indices_in_range(
    hue: float, palette: tuple[int, str],
) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    direct = np.empty_like(destination)
    palette_id, palette_name = palette

    native.render_comet_into(direct, 0.0, 1.0, 0.2, hue, 1.0, 1.0, palette_id, 0x7C3AED)
    assert kernels.render_comet_into(
        destination, 0.0, 1.0, 0.2, hue, 1.0, 1.0, palette_name, "#7C3AED",
    )
    np.testing.assert_array_equal(destination, direct)
    assert destination[0, 3] > 0


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("value", [-1e300, 1.01, 1e300])
def test_firework_rejects_unbounded_glow(value: float) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    phases = np.zeros(12, dtype=np.float64)
    bursts = np.zeros((12, 3), dtype=np.float64)

    with pytest.raises(ValueError, match="FireworkBurst"):
        native.render_firework_burst_into(
            destination, phases, bursts, 0, 0, 0, 1, 0.0, 1.0, value,
            0.5, 0.5, 1.0, None, 100, 0, 0,
        )
    with pytest.raises(ValueError, match="between zero and one"):
        kernels.render_firework_burst_into(
            destination, phases, 0, bursts, 0, 0, True, 0.0, 1.0, value,
            0.5, 0.5, 1.0, None, 100, "rainbow", "#7C3AED",
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("field,value", [("mid", 1.01), ("high", -0.01), ("band", 1.01)])
def test_firework_rejects_unnormalized_audio_inputs(field: str, value: float) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    phases = np.zeros(12, dtype=np.float64)
    bursts = np.zeros((12, 3), dtype=np.float64)
    mid = value if field == "mid" else 0.5
    high = value if field == "high" else 0.5
    bands = (value,) if field == "band" else None

    with pytest.raises(ValueError):
        native.render_firework_burst_into(
            destination, phases, bursts, 0, 0, 0, 1, 0.0, 1.0, 0.0,
            mid, high, 1.0, bands, 100, 0, 0,
        )
    with pytest.raises(ValueError):
        kernels.render_firework_burst_into(
            destination, phases, 0, bursts, 0, 0, True, 0.0, 1.0, 0.0,
            mid, high, 1.0, bands, 100, "rainbow", "#7C3AED",
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_firework_burst_avoids_phase_and_frame_integer_overflow() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    phases = np.zeros(12, dtype=np.float64)
    bursts = np.zeros((12, 3), dtype=np.float64)
    bursts[0] = (1.0, 1.0, 100.0)

    native.render_firework_burst_into(
        destination,
        phases,
        bursts,
        0,
        1,
        2**40,
        1,
        0.0,
        1.0,
        0.0,
        0.5,
        0.5,
        1.0,
        None,
        100,
        0,
        0,
    )

    bursts[0, 0] = (float(np.iinfo(np.int32).max) - 1.0) / 10.0
    native.render_firework_burst_into(
        destination,
        phases,
        bursts,
        0,
        1,
        0,
        1,
        0.0,
        1.0,
        0.0,
        0.5,
        0.5,
        1.0,
        None,
        100,
        0,
        0,
    )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("frame", [sys.maxsize, -sys.maxsize - 1])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_drop_explosion_wraps_extreme_frame_arithmetic(
    frame: int, audio_mode: bool,
) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((6, 4), dtype=np.uint8)

    native.render_drop_explosion_into(
        destination,
        frame,
        0.0,
        0.5,
        0.0,
        audio_mode,
        0.4,
        0.8,
        1.0,
    )

    expected = []
    for index in range(destination.shape[0]):
        if audio_mode:
            pulse = ((frame * 2 + index) % 24) / 24.0
            alpha = 0.5 * 0.12 + pulse * 0.06
            expected.append((20, 8, 3, int(alpha * 255.0)))
        else:
            pulse = ((frame * 3 + index) % 30) / 30.0
            alpha = 0.5 * 0.15 + pulse * 0.08
            expected.append((30, 10, 5, int(alpha * 255.0)))
    np.testing.assert_array_equal(destination, np.asarray(expected, dtype=np.uint8))


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize(("low", "high"), [(1e308, 0.5), (0.5, -0.1)])
def test_native_drop_explosion_rejects_out_of_range_audio_levels(
    low: float, high: float
) -> None:
    native = kernels._native_module
    assert native is not None

    with pytest.raises(ValueError, match="out of range"):
        native.render_drop_explosion_into(
            np.empty((1, 4), dtype=np.uint8),
            0,
            0.0,
            0.5,
            0.0,
            True,
            low,
            high,
            1.0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("field", ["local", "boost"])
@pytest.mark.parametrize("value", [-1e300, -0.01, 1.01, 1e300])
def test_native_color_wipe_rejects_unsafe_integer_conversion_inputs(
    field: str, value: float,
) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((3, 4), dtype=np.uint8)
    local = value if field == "local" else 0.0
    boost = value if field == "boost" else 0.0

    with pytest.raises(ValueError, match="geometry is invalid"):
        native.render_color_wipe_into(
            destination, 3, 0.0, 1.0, 120.0, 100.0, 1,
            local, boost, 1.0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("field", ["local", "boost"])
@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_color_wipe_wrapper_rejects_unnormalized_audio_values(
    field: str, value: float,
) -> None:
    destination = np.empty((3, 4), dtype=np.uint8)
    local = value if field == "local" else 0.0
    boost = value if field == "boost" else 0.0

    with pytest.raises(ValueError, match="normalized"):
        kernels.render_color_wipe_into(
            destination, 0.0, 1.0, 120.0, 100.0, 1,
            local, boost, 1.0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_wave_and_juggle_wrap_extreme_frame_hues_safely() -> None:
    native = kernels._native_module
    assert native is not None

    frame = int(np.iinfo(np.intp).max)
    static_wave = np.empty((2, 4), dtype=np.uint8)
    native.render_wave_into(
        static_wave, frame, 0, None, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0, 0
    )
    expected_static_hues = np.array(
        [((frame + index) * 2) % 256 for index in range(2)], dtype=np.uint8
    )
    np.testing.assert_array_equal(static_wave[:, 1], expected_static_hues)

    large_frame = 2**32 + 12_345
    audio_wave = np.empty((1, 4), dtype=np.uint8)
    native.render_wave_into(
        audio_wave,
        large_frame,
        1,
        (0.0,) * 8,
        1.0,
        0.5,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0,
        0,
    )
    hue_shift = int(large_frame * 0.08) % 256
    hue = (160 + hue_shift // 6) % 256
    crest = (math.sin(large_frame * 0.5) * 0.5 + 0.5) * 0.24
    lightness = min(56 + int(crest * 6.0), 68)
    expected_audio_rgb = Hsl(hue, 100, lightness).to_rgba()[:3]
    np.testing.assert_array_equal(audio_wave[0, :3], expected_audio_rgb)

    juggle = np.empty((2, 4), dtype=np.uint8)
    native.render_juggle_into(juggle, (1.0,), 1.1, frame, 0.0, 1.0, 1, 0, 0)
    assert juggle[1, 0] == 11
    assert tuple(juggle[1, 1:3]) == (255, 200)


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


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_rainbow_reduces_hue_before_narrowing_large_sum() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    frame = np.iinfo(np.int32).max // 2
    spread = np.iinfo(np.int32).max / 256.0 / len(destination)

    native.render_rainbow_pattern_into(
        destination, 0, frame, None, spread, 0.0, 100.0,
        1.0, 0.0, 0.0, 0.0, 1.0, 0, 0,
    )

    expected = np.empty_like(destination)
    for index in range(len(destination)):
        position = index / (len(destination) - 1)
        hue = int(position * 256.0 * spread + frame * 1.5) % 256
        expected[index] = Hsl(hue, 100, 50).to_rgba()[:3] + (255,)
    np.testing.assert_array_equal(destination, expected)


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_theater_chase_handles_maximum_stride_without_overflow() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)

    native.render_theater_chase_into(
        destination, np.iinfo(np.int32).max,
        float(np.iinfo(np.int32).max - 1), 120.0, 0.0,
        1.0, 0, 0, 0,
    )

    assert destination[0, 3] == 63


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_disco_comet_reduces_seed_before_signed_palette_arithmetic() -> None:
    native = kernels._native_module
    assert native is not None
    if sys.maxsize < (1 << 63) - 1:
        pytest.skip("the overflow case requires a 64-bit Py_ssize_t")
    modulus = 1 << 64
    target_seed = sys.maxsize - 100
    frame_unsigned = ((target_seed - 100) * pow(97, -1, modulus)) % modulus
    frame = frame_unsigned if frame_unsigned <= sys.maxsize else frame_unsigned - modulus
    seed = (frame * 97 + 100) % modulus
    assert seed == target_seed
    destination = np.empty((1, 4), dtype=np.uint8)

    native.render_disco_comet_into(
        destination, 0.0, 1.0, 0.15, 255, 1.0, 100,
        frame, 1.0, 0, 0,
    )

    neon_hues = (300, 160, 52, 206, 12, 268, 132)
    expected = Hsl(neon_hues[(255 + seed) % 7], 100, 62).to_rgba()
    assert tuple(destination[0, :3]) == expected[:3]
    assert destination[0, 3] == 255


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_center_burst_normalizes_negative_palette_seed() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)

    native.render_center_burst_into(
        destination, -1, 0.0, 0.0, 1.0, 1.0, 1.0, 0, 0,
    )

    expected = Hsl(286, 100, 60).to_rgba()
    assert tuple(destination[0, :3]) == expected[:3]
    assert destination[0, 3] == 255


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_drop_wave_avoids_seed_and_pixel_index_overflow() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    seed = sys.maxsize

    native.render_drop_wave_into(
        destination, seed, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0, 0,
    )

    warm_hues = (0, 20, 38, 320)
    expected = Hsl(warm_hues[(seed + 1) % 4], 100, 60).to_rgba()
    assert tuple(destination[1, :3]) == expected[:3]
    assert destination[1, 3] == 255


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_club_flash_wraps_segment_start_without_int_overflow() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((4, 4), dtype=np.uint8)

    native.render_club_flash_into(
        destination, -1, np.iinfo(np.int32).max, 2, 0, 1.0, 1.0, 0, 0,
    )

    expected = Hsl(286, 100, 58).to_rgba()
    assert tuple(destination[3, :3]) == expected[:3]
    assert tuple(destination[0, :3]) == expected[:3]
    assert destination[0, 3] == 255


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_lightning_strike_rejects_unbounded_all_flash() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)

    kernels.render_lightning_strike_into(
        destination, 0, 0, 120, 1.0, 1.5, 1.0, False, "rainbow", "#7C3AED",
    )
    assert destination[0, 3] == 153

    with pytest.raises(ValueError, match="out of range"):
        kernels.render_lightning_strike_into(
            destination, 0, 0, 120, 1.0, 1.5001, 1.0, False, "rainbow", "#7C3AED",
        )

    with pytest.raises(ValueError, match="LightningStrike"):
        native.render_lightning_strike_into(
            destination, 0, 0, 120, 0.0, np.finfo(np.float64).max,
            1.0, 0, 0, 0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_mirror_flash_reduces_extreme_seed_before_offset() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((4, 4), dtype=np.uint8)
    seed = -sys.maxsize - 1

    native.render_mirror_flash_into(
        destination, seed, 1.0, 2.0, 1.0, 1.0, 0, 0, 0,
    )

    club_hues = (330, 18, 48, 126, 190, 224, 286)
    expected = Hsl(club_hues[(seed - 9) % 7], 100, 62).to_rgba()
    assert tuple(destination[1, :3]) == expected[:3]
    assert tuple(destination[2, :3]) == expected[:3]


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_pulse_reduces_extreme_finite_phase_and_hue() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    extreme = np.finfo(np.float64).max

    native.render_pulse_into(destination, extreme, -extreme, 1.0, 1.0, 1, 0)

    expected_hues = (300, 160, 52, 206, 12, 268, 132)
    hue_index = (math.trunc((-extreme) / 45.0)) % 7
    expected = Hsl(expected_hues[hue_index], 82, 51).to_rgba()
    assert tuple(destination[0, :3]) == expected[:3]
    assert destination[0, 3] == 255


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_rave_scanner_reduces_distance_before_integer_conversion() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    extreme = np.finfo(np.float64).max

    native.render_rave_scanner_into(destination, -extreme, extreme, 1.0, 1.0, 255, 0, 0)

    assert destination[0, 3] == 63


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_dance_floor_handles_maximum_frame_without_signed_overflow() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    frame = np.iinfo(np.int32).max

    native.render_dance_floor_into(
        destination, frame, 1, 50.0, 0, 0.0,
        0.0, 0.0, 0.0, 0.0, 1.0, 0, 0,
    )

    hue = (frame % 128) * 2 % 256
    phase = frame * 0.08
    lightness = 40 + int((1.0 + math.sin(phase)) * 15.0)
    expected = Hsl(hue, 100, lightness).to_rgba()
    assert tuple(destination[0, :3]) == expected[:3]


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_plasma_rave_rejects_nonfinite_derived_phase() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    extreme = np.finfo(np.float64).max

    with pytest.raises(ValueError, match="phases"):
        native.render_plasma_rave_into(
            destination, extreme, 0.0, 0.0, 0.0, 0.0, 1.0,
            0.0, 1.0, 0, 1, 0, 0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_shockwave_rejects_unnormalized_audio_before_cast() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="Shockwave"):
        native.render_shockwave_into(
            destination, 0.0, 1.0, 0.5, 120, -np.finfo(np.float64).max,
            1.0, 1, (0.0,) * 8, 0, 0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_aurora_rejects_nonfinite_derived_phase() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="Aurora phase"):
        native.render_aurora_into(
            destination, sys.maxsize, 0, None,
            np.finfo(np.float64).max, 1.0,
            0.0, 0.0, 0.0, 0.0, 1.0, 0, 0, 0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_spectrum_flash_rejects_negative_flash_strength() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="SpectrumFlash"):
        native.render_spectrum_flash_into(
            destination, (0.0,) * 8, 0.5, 0.0,
            -np.finfo(np.float64).max, 1.0, 0, 0,
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


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("base_intensity", [1e300, -1e300])
@pytest.mark.parametrize("strength", [1e300])
def test_native_screen_composition_saturates_finite_scalars_for_float(
    base_intensity: float, strength: float,
) -> None:
    native = kernels._native_module
    assert native is not None
    base = np.array([[200, 120, 5, 127]], dtype=np.uint8)
    overlay = np.array([[255, 20, 90, 170]], dtype=np.uint8)
    actual = np.empty_like(base)
    expected = np.empty_like(base)
    native.composite_screen_rgba_into(
        base, actual, base_intensity, overlay, strength, None, 0.0,
    )
    with np.errstate(over="ignore"):
        kernels._composite_screen_rgba_numpy(
            base, expected, base_intensity, overlay, strength, None, 0.0,
        )
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
@pytest.mark.parametrize("field", ["cooling", "sparking", "drive", "bass", "shimmer", "accent"])
@pytest.mark.parametrize("value", [-1e300, -0.01, 1.01, 1e300])
def test_native_fire_rejects_unbounded_values_before_float_conversions(
    field: str, value: float,
) -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((1, 4), dtype=np.uint8)
    heat = np.zeros(1, dtype=np.float32)
    next_heat = np.full_like(heat, np.nan)
    values = {
        "cooling": 0.5,
        "sparking": 0.35,
        "drive": 0.2,
        "bass": 0.1,
        "shimmer": 0.1,
        "accent": 0.0,
    }
    values[field] = value

    with pytest.raises(ValueError, match="normalized"):
        native.render_fire_into(
            destination, heat, next_heat, 0,
            values["cooling"], values["sparking"], values["drive"],
            values["bass"], values["shimmer"], values["accent"], 1.0,
        )
    with pytest.raises(ValueError, match="normalized"):
        kernels.render_fire_into(
            destination, heat, next_heat, 0,
            values["cooling"], values["sparking"], values["drive"],
            values["bass"], values["shimmer"], values["accent"], 1.0,
        )


@pytest.mark.skipif(kernels._native_module is None, reason="render extension is not built")
def test_native_fire_overwrites_uninitialized_scratch_before_reading_it() -> None:
    native = kernels._native_module
    assert native is not None
    destination = np.empty((2, 4), dtype=np.uint8)
    heat = np.zeros(2, dtype=np.float32)
    next_heat = np.full(2, np.nan, dtype=np.float32)

    native.render_fire_into(
        destination, heat, next_heat, 0,
        0.5, 0.35, 0.2, 0.1, 0.1, 0.0, 1.0,
    )

    assert np.isfinite(next_heat).all()
    assert np.all((next_heat >= 0.0) & (next_heat <= 1.0))


def test_screen_composition_accepts_read_only_overlay_frames() -> None:
    base = np.zeros((2, 4), dtype=np.uint8)
    overlay = np.full((2, 4), 255, dtype=np.uint8)
    overlay.flags.writeable = False
    destination = np.empty_like(base)

    composite_screen_rgba_into(base, destination, 1.0, overlay, 0.5)

    np.testing.assert_array_equal(destination[:, :3], np.full((2, 3), 127))
    np.testing.assert_array_equal(destination[:, 3], np.full(2, 255))
