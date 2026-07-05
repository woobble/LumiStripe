from lumistripe import AnimationPlayer, AudioFrame, RgbwTest, Stripe


def test_party_contains_expected_reactive_animations() -> None:
    player = AnimationPlayer.party()
    for name in (
        "aurora",
        "peak_mirror",
        "shockwave",
        "dual_comet",
        "club_flash",
        "color_burst",
        "disco_comet",
        "rave_scanner",
        "neon_confetti",
        "strobe_chase",
        "center_burst",
        "mirror_flash",
        "spectrum_flash",
        "drop_wave",
    ):
        assert player.index_of(name) is not None


def test_reactive_indices_exclude_utility_entries() -> None:
    player = AnimationPlayer()
    party = AnimationPlayer.party()
    player.add(party.animations[0].animation, 20, 120)
    player.add_utility(RgbwTest(), 20, 120)
    assert player.reactive_indices() == [0]


def test_player_uses_audio_snapshot() -> None:
    stripe = Stripe(10)
    player = AnimationPlayer.party()
    player.set_index(player.index_of("pulse") or 0)
    player.set_audio_snapshot(lambda: AudioFrame(rms=0.9, bands=(0.9, 0.8, 0.6, 0.5, 0.4, 0.7, 0.8, 0.9), beat=True, beat_strength=1.0))
    player.step(stripe)
    assert stripe.pixels()[:, 3].max() > 0
