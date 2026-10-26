from __future__ import annotations

import numpy as np
import pytest
from lumistripe import AudioFrame, SpectrumFlash, Stripe
from lumistripe.render import kernels

_BANDS = (
    AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
    AudioFrame(
        rms=0.96,
        bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28),
        beat=True,
        beat_strength=0.94,
    ),
    AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
)


def _configured(palette: str) -> SpectrumFlash:
    animation = SpectrumFlash()
    animation.configure(
        {
            "width": 74,
            "decay": 38,
            "intensity": 83,
            "palette": palette,
            "custom_color": "#2EA7D4",
            "music_reactivity": 100,
        }
    )
    return animation


@pytest.mark.skipif(
    not kernels.native_spectrum_flash_available(),
    reason="render extension with SpectrumFlash kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True], ids=["static", "audio"])
def test_native_spectrum_flash_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = _configured(palette)
    python = _configured(palette)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)

    for frame, audio in enumerate(_BANDS):
        configured_audio = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured_audio, brightness)

        configured_audio = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured_audio is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured_audio)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.hue_phase == pytest.approx(python.hue_phase)
        assert native.flash.value == pytest.approx(python.flash.value)


def test_native_hook_declines_without_changing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lumistripe.effects.spectrum_flash.native_spectrum_flash_available",
        lambda: False,
    )
    animation = _configured("rainbow")
    destination = np.full((16, 4), 123, dtype=np.uint8)
    previous = destination.copy()

    assert not animation._render_native(2, destination, _BANDS[0], 0.7)

    np.testing.assert_array_equal(destination, previous)
    assert animation.hue_phase == 0.0
    assert animation.flash.value == 0.0
