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
    RgbwTest,
    Stripe,
)
from lumistripe.animation import AnimationPlayer


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


def test_dynamic_quiet_state_uses_calm_animation_without_audio() -> None:
    player = AnimationPlayer.party()
    config = PlaybackConfig(
        mode=PlaybackMode.DYNAMIC,
        activity=MusicActivityConfig(idle_enter_frames=1),
    )
    engine = PlaybackEngine(player, config)
    stripe = Stripe(8)

    engine.step(stripe, snapshot=AudioSnapshot.silence(), now_s=0.0)

    current = player.animations[player.current_index()]
    assert current.animation.metadata.supports_silence is True
    assert engine.music_active is False
    assert player.audio_enabled is False


def test_dynamic_resumes_audio_when_music_returns() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC, activity=MusicActivityConfig(idle_enter_frames=1)
        ),
    )
    stripe = Stripe(8)
    engine.step(stripe, snapshot=AudioSnapshot.silence(), now_s=0.0)

    engine.step(stripe, snapshot=_active_snapshot(), now_s=20.0)

    assert engine.music_active is True
    assert engine.player.audio_enabled is True


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
