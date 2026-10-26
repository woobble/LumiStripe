from __future__ import annotations

import numpy as np
import pytest
from lumistripe import (
    AudioFrame,
    Bpm,
    CometStorm,
    Police,
    RedBlackoutStrobe,
    Stripe,
    Strobe,
    Twinkle,
)
from lumistripe.render import kernels


@pytest.mark.skipif(
    not kernels.native_solid_render_available()
    or not kernels.native_halves_render_available(),
    reason="render extension with solid/halves kernels is not built",
)
@pytest.mark.parametrize("animation_type", [Bpm, Police, RedBlackoutStrobe, Strobe])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_solid_animations_match_python(
    animation_type: type[Bpm | Police | RedBlackoutStrobe | Strobe],
    brightness: float,
    audio_mode: bool,
) -> None:
    settings = {
        "flash_rate": 71,
        "duty_cycle": 43,
        "intensity": 82,
        "palette": "neon",
        "custom_color": "#2EA7D4",
        "pattern": "double",
        "speed": 65,
        "decay": 42,
    }
    native = animation_type()
    python = animation_type()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((150, 4), dtype=np.uint8)
    stripe = Stripe(150)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6), beat=False),
        AudioFrame(rms=0.71, bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8), beat=True),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2), beat=False),
    )

    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        if isinstance(native, Bpm):
            assert native._flash == pytest.approx(python._flash)
            assert native._hue == python._hue
        if isinstance(native, RedBlackoutStrobe):
            assert native.flash_frames == python.flash_frames
            assert native._last_beat_frame == python._last_beat_frame


@pytest.mark.skipif(
    not kernels.native_comet_storm_available(),
    reason="render extension with CometStorm kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_comet_storm_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    settings = {
        "count": 6,
        "speed": 68,
        "tail_length": 17,
        "intensity": 78,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native = CometStorm()
    python = CometStorm()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.45, bands=(0.3, 0.4, 0.6, 0.7, 0.5, 0.4, 0.3, 0.2)),
        AudioFrame(rms=0.78, bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8), beat=True),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.phase == pytest.approx(python.phase)
        assert native.hue_phase == pytest.approx(python.hue_phase)


@pytest.mark.skipif(
    not kernels.native_twinkle_available(),
    reason="render extension with Twinkle kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_twinkle_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    settings = {
        "density": 68,
        "fade_time": 47,
        "particle_size": 52,
        "intensity": 78,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native = Twinkle()
    python = Twinkle()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.45, bands=(0.3, 0.4, 0.6, 0.7, 0.5, 0.4, 0.3, 0.2)),
        AudioFrame(rms=0.78, bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8), beat=True),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        np.testing.assert_array_equal(native.levels, python.levels)
        np.testing.assert_array_equal(native.hues, python.hues)
        assert native._bloom == pytest.approx(python._bloom)
