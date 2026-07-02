import numpy as np

from lumistripe import AudioFrame, ColorWipe, Pulse, RainbowCycle, Stripe


TEST_AUDIO = AudioFrame(
    rms=0.9,
    bands=(0.9, 0.8, 0.6, 0.5, 0.4, 0.7, 0.8, 0.9),
    beat=True,
    beat_strength=1.0,
)


def test_rainbow_cycle_audio_preserves_full_opacity() -> None:
    stripe = Stripe(16)
    animation = RainbowCycle()

    animation.tick_audio(24, stripe, TEST_AUDIO)

    np.testing.assert_array_equal(stripe.pixels()[:, 3], np.full(16, 255, dtype=np.uint8))


def test_color_wipe_audio_keeps_lit_pixels_fully_opaque() -> None:
    stripe = Stripe(16)
    animation = ColorWipe()

    animation.tick_audio(30, stripe, TEST_AUDIO)

    lit = stripe.pixels()[:, 3] > 0
    assert lit.any()
    np.testing.assert_array_equal(stripe.pixels()[lit, 3], np.full(int(lit.sum()), 255, dtype=np.uint8))


def test_pulse_audio_stays_in_manual_alpha_range() -> None:
    stripe = Stripe(8)
    animation = Pulse()

    animation.tick_audio(18, stripe, TEST_AUDIO)

    alpha = stripe.pixels()[:, 3]
    assert alpha.min() >= 51
    assert alpha.max() <= 255
