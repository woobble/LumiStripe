import numpy as np
from lumistripe import AudioFrame, PeakMirror, Stripe

STRONG_AUDIO = AudioFrame(
    rms=0.9,
    bands=(0.9, 0.8, 0.6, 0.5, 0.4, 0.7, 0.8, 0.9),
    beat=True,
    beat_strength=1.0,
)


def test_peak_mirror_is_dark_during_silence() -> None:
    stripe = Stripe(42)

    PeakMirror().tick_audio(0, stripe, AudioFrame())

    assert not np.any(stripe.pixels())


def test_peak_mirror_audio_output_is_symmetric_and_colorful() -> None:
    stripe = Stripe(42)

    PeakMirror().tick_audio(0, stripe, STRONG_AUDIO)
    pixels = stripe.pixels()

    np.testing.assert_array_equal(pixels, pixels[::-1])
    assert not np.any(np.all(pixels[:, :3] == 255, axis=1))
    assert np.unique(pixels[:, :3], axis=0).shape[0] > 8
    assert pixels[20:22, 3].min() == pixels[:, 3].max()


def test_peak_mirror_non_audio_output_uses_hues_and_is_symmetric() -> None:
    stripe = Stripe(41)

    PeakMirror().tick(30, stripe)
    pixels = stripe.pixels()

    np.testing.assert_array_equal(pixels, pixels[::-1])
    assert np.any(pixels[:, 3] > 0)
    assert np.unique(pixels[:, :3], axis=0).shape[0] > 8
