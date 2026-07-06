import numpy as np
import pytest

from lumistripe import AudioFrame, AudioSnapshot, ColorWipe, MusicFeatures, Pulse, RainbowCycle, Stripe
from lumistripe.animation.reactive import AudioReactive


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


def test_audio_reactive_from_snapshot_uses_music_features() -> None:
    frame = AudioFrame(
        rms=0.9,
        bands=(0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2),
        beat=True,
        beat_strength=1.0,
        fresh=True,
    )
    features = MusicFeatures(
        bpm=132.0,
        energy=0.42,
        bass=0.6,
        brightness=0.7,
        onset_strength=0.8,
        beat=True,
        beat_strength=0.9,
        bands=(0.6, 0.6, 0.4, 0.4, 0.4, 0.7, 0.7, 0.7),
    )

    reactive = AudioReactive.from_snapshot(AudioSnapshot.from_parts(frame, features))

    assert reactive.rms == pytest.approx(0.42)
    assert reactive.low == pytest.approx(0.6)
    assert reactive.mid == pytest.approx(0.4)
    assert reactive.high == pytest.approx(0.7)
    assert reactive.onset == pytest.approx(0.8)
    assert reactive.brightness == pytest.approx(0.7)
    assert reactive.bpm == pytest.approx(132.0)
    assert reactive.activity() > 0.0


def test_audio_reactive_hit_helpers_detect_strong_events() -> None:
    reactive = AudioReactive(rms=0.5, accent=0.85, low=0.5, mid=0.2, high=0.65, onset=0.75)

    assert reactive.bass_hit()
    assert reactive.high_hit()
    assert reactive.drop_hit(beat=True)
    assert not reactive.drop_hit(beat=False)
