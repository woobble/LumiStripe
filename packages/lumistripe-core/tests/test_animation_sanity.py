from __future__ import annotations

import numpy as np

from lumistripe import (
    AudioFrame,
    Aurora,
    BouncingBall,
    ColorWipe,
    Comet,
    Confetti,
    DualComet,
    Fire,
    PeakMirror,
    Pulse,
    Rainbow,
    RainbowCycle,
    Shockwave,
    Stripe,
    TheaterChase,
    Twinkle,
    Wave,
)


QUIET_AUDIO = AudioFrame(
    rms=0.01,
    bands=(0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01),
    beat=False,
    beat_strength=0.0,
)


PUNCH_AUDIO = AudioFrame(
    rms=0.88,
    bands=(0.92, 0.82, 0.64, 0.52, 0.42, 0.74, 0.82, 0.9),
    beat=True,
    beat_strength=1.0,
)


CHANGED_ANIMATIONS = [
    RainbowCycle(),
    Pulse(),
    Confetti(),
    Comet(),
    ColorWipe(),
    TheaterChase(),
    Aurora(),
    Fire(),
    PeakMirror(),
    Wave(),
    Twinkle(),
    BouncingBall(),
    DualComet(),
    Rainbow(),
    Shockwave(),
]


def _assert_valid_pixels(stripe: Stripe) -> None:
    pixels = stripe.pixels()
    assert pixels.shape[1] == 4
    assert int(pixels[:, 3].min()) >= 0
    assert int(pixels[:, 3].max()) <= 255


def test_changed_animations_render_on_small_strips() -> None:
    for animation in CHANGED_ANIMATIONS:
        stripe = Stripe(1)
        animation.tick(3, stripe)
        _assert_valid_pixels(stripe)

        stripe = Stripe(3)
        animation.tick_audio(5, stripe, QUIET_AUDIO)
        _assert_valid_pixels(stripe)

        stripe = Stripe(8)
        animation.tick_audio(11, stripe, PUNCH_AUDIO)
        _assert_valid_pixels(stripe)


def test_bouncing_ball_moves_in_manual_mode() -> None:
    stripe = Stripe(8)
    animation = BouncingBall()

    animation.tick(0, stripe)
    first = np.copy(stripe.pixels()[:, 3])
    animation.tick(1, stripe)
    second = np.copy(stripe.pixels()[:, 3])

    assert not np.array_equal(first, second)
