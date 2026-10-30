from __future__ import annotations

import numpy as np
import pytest
from lumistripe.render import kernels

_native = kernels._native_module
pytestmark = pytest.mark.skipif(_native is None, reason="native render extension is unavailable")


@pytest.mark.parametrize("length", [0, 1, 2, 3, 4, 150, 600])
@pytest.mark.parametrize("audio_mode", [0, 1])
@pytest.mark.parametrize("palette", [0, 1, 2, 3])
def test_aurora_dispatch_matches_scalar(
    length: int, audio_mode: int, palette: int
) -> None:
    assert _native is not None
    scalar = np.empty((length, 4), dtype=np.uint8)
    dispatched = np.empty_like(scalar)
    production = np.empty_like(scalar)
    bands = (0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)
    args = (37, audio_mode, bands, 71.0, 0.045, 0.12, 0.31, 0.57,
            0.35, 0.9, 23, palette, 0x2EA7D4)
    _native._benchmark_render_aurora_scalar(scalar, *args)
    _native._benchmark_render_aurora_candidate(dispatched, *args)
    _native.render_aurora_into(production, *args)
    np.testing.assert_array_equal(dispatched, scalar)
    np.testing.assert_array_equal(production, scalar)


@pytest.mark.parametrize("length", [0, 1, 2, 3, 4, 150, 600])
@pytest.mark.parametrize("audio_mode", [0, 1])
@pytest.mark.parametrize("palette", [0, 1, 2, 3])
def test_plasma_rave_dispatch_matches_scalar(
    length: int, audio_mode: int, palette: int
) -> None:
    assert _native is not None
    scalar = np.empty((length, 4), dtype=np.uint8)
    dispatched = np.empty_like(scalar)
    production = np.empty_like(scalar)
    args = (73.0, 0.23, 1.19, 0.41, 120, 80.0, 0.12, 0.9,
            palette, audio_mode, int(not audio_mode), 0x2EA7D4)
    _native._benchmark_render_plasma_rave_scalar(scalar, *args)
    _native._benchmark_render_plasma_rave_candidate(dispatched, *args)
    _native.render_plasma_rave_into(production, *args)
    np.testing.assert_array_equal(dispatched, scalar)
    np.testing.assert_array_equal(production, scalar)


@pytest.mark.parametrize("length", [0, 1, 2, 3, 4, 150, 600])
@pytest.mark.parametrize("audio_mode", [0, 1])
@pytest.mark.parametrize("palette", [0, 1, 2, 3])
@pytest.mark.parametrize(
    "center, radius_or_offset, width, alpha_scale, brightness",
    [(299.5, 84.0, 46.0, 0.83, 0.91), (-20.25, 301.75, 0.125, 2.4, 0.0),
     (8192.0, -900.0, 333.3, 0.001, 1.0)],
)
def test_beat_ripple_dispatch_matches_scalar(
    length: int, audio_mode: int, palette: int, center: float,
    radius_or_offset: float, width: float, alpha_scale: float,
    brightness: float,
) -> None:
    assert _native is not None
    scalar = np.empty((length, 4), dtype=np.uint8)
    dispatched = np.empty_like(scalar)
    production = np.empty_like(scalar)
    args = (audio_mode, center, radius_or_offset, width, alpha_scale,
            brightness, 145, 58, palette, 0x2EA7D4)
    _native._benchmark_render_beat_ripple_scalar(scalar, *args)
    _native._benchmark_render_beat_ripple_candidate(dispatched, *args)
    _native.render_beat_ripple_into(production, *args)
    np.testing.assert_array_equal(dispatched, scalar)
    np.testing.assert_array_equal(production, scalar)
