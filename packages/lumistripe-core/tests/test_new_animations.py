import numpy as np

from lumistripe import (
    AudioFrame,
    BassDrop,
    BeatExplosion,
    BeatTunnel,
    CometStorm,
    DiscoSparkle,
    DropExplosion,
    FireworkBurst,
    LaserSweep,
    LightningStrike,
    PlasmaRave,
    Stripe,
)


TEST_AUDIO = AudioFrame(
    rms=0.9,
    bands=(0.9, 0.8, 0.6, 0.5, 0.4, 0.7, 0.8, 0.9),
    beat=True,
    beat_strength=1.0,
)


QUIET_AUDIO = AudioFrame(
    rms=0.02,
    bands=(0.01, 0.01, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01),
    beat=False,
    beat_strength=0.0,
)


def test_beat_explosion_name() -> None:
    assert BeatExplosion().name == "beat_explosion"


def test_beat_explosion_produces_pixels_on_beat() -> None:
    stripe = Stripe(24)
    anim = BeatExplosion()
    anim.tick_audio(10, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_beat_explosion_non_audio_produces_pixels() -> None:
    stripe = Stripe(24)
    anim = BeatExplosion()
    anim.tick(31, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_bass_drop_name() -> None:
    assert BassDrop().name == "bass_drop"


def test_bass_drop_tension_builds_then_flashes_on_beat() -> None:
    stripe = Stripe(16)
    anim = BassDrop()
    anim.tick_audio(0, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_bass_drop_non_audio_produces_pixels() -> None:
    stripe = Stripe(16)
    anim = BassDrop()
    anim.tick(61, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_firework_burst_name() -> None:
    assert FireworkBurst().name == "firework_burst"


def test_firework_burst_produces_pixels_on_beat() -> None:
    stripe = Stripe(40)
    anim = FireworkBurst()
    anim.tick_audio(5, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_firework_burst_non_audio_produces_pixels() -> None:
    stripe = Stripe(40)
    anim = FireworkBurst()
    anim.tick(5, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_laser_sweep_name() -> None:
    assert LaserSweep().name == "laser_sweep"


def test_laser_sweep_produces_pixels() -> None:
    stripe = Stripe(32)
    anim = LaserSweep()
    anim.tick_audio(20, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_laser_sweep_non_audio_produces_pixels() -> None:
    stripe = Stripe(32)
    anim = LaserSweep()
    anim.tick(20, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_laser_sweep_beam_is_narrow() -> None:
    stripe = Stripe(32)
    anim = LaserSweep()
    anim.tick(10, stripe)
    lit = np.sum(stripe.pixels()[:, 3] > 0)
    assert lit <= 5


def test_plasma_rave_name() -> None:
    assert PlasmaRave().name == "plasma_rave"


def test_plasma_rave_produces_pixels() -> None:
    stripe = Stripe(30)
    anim = PlasmaRave()
    anim.tick_audio(50, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_plasma_rave_non_audio_produces_pixels() -> None:
    stripe = Stripe(30)
    anim = PlasmaRave()
    anim.tick(50, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_comet_storm_name() -> None:
    assert CometStorm().name == "comet_storm"


def test_comet_storm_produces_pixels() -> None:
    stripe = Stripe(40)
    anim = CometStorm()
    anim.tick_audio(30, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_comet_storm_non_audio_produces_pixels() -> None:
    stripe = Stripe(40)
    anim = CometStorm()
    anim.tick(30, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_beat_tunnel_name() -> None:
    assert BeatTunnel().name == "beat_tunnel"


def test_beat_tunnel_produces_pixels() -> None:
    stripe = Stripe(40)
    anim = BeatTunnel()
    anim.tick_audio(20, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_beat_tunnel_non_audio_produces_pixels() -> None:
    stripe = Stripe(40)
    anim = BeatTunnel()
    anim.tick(20, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_lightning_strike_name() -> None:
    assert LightningStrike().name == "lightning_strike"


def test_lightning_strike_produces_pixels_on_beat() -> None:
    stripe = Stripe(40)
    anim = LightningStrike()
    anim.tick_audio(10, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_lightning_strike_non_audio_produces_pixels() -> None:
    stripe = Stripe(40)
    anim = LightningStrike()
    anim.tick(45, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_lightning_strike_flash_decays_over_time() -> None:
    stripe = Stripe(40)
    anim = LightningStrike()
    anim.tick_audio(0, stripe, TEST_AUDIO)
    bright_after_trigger = stripe.pixels()[:, 3].max()
    anim.tick_audio(20, stripe, QUIET_AUDIO)
    dim_after_decay = stripe.pixels()[:, 3].max()
    assert dim_after_decay <= bright_after_trigger


def test_disco_sparkle_name() -> None:
    assert DiscoSparkle().name == "disco_sparkle"


def test_disco_sparkle_produces_pixels() -> None:
    stripe = Stripe(40)
    anim = DiscoSparkle()
    anim.tick_audio(10, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_disco_sparkle_non_audio_produces_pixels() -> None:
    stripe = Stripe(40)
    anim = DiscoSparkle()
    anim.tick(10, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_disco_sparkle_has_dense_pattern() -> None:
    stripe = Stripe(40)
    anim = DiscoSparkle()
    for i in range(5):
        anim.tick_audio(i, stripe, TEST_AUDIO)
    lit = np.sum(stripe.pixels()[:, 3] > 0)
    assert lit > 10


def test_drop_explosion_name() -> None:
    assert DropExplosion().name == "drop_explosion"


def test_drop_explosion_produces_pixels() -> None:
    stripe = Stripe(30)
    anim = DropExplosion()
    anim.tick_audio(30, stripe, TEST_AUDIO)
    assert stripe.pixels()[:, 3].max() > 0


def test_drop_explosion_non_audio_produces_pixels() -> None:
    stripe = Stripe(30)
    anim = DropExplosion()
    anim.tick(80, stripe)
    assert stripe.pixels()[:, 3].max() > 0


def test_drop_explosion_produces_bright_flash_near_drop() -> None:
    stripe = Stripe(30)
    anim = DropExplosion()
    for i in range(80):
        anim.tick(i, stripe)
    assert stripe.pixels()[:, 3].max() > 0.5 * 255
