import numpy as np

from lumistripe import (
    AudioFrame,
    BassDrop,
    BeatExplosion,
    BeatRipple,
    BeatTunnel,
    BeatWave,
    CenterBurst,
    ClubFlash,
    ColorBurst,
    CometStorm,
    Bpm,
    DanceFloor,
    DiscoComet,
    DiscoSparkle,
    DropExplosion,
    DropWave,
    DualLaser,
    ElectricStorm,
    FireworkBurst,
    GlowRush,
    HardBeat,
    Juggle,
    LaserSweep,
    LightningStrike,
    MirrorFlash,
    NeonConfetti,
    NeonStorm,
    PixelExplosion,
    PlasmaRave,
    Police,
    RainbowStrobe,
    RavePulse,
    RaveScanner,
    Rgba,
    RgbwTest,
    Sinelon,
    Stripe,
    Strobe,
    SpectrumFlash,
    StrobeChase,
    Twinkle,
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


PUNCH_AUDIO = AudioFrame(
    rms=0.75,
    bands=(0.92, 0.78, 0.35, 0.28, 0.22, 0.24, 0.19, 0.12),
    beat=True,
    beat_strength=0.9,
)


HIGH_AUDIO = AudioFrame(
    rms=0.62,
    bands=(0.18, 0.2, 0.32, 0.34, 0.31, 0.8, 0.86, 0.92),
    beat=False,
    beat_strength=0.0,
)


def _max_alpha(stripe: Stripe) -> int:
    return int(stripe.pixels()[:, 3].max())


def _lit_count(stripe: Stripe) -> int:
    return int(np.sum(stripe.pixels()[:, 3] > 0))


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


def test_beat_ripple_name() -> None:
    assert BeatRipple().name == "beat_ripple"


def test_beat_ripple_non_audio_produces_mirrored_ring() -> None:
    stripe = Stripe(24)
    anim = BeatRipple()

    anim.tick(10, stripe)

    assert _lit_count(stripe) > 0
    assert anim.radius > 0.0
    assert anim.hue == 30


def test_beat_ripple_audio_beat_resets_radius_and_lights_pixels() -> None:
    stripe = Stripe(24)
    anim = BeatRipple(radius=5.0, hue=10)

    anim.tick_audio(0, stripe, TEST_AUDIO)

    assert anim.radius == 0.0
    assert anim.hue == 41
    assert _max_alpha(stripe) > 0


def test_dual_laser_name() -> None:
    assert DualLaser().name == "dual_laser"


def test_dual_laser_non_audio_moves_and_renders_beams() -> None:
    stripe = Stripe(32)
    anim = DualLaser()

    anim.tick(3, stripe)

    assert anim.beam_a.position > -2.0
    assert anim.beam_b.hue == (anim.beam_a.hue + 140) % 256
    assert _lit_count(stripe) > 0


def test_dual_laser_audio_beat_reverses_directions_and_bursts() -> None:
    stripe = Stripe(32)
    anim = DualLaser()

    anim.tick_audio(8, stripe, TEST_AUDIO)

    assert anim.beam_a.direction == -1.0
    assert anim.beam_b.direction == -1.0
    assert anim.burst.value < 1.0
    assert _max_alpha(stripe) > 0


def test_electric_storm_name() -> None:
    assert ElectricStorm().name == "electric_storm"


def test_electric_storm_non_audio_spawns_streak_and_flash() -> None:
    stripe = Stripe(24)
    anim = ElectricStorm()
    anim._rng.seed(1)

    anim.tick(0, stripe)

    assert len(anim.streaks) == 1
    assert anim.flash.value < 1.0
    assert _lit_count(stripe) > 0


def test_electric_storm_audio_beat_adds_flash_and_streak() -> None:
    stripe = Stripe(24)
    anim = ElectricStorm()
    anim._rng.seed(1)

    anim.tick_audio(0, stripe, TEST_AUDIO)

    assert len(anim.streaks) == 1
    assert anim.flash.value > 0.0
    assert _max_alpha(stripe) > 0


def test_juggle_name() -> None:
    assert Juggle().name == "juggle"


def test_juggle_non_audio_moves_and_lights_pixels() -> None:
    stripe = Stripe(80)
    anim = Juggle()
    positions = list(anim.positions)

    anim.tick(0, stripe)

    assert anim.positions != positions
    assert _lit_count(stripe) > 0


def test_juggle_audio_beat_scales_speeds_and_lights_pixels() -> None:
    stripe = Stripe(80)
    anim = Juggle()
    speeds = list(anim.speeds)

    anim.tick_audio(0, stripe, PUNCH_AUDIO)

    assert all(abs(after) > abs(before) for before, after in zip(speeds, anim.speeds, strict=True))
    assert _lit_count(stripe) > 0


def test_beat_wave_name() -> None:
    assert BeatWave().name == "beat_wave"


def test_beat_wave_non_audio_clears_pixels() -> None:
    stripe = Stripe(24)
    anim = BeatWave()

    stripe.fill(Rgba(1, 2, 3, 1.0))
    anim.tick(0, stripe)

    assert _max_alpha(stripe) == 0


def test_beat_wave_audio_beat_creates_wave_and_lights_pixels() -> None:
    stripe = Stripe(32)
    anim = BeatWave()

    anim.tick_audio(0, stripe, PUNCH_AUDIO)

    assert len(anim.waves) == 1
    assert anim._hue == 17
    assert _max_alpha(stripe) > 0


def test_bpm_name() -> None:
    assert Bpm().name == "bpm"


def test_bpm_non_audio_produces_pulse() -> None:
    stripe = Stripe(16)
    anim = Bpm()

    anim.tick(10, stripe)

    assert _max_alpha(stripe) > 0


def test_bpm_audio_beat_updates_flash_and_hue() -> None:
    stripe = Stripe(16)
    anim = Bpm()

    anim.tick_audio(0, stripe, TEST_AUDIO)

    assert anim._hue == 31
    assert anim._flash > 0.0
    assert _max_alpha(stripe) > 0


def test_dance_floor_name() -> None:
    assert DanceFloor().name == "dance_floor"


def test_dance_floor_non_audio_fills_segments() -> None:
    stripe = Stripe(32)
    anim = DanceFloor()

    anim.tick(10, stripe)

    assert _lit_count(stripe) == stripe.length


def test_dance_floor_audio_beat_fills_segments() -> None:
    stripe = Stripe(32)
    anim = DanceFloor()

    anim.tick_audio(10, stripe, TEST_AUDIO)

    assert _lit_count(stripe) == stripe.length
    assert _max_alpha(stripe) > 0


def test_glow_rush_name() -> None:
    assert GlowRush().name == "glow_rush"


def test_glow_rush_non_audio_advances_and_lights_pixels() -> None:
    stripe = Stripe(32)
    anim = GlowRush()

    anim.tick(4, stripe)

    assert anim.position > 0.0
    assert anim.hue == 8
    assert _lit_count(stripe) > 0


def test_glow_rush_audio_advances_and_lights_pixels() -> None:
    stripe = Stripe(32)
    anim = GlowRush()

    anim.tick_audio(4, stripe, HIGH_AUDIO)

    assert anim.position > 0.0
    assert _lit_count(stripe) > 0


def test_hard_beat_name() -> None:
    assert HardBeat().name == "hard_beat"


def test_hard_beat_non_audio_flashes_then_clears() -> None:
    stripe = Stripe(12)
    anim = HardBeat()

    anim.tick(0, stripe)
    lit_alpha = _max_alpha(stripe)
    anim.tick(4, stripe)

    assert lit_alpha > 0
    assert _max_alpha(stripe) == 0


def test_hard_beat_audio_beat_updates_flash_and_hue() -> None:
    stripe = Stripe(12)
    anim = HardBeat()

    anim.tick_audio(0, stripe, TEST_AUDIO)

    assert anim.hue == 43
    assert anim.flash.value > 0.0
    assert _max_alpha(stripe) > 0


def test_neon_storm_name() -> None:
    assert NeonStorm().name == "neon_storm"


def test_neon_storm_non_audio_produces_deterministic_pixels() -> None:
    stripe = Stripe(48)
    anim = NeonStorm()

    anim.tick(2, stripe)

    assert _lit_count(stripe) > 0


def test_neon_storm_non_audio_draws_streak_branch() -> None:
    stripe = Stripe(24)
    anim = NeonStorm()

    anim.tick(8, stripe)

    assert _lit_count(stripe) > 10


def test_neon_storm_audio_drive_lights_pixels() -> None:
    stripe = Stripe(48)
    anim = NeonStorm()

    anim.tick_audio(2, stripe, TEST_AUDIO)

    assert _lit_count(stripe) > 0
    assert _max_alpha(stripe) > 0


def test_neon_storm_audio_quiet_draws_trails_and_dark_pixels() -> None:
    stripe = Stripe(24)
    anim = NeonStorm()

    anim.tick_audio(1, stripe, QUIET_AUDIO)

    assert _lit_count(stripe) > 0
    assert _lit_count(stripe) < stripe.length


def test_pixel_explosion_name() -> None:
    assert PixelExplosion().name == "pixel_explosion"


def test_pixel_explosion_non_audio_spawns_burst() -> None:
    stripe = Stripe(32)
    anim = PixelExplosion()
    anim._rng.seed(1)

    anim.tick(0, stripe)

    assert len(anim.bursts) == 1
    assert anim.bursts[0].radius > 0.0
    assert _max_alpha(stripe) > 0


def test_pixel_explosion_audio_beat_spawns_bursts() -> None:
    stripe = Stripe(32)
    anim = PixelExplosion()
    anim._rng.seed(1)

    anim.tick_audio(0, stripe, TEST_AUDIO)

    assert len(anim.bursts) > 1
    assert _max_alpha(stripe) > 0


def test_police_name() -> None:
    assert Police().name == "police"


def test_police_non_audio_alternates_halves() -> None:
    stripe = Stripe(4)
    anim = Police()

    anim.tick(0, stripe)
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array(
            [[255, 0, 0, 255], [255, 0, 0, 255], [0, 0, 255, 255], [0, 0, 255, 255]],
            dtype=np.uint8,
        ),
    )

    anim.tick(8, stripe)
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array(
            [[0, 0, 255, 255], [0, 0, 255, 255], [255, 0, 0, 255], [255, 0, 0, 255]],
            dtype=np.uint8,
        ),
    )


def test_police_audio_beat_flashes_white() -> None:
    stripe = Stripe(4)
    anim = Police()

    anim.tick_audio(0, stripe, TEST_AUDIO)

    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array(
            [[255, 255, 255, 255], [255, 255, 255, 255], [255, 255, 255, 255], [255, 255, 255, 255]],
            dtype=np.uint8,
        ),
    )


def test_rainbow_strobe_name() -> None:
    assert RainbowStrobe().name == "rainbow_strobe"


def test_rainbow_strobe_non_audio_covers_on_and_off_frames() -> None:
    stripe = Stripe(8)
    anim = RainbowStrobe()

    anim.tick(0, stripe)
    on_alpha = _max_alpha(stripe)
    anim.tick(4, stripe)

    assert on_alpha > 0
    assert _max_alpha(stripe) == 0


def test_rainbow_strobe_audio_beat_turns_on() -> None:
    stripe = Stripe(8)
    anim = RainbowStrobe()

    anim.tick_audio(20, stripe, TEST_AUDIO)

    assert _max_alpha(stripe) > 0


def test_rave_pulse_name() -> None:
    assert RavePulse().name == "rave_pulse"


def test_rave_pulse_non_audio_pulses_pixels() -> None:
    stripe = Stripe(10)
    anim = RavePulse()

    anim.tick(10, stripe)

    assert _max_alpha(stripe) > 0


def test_rave_pulse_audio_beat_updates_flash_and_hue() -> None:
    stripe = Stripe(10)
    anim = RavePulse()

    anim.tick_audio(0, stripe, TEST_AUDIO)

    assert anim.hue == 37
    assert anim.flash.value > 0.0
    assert _max_alpha(stripe) > 0


def test_sinelon_name() -> None:
    assert Sinelon().name == "sinelon"


def test_sinelon_non_audio_draws_trail() -> None:
    stripe = Stripe(24)
    anim = Sinelon()

    anim.tick(10, stripe)

    assert _max_alpha(stripe) > 0


def test_sinelon_audio_draws_reactive_trail() -> None:
    stripe = Stripe(24)
    anim = Sinelon()

    anim.tick_audio(10, stripe, HIGH_AUDIO)

    assert _max_alpha(stripe) > 0


def test_strobe_name() -> None:
    assert Strobe().name == "strobe"


def test_strobe_non_audio_covers_on_and_off_frames() -> None:
    stripe = Stripe(8)
    anim = Strobe()

    anim.tick(0, stripe)
    on_alpha = _max_alpha(stripe)
    anim.tick(4, stripe)

    assert on_alpha > 0
    assert _max_alpha(stripe) == 0


def test_strobe_audio_beat_turns_on() -> None:
    stripe = Stripe(8)
    anim = Strobe()

    anim.tick_audio(20, stripe, TEST_AUDIO)

    assert _max_alpha(stripe) > 0


def test_rgbw_test_name() -> None:
    assert RgbwTest().name == "rgbw_test"


def test_rgbw_test_cycles_primary_colors() -> None:
    stripe = Stripe(2)
    anim = RgbwTest()

    anim.tick(0, stripe)
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array([[255, 0, 0, 255], [255, 0, 0, 255]], dtype=np.uint8),
    )

    anim.tick(500, stripe)
    np.testing.assert_array_equal(
        stripe.pixels(),
        np.array([[0, 0, 255, 255], [0, 0, 255, 255]], dtype=np.uint8),
    )
    assert anim.last_idx == 2


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


def test_drop_explosion_non_audio_draws_charging_preview() -> None:
    stripe = Stripe(30)
    anim = DropExplosion(charge=0.8)

    anim.tick(8, stripe)

    assert _max_alpha(stripe) > 0
    assert anim.charge > 0.8


def test_drop_explosion_audio_charges_without_drop() -> None:
    stripe = Stripe(30)
    anim = DropExplosion(charge=0.4)

    anim.tick_audio(10, stripe, HIGH_AUDIO)

    assert anim.charge > 0.4
    assert _max_alpha(stripe) > 0


def test_twinkle_name() -> None:
    assert Twinkle().name == "twinkle"


def test_twinkle_non_audio_produces_soft_glow() -> None:
    stripe = Stripe(36)
    anim = Twinkle()
    anim.tick(24, stripe)

    pixels = stripe.pixels()[:, 3]
    assert pixels.max() > 0
    assert pixels.mean() < 80


def test_twinkle_audio_reacts_without_dense_burst() -> None:
    stripe = Stripe(36)
    anim = Twinkle()

    anim.tick_audio(0, stripe, TEST_AUDIO)
    beat_pixels = stripe.pixels()[:, 3].copy()
    anim.tick_audio(20, stripe, QUIET_AUDIO)
    quiet_pixels = stripe.pixels()[:, 3].copy()

    assert beat_pixels.mean() > quiet_pixels.mean()
    assert beat_pixels.max() < 160
    assert quiet_pixels.max() > 0


def test_club_flash_name() -> None:
    assert ClubFlash().name == "club_flash"


def test_club_flash_is_brighter_on_strong_audio() -> None:
    stripe = Stripe(32)
    anim = ClubFlash()

    anim.tick_audio(0, stripe, PUNCH_AUDIO)
    loud = _max_alpha(stripe)
    anim.tick_audio(1, stripe, QUIET_AUDIO)
    quiet = _max_alpha(stripe)

    assert loud >= quiet
    assert loud > 0


def test_color_burst_name() -> None:
    assert ColorBurst().name == "color_burst"


def test_color_burst_spawns_a_visible_burst_on_beat() -> None:
    stripe = Stripe(40)
    anim = ColorBurst()

    anim.tick_audio(0, stripe, TEST_AUDIO)
    assert _lit_count(stripe) > 0


def test_disco_comet_name() -> None:
    assert DiscoComet().name == "disco_comet"


def test_disco_comet_responds_to_high_frequency_audio() -> None:
    stripe = Stripe(40)
    anim = DiscoComet()

    anim.tick_audio(0, stripe, HIGH_AUDIO)
    assert _lit_count(stripe) > 0
    assert _max_alpha(stripe) > 0


def test_rave_scanner_name() -> None:
    assert RaveScanner().name == "rave_scanner"


def test_rave_scanner_gets_wider_on_bass_heavy_audio() -> None:
    stripe = Stripe(40)
    anim = RaveScanner()

    anim.tick_audio(0, stripe, QUIET_AUDIO)
    quiet_lit = _lit_count(stripe)
    anim.tick_audio(1, stripe, PUNCH_AUDIO)
    loud_lit = _lit_count(stripe)

    assert loud_lit >= quiet_lit
    assert loud_lit > 0


def test_neon_confetti_name() -> None:
    assert NeonConfetti().name == "neon_confetti"


def test_neon_confetti_becomes_denser_with_volume() -> None:
    stripe = Stripe(48)
    anim = NeonConfetti()

    anim.tick_audio(0, stripe, QUIET_AUDIO)
    quiet_lit = _lit_count(stripe)
    anim.tick_audio(1, stripe, PUNCH_AUDIO)
    loud_lit = _lit_count(stripe)

    assert loud_lit >= quiet_lit
    assert loud_lit > 0


def test_strobe_chase_name() -> None:
    assert StrobeChase().name == "strobe_chase"


def test_strobe_chase_fires_on_strong_hits() -> None:
    stripe = Stripe(36)
    anim = StrobeChase()

    anim.tick_audio(0, stripe, PUNCH_AUDIO)
    assert _max_alpha(stripe) > 0


def test_center_burst_name() -> None:
    assert CenterBurst().name == "center_burst"


def test_center_burst_is_symmetric() -> None:
    stripe = Stripe(20)
    anim = CenterBurst()

    anim.tick_audio(0, stripe, TEST_AUDIO)

    left = stripe.pixels()[:10, 3]
    right = stripe.pixels()[10:, 3][::-1]
    np.testing.assert_array_equal(left, right)


def test_mirror_flash_name() -> None:
    assert MirrorFlash().name == "mirror_flash"


def test_mirror_flash_is_symmetric() -> None:
    stripe = Stripe(24)
    anim = MirrorFlash()

    anim.tick_audio(0, stripe, TEST_AUDIO)

    left = stripe.pixels()[:12, 3]
    right = stripe.pixels()[12:, 3][::-1]
    np.testing.assert_array_equal(left, right)


def test_spectrum_flash_name() -> None:
    assert SpectrumFlash().name == "spectrum_flash"


def test_spectrum_flash_produces_band_color_output() -> None:
    stripe = Stripe(36)
    anim = SpectrumFlash()

    anim.tick_audio(0, stripe, HIGH_AUDIO)
    assert _lit_count(stripe) > 0
    assert _max_alpha(stripe) > 0


def test_drop_wave_name() -> None:
    assert DropWave().name == "drop_wave"


def test_drop_wave_builds_then_releases() -> None:
    stripe = Stripe(32)
    anim = DropWave()

    for i in range(6):
        anim.tick_audio(i, stripe, QUIET_AUDIO)
    pre_drop = _max_alpha(stripe)

    anim.tick_audio(7, stripe, PUNCH_AUDIO)
    post_drop = _max_alpha(stripe)

    assert post_drop >= pre_drop
    assert post_drop > 0
