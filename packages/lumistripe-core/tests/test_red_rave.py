from __future__ import annotations

import numpy as np
import pytest
from lumistripe import (
    AudioFrame,
    RedBlackoutStrobe,
    RedRaveChase,
    RedRaveSweep,
    Stripe,
)


def _assert_red_and_black_only(stripe: Stripe) -> None:
    pixels = stripe.pixels()
    assert set(np.unique(pixels[:, 0])).issubset({0, 255})
    assert np.all(pixels[:, 1] == 0)
    assert np.all(pixels[:, 2] == 0)


@pytest.mark.parametrize(
    "animation",
    (RedRaveSweep(), RedRaveChase(), RedBlackoutStrobe()),
)
@pytest.mark.parametrize("length", (1, 3, 41))
def test_red_rave_animations_render_safely_on_different_strip_lengths(
    animation: RedRaveSweep | RedRaveChase | RedBlackoutStrobe,
    length: int,
) -> None:
    stripe = Stripe(length)

    animation.tick(0, stripe)

    _assert_red_and_black_only(stripe)


@pytest.mark.parametrize("animation", (RedRaveSweep(), RedRaveChase()))
def test_red_segment_animations_move_the_lit_sections(
    animation: RedRaveSweep | RedRaveChase,
) -> None:
    stripe = Stripe(41)
    animation.tick(0, stripe)
    first = stripe.pixels().copy()

    for frame in range(1, 7):
        animation.tick(frame, stripe)

    assert not np.array_equal(first, stripe.pixels())
    assert np.any(stripe.pixels()[:, 0] == 255)
    assert np.any(stripe.pixels()[:, 0] == 0)
    _assert_red_and_black_only(stripe)


def test_red_blackout_strobe_flashes_for_two_frames_per_beat() -> None:
    stripe = Stripe(12)
    animation = RedBlackoutStrobe()
    beat = AudioFrame(beat=True, beat_strength=1.0)

    animation.tick_audio(0, stripe, beat)
    assert np.all(stripe.pixels()[:, 0] == 255)

    animation.tick_audio(1, stripe, AudioFrame())
    assert np.all(stripe.pixels()[:, 0] == 255)

    animation.tick_audio(2, stripe, AudioFrame())
    assert np.all(stripe.pixels()[:, :3] == 0)
