from __future__ import annotations

from lumistripe import (
    AudioFrame,
    AudioSnapshot,
    CycleOrder,
    CycleTiming,
    CyclingConfig,
    MusicActivityConfig,
    MusicActivityDetector,
    MusicFeatures,
    PlaybackConfig,
    PlaybackEngine,
    PlaybackMode,
    Rgb,
    RgbwTest,
    Stripe,
)
from lumistripe.animation import AnimationPlayer


class CountingStripe(Stripe):
    def __init__(self, length: int) -> None:
        super().__init__(length)
        self.flush_calls = 0

    def flush(self) -> None:
        self.flush_calls += 1
        super().flush()


def _active_snapshot() -> AudioSnapshot:
    frame = AudioFrame(
        rms=0.8,
        bands=(0.9, 0.8, 0.5, 0.4, 0.3, 0.4, 0.5, 0.3),
        beat=True,
        beat_strength=0.9,
        fresh=True,
    )
    features = MusicFeatures(
        energy=0.8,
        volume=0.8,
        energy_level=0.8,
        bass=0.85,
        bass_energy=0.85,
        mid_energy=0.4,
        treble_energy=0.3,
        brightness=0.3,
        onset_strength=0.7,
        beat=True,
        beat_strength=0.9,
        beat_confidence=0.9,
    )
    return AudioSnapshot.from_parts(frame, features)


def test_static_holds_animation_past_authored_cycle() -> None:
    player = AnimationPlayer.party()
    engine = PlaybackEngine(player)
    stripe = Stripe(8)
    original = player.current_index()
    player.frame = player.animations[original].frames_per_cycle

    engine.step(stripe, now_s=10.0)

    assert player.current_index() == original


def test_static_can_render_with_optional_audio() -> None:
    player = AnimationPlayer.party()
    engine = PlaybackEngine(player)

    engine.step(Stripe(8), snapshot=_active_snapshot(), now_s=0.0)

    assert player.audio_enabled is True


def test_solid_mode_fills_every_pixel_with_selected_color_and_brightness() -> None:
    player = AnimationPlayer.party()
    player.set_brightness(0.5)
    engine = PlaybackEngine(
        player,
        PlaybackConfig(mode=PlaybackMode.SOLID, solid_color=Rgb(20, 40, 60)),
    )
    stripe = CountingStripe(4)

    delay = engine.step(stripe, now_s=0.0)

    assert stripe.pixels().tolist() == [[20, 40, 60, 127]] * 4
    assert stripe.flush_calls == 1
    assert delay == 0.05
    assert player.audio_enabled is False


def test_solid_color_can_be_changed_without_rebuilding_playback() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(), PlaybackConfig(mode=PlaybackMode.SOLID)
    )
    stripe = Stripe(2)

    engine.set_solid_color(Rgb(1, 2, 3))
    engine.step(stripe, now_s=0.0)

    assert stripe.pixels().tolist() == [[1, 2, 3, 255]] * 2


def test_manual_selection_switches_to_static() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(), PlaybackConfig(mode=PlaybackMode.DYNAMIC)
    )

    engine.select_animation("pulse")

    assert engine.mode is PlaybackMode.STATIC
    assert engine.player.name_at(engine.player.current_index()) == "pulse"


def test_cycling_uses_authored_duration_and_skips_utility() -> None:
    player = AnimationPlayer()
    party = AnimationPlayer.party()
    player.add(party.animations[0].animation, 20, 2)
    player.add_utility(RgbwTest(), 20, 1)
    player.add(party.animations[1].animation, 20, 2)
    engine = PlaybackEngine(player, PlaybackConfig(mode=PlaybackMode.CYCLING))
    stripe = Stripe(8)

    engine.step(stripe, now_s=0.0)
    engine.step(stripe, now_s=0.1)
    engine.step(stripe, now_s=0.2)

    assert player.current_index() == 2


def test_fixed_shuffle_never_repeats_current_animation() -> None:
    player = AnimationPlayer.party()
    config = PlaybackConfig(
        mode=PlaybackMode.CYCLING,
        cycling=CyclingConfig(
            order=CycleOrder.SHUFFLE, timing=CycleTiming.FIXED, interval_s=1.0, seed=2
        ),
    )
    engine = PlaybackEngine(player, config)
    stripe = Stripe(8)
    before = player.current_index()

    engine.step(stripe, now_s=0.0)
    engine.step(stripe, now_s=1.1)

    assert player.current_index() != before


def test_dynamic_quiet_state_latches_steady_idle_color() -> None:
    player = AnimationPlayer.party()
    config = PlaybackConfig(
        mode=PlaybackMode.DYNAMIC,
        activity=MusicActivityConfig(idle_enter_frames=1),
    )
    engine = PlaybackEngine(player, config)
    stripe = CountingStripe(8)

    engine.step(stripe, snapshot=AudioSnapshot.silence(), now_s=0.0)
    engine.step(stripe, snapshot=AudioSnapshot.silence(), now_s=0.1)

    assert stripe.pixels()[0].tolist() == [32, 96, 255, 20]
    assert stripe.flush_calls == 1
    assert engine.music_active is False
    assert player.audio_enabled is False


def test_dynamic_resumes_audio_when_music_returns() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            activity=MusicActivityConfig(
                idle_enter_frames=1,
                activation_delay_s=0.0,
            ),
        ),
    )
    stripe = Stripe(8)
    engine.step(stripe, snapshot=AudioSnapshot.silence(), now_s=0.0)

    engine.step(stripe, snapshot=_active_snapshot(), now_s=20.0)

    assert engine.music_active is True
    assert engine.player.audio_enabled is True


def test_dynamic_schedules_layered_effects_after_activation_crossfade() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            activity=MusicActivityConfig(activation_delay_s=0.0),
        ),
    )
    stripe = Stripe(42)

    for frame in range(24):
        engine.step(stripe, snapshot=_active_snapshot(), now_s=frame * 0.025)

    assert engine.music_active is True
    assert engine.active_effect_names
    assert len(engine.active_effect_names) <= 2


def test_speech_like_low_energy_input_does_not_activate_music() -> None:
    detector = MusicActivityDetector(MusicActivityConfig(idle_enter_frames=3))
    speech_like = MusicFeatures(
        energy=0.02,
        onset_strength=0.2,
        brightness=0.3,
        silence=False,
    )

    for _ in range(10):
        assert detector.update(speech_like) is False


def test_loud_speech_without_rhythm_or_broadband_energy_stays_idle() -> None:
    detector = MusicActivityDetector(MusicActivityConfig(activation_delay_s=0.75))
    speech_like = MusicFeatures(
        energy=0.7,
        bass_energy=0.12,
        mid_energy=0.8,
        treble_energy=0.1,
        onset_strength=0.7,
        brightness=0.5,
        silence=False,
    )

    for step in range(20):
        assert detector.update(speech_like, now_s=step * 0.1) is False


def test_music_must_remain_qualified_for_activation_delay() -> None:
    detector = MusicActivityDetector(MusicActivityConfig(activation_delay_s=0.75))
    music = _active_snapshot().features

    assert detector.update(music, now_s=0.0) is False
    assert detector.state.value == "candidate"
    assert detector.update(music, now_s=0.74) is False
    assert detector.update(music, now_s=0.75) is True
    assert detector.state.value == "music"


def test_balanced_broadband_music_can_activate_without_detected_beats() -> None:
    detector = MusicActivityDetector(MusicActivityConfig(activation_delay_s=0.75))
    broadband_music = MusicFeatures(
        energy=0.6,
        bass_energy=0.4,
        mid_energy=0.5,
        treble_energy=0.35,
        silence=False,
    )

    assert detector.update(broadband_music, now_s=0.0) is False
    assert detector.update(broadband_music, now_s=0.75) is True


def test_music_candidate_resets_when_evidence_disappears() -> None:
    detector = MusicActivityDetector(MusicActivityConfig(activation_delay_s=0.75))

    assert detector.update(_active_snapshot().features, now_s=0.0) is False
    assert detector.update(MusicFeatures(silence=True), now_s=0.5) is False
    assert detector.update(_active_snapshot().features, now_s=1.0) is False
    assert detector.update(_active_snapshot().features, now_s=1.5) is False


def test_playback_config_rejects_invalid_idle_brightness() -> None:
    try:
        PlaybackConfig(idle_brightness=1.1)
    except ValueError as exc:
        assert "idle_brightness" in str(exc)
    else:
        raise AssertionError("invalid idle brightness was accepted")
