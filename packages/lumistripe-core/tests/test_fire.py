import numpy as np
from lumistripe import AudioFrame, Fire, Stripe

QUIET_AUDIO = AudioFrame(
    rms=0.02,
    bands=(0.01, 0.01, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01),
)

STRONG_AUDIO = AudioFrame(
    rms=0.9,
    bands=(0.9, 0.8, 0.6, 0.5, 0.4, 0.7, 0.8, 0.9),
    beat=True,
    beat_strength=1.0,
)


def _render(audio: AudioFrame) -> tuple[Fire, Stripe]:
    fire = Fire()
    stripe = Stripe(42)
    for frame in range(300):
        fire.tick_audio(frame, stripe, audio)
    return fire, stripe


def test_fire_does_not_saturate_during_quiet_audio() -> None:
    fire, stripe = _render(QUIET_AUDIO)
    rgb = stripe.pixels()[:, :3]

    assert float(fire.heat.max()) < 0.5
    assert np.unique(rgb, axis=0).shape[0] > 4
    assert np.any(rgb > 0)


def test_fire_stays_warm_and_varied_during_strong_audio() -> None:
    fire, stripe = _render(STRONG_AUDIO)
    rgb = stripe.pixels()[:, :3]

    assert np.all(rgb[:, 0] >= rgb[:, 1])
    assert np.all(rgb[:, 1] >= rgb[:, 2])
    assert int(rgb[:, 2].max()) <= 26
    assert np.unique(rgb, axis=0).shape[0] > 4
    assert np.count_nonzero(fire.heat >= 1.0) < stripe.length // 4
