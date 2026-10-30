from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from lumistripe import (
    Animation,
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
    TransitionEffect,
)
from lumistripe.animation import AnimationPlayer
from lumistripe.audio import BeatEvent, MusicalTimelineConfig, MusicStructurePhase
from lumistripe.controller import Controller


class CountingStripe(Stripe):
    def __init__(self, length: int) -> None:
        super().__init__(length)
        self.flush_calls = 0

    def flush(self) -> None:
        self.flush_calls += 1
        super().flush()


class Red(Animation):
    @property
    def name(self) -> str:
        return "red"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(255, 0, 0))


class Blue(Animation):
    @property
    def name(self) -> str:
        return "blue"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(0, 0, 255))


class Green(Animation):
    @property
    def name(self) -> str:
        return "green"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(0, 255, 0))


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
        musical_impact=1.0,
    )
    return AudioSnapshot.from_parts(frame, features)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize(
    "field", ["activation_delay_s", "activation_hysteresis_ratio", "beat_density_window_s"]
)
def test_music_activity_rejects_nonfinite_timing(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        MusicActivityConfig(**{field: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_cycling_rejects_nonfinite_interval(value: float) -> None:
    with pytest.raises(ValueError):
        CyclingConfig(interval_s=value)


def test_static_holds_animation_past_authored_cycle() -> None:
    player = AnimationPlayer.party()
    engine = PlaybackEngine(player)
    stripe = Stripe(8)
    original = player.current_index()
    player.frame = player.animations[original].frames_per_cycle

    engine.step(stripe, now_s=10.0)

    assert player.current_index() == original


def test_music_idle_modes_render_without_changing_active_animation() -> None:
    player = AnimationPlayer()
    player.add(Red(), 20, 10)
    player.add(Blue(), 20, 10)
    engine = PlaybackEngine(player, PlaybackConfig(mode=PlaybackMode.DYNAMIC))
    stripe = Stripe(2)
    engine.step(stripe, now_s=0.0)
    np.testing.assert_array_equal(stripe.pixels()[0, :3], [0, 0, 0])
    engine.set_idle("solid", Rgb(20, 40, 60), "blue")
    engine.step(stripe, now_s=1.0)
    np.testing.assert_array_equal(stripe.pixels()[0, :3], [20, 40, 60])
    engine.set_idle("animation", Rgb(20, 40, 60), "blue")
    engine.step(stripe, now_s=2.0)
    np.testing.assert_array_equal(stripe.pixels()[0, :3], [0, 0, 255])
    assert player.name_at(player.current_index()) == "red"
    engine.set_idle("off", Rgb(20, 40, 60), "blue")
    engine.step(stripe, now_s=3.0)
    np.testing.assert_array_equal(stripe.pixels()[0, :3], [0, 0, 0])


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


def test_playback_can_render_without_flushing_for_frame_coordination() -> None:
    player = AnimationPlayer.party()
    engine = PlaybackEngine(
        player,
        PlaybackConfig(mode=PlaybackMode.SOLID, solid_color=Rgb(20, 40, 60)),
    )
    stripe = CountingStripe(4)

    engine.step(stripe, now_s=0.0, flush=False)

    assert stripe.pixels().tolist() == [[20, 40, 60, 255]] * 4
    assert stripe.flush_calls == 0


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


def test_manual_selection_crossfades_from_the_last_rendered_frame() -> None:
    player = AnimationPlayer()
    player.add(Red(), 20, 10)
    player.add(Blue(), 80, 10)
    engine = PlaybackEngine(player, PlaybackConfig(transition_duration_s=0.1))
    stripe = Stripe(2)
    engine.step(stripe, now_s=0.0)

    engine.select_animation("blue", now_s=1.0)
    engine.step(stripe, now_s=1.05)

    np.testing.assert_array_equal(stripe.pixels()[0, :3], [127, 0, 127])
    assert engine.transition_active is True
    engine.step(stripe, now_s=1.1)
    np.testing.assert_array_equal(stripe.pixels()[0, :3], [0, 0, 255])
    assert engine.transition_active is False


def test_manual_selection_uses_configured_fade_through_black_effect() -> None:
    player = AnimationPlayer()
    player.add(Red(), 20, 10)
    player.add(Blue(), 80, 10)
    engine = PlaybackEngine(
        player,
        PlaybackConfig(
            transition_duration_s=0.1,
            transition_effect=TransitionEffect.FADE_BLACK,
        ),
    )
    stripe = Stripe(2)
    engine.step(stripe, now_s=0.0)

    engine.select_animation("blue", now_s=1.0)
    engine.step(stripe, now_s=1.05)

    np.testing.assert_array_equal(stripe.pixels(), [[0, 0, 0, 255]] * 2)


def test_runtime_effect_update_keeps_active_effect_and_changes_next_transition() -> None:
    player = AnimationPlayer()
    player.add(Red(), 20, 10)
    player.add(Blue(), 80, 10)
    engine = PlaybackEngine(
        player,
        PlaybackConfig(
            transition_duration_s=0.1,
            transition_effect=TransitionEffect.FADE_BLACK,
        ),
    )
    stripe = Stripe(2)
    engine.step(stripe, now_s=0.0)
    engine.select_animation("blue", now_s=1.0)
    engine.set_transition_effect(TransitionEffect.WIPE)
    engine.step(stripe, now_s=1.05)

    np.testing.assert_array_equal(stripe.pixels(), [[0, 0, 0, 255]] * 2)
    assert engine.transition_active is True
    engine.step(stripe, now_s=1.1)
    assert engine.transition_active is False

    engine.select_animation("red", now_s=2.0)
    engine.step(stripe, now_s=2.06)

    np.testing.assert_array_equal(
        stripe.pixels(), [[255, 0, 0, 255], [51, 0, 203, 255]]
    )
    assert engine.player._active_transition_effect is TransitionEffect.WIPE


def test_zero_transition_duration_keeps_manual_selection_instant() -> None:
    player = AnimationPlayer()
    player.add(Red(), 20, 10)
    player.add(Blue(), 80, 10)
    engine = PlaybackEngine(player, PlaybackConfig(transition_duration_s=0.0))
    stripe = Stripe(2)
    engine.step(stripe, now_s=0.0)

    engine.select_animation("blue", now_s=1.0)
    engine.step(stripe, now_s=1.01)

    np.testing.assert_array_equal(stripe.pixels()[0, :3], [0, 0, 255])
    assert engine.transition_active is False


def test_transition_duration_updates_future_transitions_without_restarting_playback() -> None:
    player = AnimationPlayer()
    player.add(Red(), 20, 10)
    player.add(Blue(), 80, 10)
    engine = PlaybackEngine(player, PlaybackConfig(transition_duration_s=0.1))
    stripe = Stripe(2)
    engine.step(stripe, now_s=0.0)
    engine.select_animation("blue", now_s=1.0)
    engine.step(stripe, now_s=1.05)

    engine.set_transition_duration(0.5)

    assert engine.transition_active is True
    assert engine.player.transition_ms == 500
    engine.step(stripe, now_s=1.1)
    np.testing.assert_array_equal(stripe.pixels()[0, :3], [0, 0, 255])
    assert engine.transition_active is False

    engine.select_animation("red", now_s=2.0)
    engine.step(stripe, now_s=2.25)
    np.testing.assert_array_equal(stripe.pixels()[0, :3], [127, 0, 127])
    assert engine.transition_active is True


def test_cycling_switch_uses_elapsed_transition_duration() -> None:
    player = AnimationPlayer()
    player.add(Red(), 20, 1)
    player.add(Blue(), 80, 10)
    engine = PlaybackEngine(
        player,
        PlaybackConfig(mode=PlaybackMode.CYCLING, transition_duration_s=0.1),
    )
    stripe = Stripe(2)
    engine.step(stripe, now_s=0.0)

    engine.step(stripe, now_s=1.0)
    assert player.name_at(player.current_index()) == "blue"
    engine.step(stripe, now_s=1.05)

    np.testing.assert_array_equal(stripe.pixels()[0, :3], [127, 0, 127])
    assert engine.transition_active is True


def test_cycling_switch_uses_configured_transition_effect() -> None:
    player = AnimationPlayer()
    player.add(Red(), 20, 1)
    player.add(Blue(), 80, 10)
    engine = PlaybackEngine(
        player,
        PlaybackConfig(
            mode=PlaybackMode.CYCLING,
            transition_duration_s=0.1,
            transition_effect=TransitionEffect.WIPE,
        ),
    )
    stripe = Stripe(2)
    engine.step(stripe, now_s=0.0)
    engine.step(stripe, now_s=1.0)

    assert engine.player.name_at(engine.player.current_index()) == "blue"
    assert engine.player._active_transition_effect is TransitionEffect.WIPE
    assert engine.transition_active is True


def test_independent_playback_engines_keep_transition_sources_separate() -> None:
    left_player = AnimationPlayer()
    left_player.add(Red(), 20, 10)
    left_player.add(Blue(), 80, 10)
    right_player = AnimationPlayer()
    right_player.add(Green(), 20, 10)
    right_player.add(Blue(), 80, 10)
    config = PlaybackConfig(transition_duration_s=0.1)
    left = PlaybackEngine(left_player, config)
    right = PlaybackEngine(right_player, config)
    left_stripe = Stripe(1)
    right_stripe = Stripe(1)
    left.step(left_stripe, now_s=0.0)
    right.step(right_stripe, now_s=0.0)

    left.select_animation("blue", now_s=1.0)
    right.select_animation("blue", now_s=1.0)
    left.step(left_stripe, now_s=1.05)
    right.step(right_stripe, now_s=1.05)

    np.testing.assert_array_equal(left_stripe.pixels()[0, :3], [127, 0, 127])
    np.testing.assert_array_equal(right_stripe.pixels()[0, :3], [0, 127, 127])


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


def test_dynamic_quiet_state_latches_complete_blackout() -> None:
    player = AnimationPlayer.party()
    config = PlaybackConfig(
        mode=PlaybackMode.DYNAMIC,
        activity=MusicActivityConfig(idle_enter_frames=1),
    )
    engine = PlaybackEngine(player, config)
    stripe = CountingStripe(8)

    engine.step(stripe, snapshot=AudioSnapshot.silence(), now_s=0.0)
    engine.step(stripe, snapshot=AudioSnapshot.silence(), now_s=0.1)

    assert not stripe.pixels()[:, :3].any()
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

    # Some newly eligible strobe/effect entries render at 10 ms per frame, so
    # allow enough frames for the 300 ms activation crossfade to finish.
    for frame in range(40):
        engine.step(stripe, snapshot=_active_snapshot(), now_s=frame * 0.025)

    assert engine.music_active is True
    assert engine.active_effect_names
    assert len(engine.active_effect_names) <= 2


def test_dynamic_playback_tracks_music_structure_and_resets_on_mode_change() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            activity=MusicActivityConfig(
                activation_delay_s=0.0,
                idle_enter_frames=1,
            ),
        ),
    )
    stripe = Stripe(8)

    for index in range(45):
        energy = 0.18 + 0.008 * index
        features = MusicFeatures(
            energy=energy,
            volume=energy,
            energy_level=energy,
            bass_energy=energy * 0.7,
            mid_energy=0.4,
            treble_energy=0.3,
            onset_strength=0.2,
            beat=True,
            beat_strength=0.8,
            silence=False,
        )
        frame = AudioFrame(
            rms=energy,
            beat=True,
            beat_strength=0.8,
            timestamp=index * 0.1,
            fresh=True,
        )
        engine.step(
            stripe,
            snapshot=AudioSnapshot.from_parts(frame, features),
            now_s=index * 0.1,
        )

    assert engine.music_structure_state.phase is MusicStructurePhase.BUILDUP
    assert engine.music_structure_state.buildup_progress > 0.0

    stale_drop = MusicFeatures(
        energy=0.9,
        bass_energy=0.75,
        mid_energy=0.4,
        treble_energy=0.3,
        onset_strength=0.4,
        drop_detected=True,
    )
    engine.step(
        stripe,
        snapshot=AudioSnapshot.from_parts(
            AudioFrame(timestamp=4.5, fresh=False), stale_drop
        ),
        now_s=4.5,
    )

    assert engine.music_structure_state.phase is MusicStructurePhase.BUILDUP
    assert engine.music_structure_state.buildup_progress > 0.0
    assert engine.confirmed_drop_count == 0

    engine.step(
        stripe,
        snapshot=AudioSnapshot.from_parts(
            AudioFrame(timestamp=4.6, fresh=True), stale_drop
        ),
        now_s=4.6,
    )

    assert engine.music_structure_state.phase is MusicStructurePhase.DROP
    assert engine.music_structure_state.drop_event is True
    assert engine.confirmed_drop_count == 1

    engine.step(
        stripe,
        snapshot=AudioSnapshot.from_parts(
            AudioFrame(timestamp=4.7, fresh=False), stale_drop
        ),
        now_s=4.7,
    )

    assert engine.music_structure_state.phase is MusicStructurePhase.DROP
    assert engine.music_structure_state.drop_event is False
    assert engine.confirmed_drop_count == 1

    engine.step(
        stripe,
        snapshot=AudioSnapshot.from_parts(
            AudioFrame(timestamp=4.8, fresh=True), MusicFeatures(silence=True)
        ),
        now_s=4.8,
    )

    assert engine.music_structure_state.phase is MusicStructurePhase.IDLE
    assert engine.music_structure_state.confidence == 0.0
    assert engine.confirmed_drop_count == 0

    engine.set_mode(PlaybackMode.STATIC, now_s=5.0)

    assert engine.music_structure_state.phase is MusicStructurePhase.IDLE
    assert engine.music_structure_state.confidence == 0.0


def test_playback_exposes_timeline_from_its_advancing_beat_clock() -> None:
    player = AnimationPlayer.party()
    original_animation = player.current_index()
    engine = PlaybackEngine(
        player,
        PlaybackConfig(
            mode=PlaybackMode.STATIC,
            musical_timeline=MusicalTimelineConfig(bars_per_phrase=2),
        ),
    )
    stripe = Stripe(8)
    first_snapshot = AudioSnapshot.from_parts(
        AudioFrame(fresh=True),
        MusicFeatures(bpm=120.0),
        beat_events=(BeatEvent(sequence=1, timestamp=10.0, strength=0.9),),
    )

    engine.step(stripe, snapshot=first_snapshot, now_s=10.0)
    first_beat = engine.musical_timeline_state
    engine.step(stripe, now_s=10.5)
    second_beat = engine.musical_timeline_state

    assert first_beat.aligned is True
    assert first_beat.beat_index == 1
    assert first_beat.bars_per_phrase == 2
    assert first_beat.next_phrase_at == 14.0
    assert second_beat.beat_index == 2
    assert second_beat.beat_in_bar == 2
    assert player.current_index() == original_animation


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


def test_rhythmic_activation_requires_configured_onset_threshold() -> None:
    detector = MusicActivityDetector(
        MusicActivityConfig(activation_delay_s=0.0, onset_threshold=0.5)
    )
    weak_onsets = MusicFeatures(
        energy=0.7,
        onset_strength=0.1,
        beat=True,
        bass_energy=0.7,
        mid_energy=0.9,
        treble_energy=0.01,
        silence=False,
    )

    for step in range(20):
        assert detector.update(weak_onsets, now_s=step * 0.1) is False


def test_music_must_remain_qualified_for_activation_delay() -> None:
    detector = MusicActivityDetector(MusicActivityConfig(activation_delay_s=0.75))
    music = _active_snapshot().features

    assert detector.update(music, now_s=0.0) is False
    assert detector.state.value == "candidate"
    assert detector.update(music, now_s=0.74) is False
    assert detector.update(music, now_s=0.75) is True
    assert detector.state.value == "music"


@pytest.mark.parametrize("now_s", [float("nan"), float("inf"), -float("inf")])
def test_music_activity_rejects_nonfinite_update_timestamp(now_s: float) -> None:
    detector = MusicActivityDetector()

    with pytest.raises(ValueError, match="now_s must be finite"):
        detector.update(MusicFeatures(beat=True), now_s=now_s)

    assert not detector.beat_samples


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


def test_beat_density_uses_a_bounded_time_window() -> None:
    detector = MusicActivityDetector(
        MusicActivityConfig(beat_density_window_s=2.0, activation_delay_s=10.0)
    )
    pulse = MusicFeatures(beat=True, silence=False)
    quiet = MusicFeatures(silence=False)

    detector.update(pulse, now_s=0.0)
    detector.update(quiet, now_s=1.0)
    assert detector.beat_density == pytest.approx(0.5)

    detector.update(quiet, now_s=2.1)
    assert detector.beat_density == pytest.approx(0.0)


def test_music_activation_uses_stronger_thresholds_than_deactivation() -> None:
    config = MusicActivityConfig(
        activation_delay_s=0.0,
        idle_enter_frames=2,
        feature_attack=1.0,
        feature_release=1.0,
        energy_threshold=0.1,
        onset_threshold=0.1,
        beat_density_threshold=0.1,
        activation_hysteresis_ratio=1.5,
    )
    detector = MusicActivityDetector(config)
    borderline = MusicFeatures(
        energy=0.12,
        onset_strength=0.12,
        beat=True,
        silence=False,
    )
    strong = replace(borderline, energy=0.2, onset_strength=0.2)

    assert detector.update(borderline, now_s=0.0) is False
    assert detector.update(strong, now_s=0.1) is True
    assert detector.update(borderline, now_s=0.2) is True


def test_playback_config_rejects_invalid_idle_brightness() -> None:
    try:
        PlaybackConfig(idle_brightness=1.1)
    except ValueError as exc:
        assert "idle_brightness" in str(exc)
    else:
        raise AssertionError("invalid idle brightness was accepted")


@pytest.mark.parametrize("brightness", [float("nan"), float("inf"), -float("inf")])
def test_animation_player_rejects_nonfinite_brightness(brightness: float) -> None:
    player = AnimationPlayer.party()
    player.set_brightness(0.25)

    with pytest.raises(ValueError, match="brightness must be finite"):
        player.set_brightness(brightness)

    assert player.brightness == 0.25
