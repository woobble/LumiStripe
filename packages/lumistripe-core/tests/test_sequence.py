from __future__ import annotations

import pytest
from lumistripe import (
    AnimationSequence,
    AnimationSequenceStep,
    AudioFrame,
    AudioSnapshot,
    BeatEvent,
    MusicActivityConfig,
    MusicFeatures,
    PlaybackConfig,
    PlaybackEngine,
    PlaybackMode,
    RgbwTest,
    Stripe,
)
from lumistripe.animation import AnimationPlayer
from lumistripe.audio import MusicalTimelineState
from lumistripe.playback import AnimationSequenceCoordinator


def _timeline(
    bar: int,
    *,
    beat: int = 1,
    next_bar_at: float,
    bpm: float = 120.0,
    confidence: float = 0.9,
) -> MusicalTimelineState:
    beat_index = (bar - 1) * 4 + beat
    return MusicalTimelineState(
        aligned=True,
        alignment_estimated=True,
        beats_per_bar=4,
        bars_per_phrase=8,
        beat_index=beat_index,
        beat_in_bar=beat,
        bar_index=bar,
        bar_in_phrase=(bar - 1) % 8 + 1,
        phrase_index=(bar - 1) // 8 + 1,
        beat_in_phrase=(bar - 1) % 8 * 4 + beat,
        bpm=bpm,
        confidence=confidence,
        next_bar_at=next_bar_at,
    )


def _coordinator(sequence: AnimationSequence) -> AnimationSequenceCoordinator:
    return AnimationSequenceCoordinator(
        sequence,
        confidence_threshold=0.65,
        subdivisions_per_beat=1,
    )


def test_sequence_models_validate_and_freeze_ordered_steps() -> None:
    step = AnimationSequenceStep(" pulse ", 2)
    sequence = AnimationSequence("id", "Night drive", [step], True)  # type: ignore[arg-type]

    assert step.animation_name == "pulse"
    assert sequence.steps == (step,)
    with pytest.raises((AttributeError, TypeError)):
        sequence.steps += (step,)  # type: ignore[misc]
    with pytest.raises(ValueError, match="duration_bars"):
        AnimationSequenceStep("pulse", 3)
    with pytest.raises(ValueError, match="at least one"):
        AnimationSequence("empty", "Empty", (), False)
    with pytest.raises(ValueError, match="at most"):
        AnimationSequence("long", "Long", (step,) * 33, True)


def test_sequence_starts_on_confident_bar_and_loops_with_step_durations() -> None:
    sequence = AnimationSequence(
        "loop",
        "Loop",
        (
            AnimationSequenceStep("pulse", 2),
            AnimationSequenceStep("comet", 1),
        ),
        True,
    )
    coordinator = _coordinator(sequence)

    assert coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=0.0,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.state.next_transition_at_s == pytest.approx(2.0)
    assert coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.0,
        sync_offset_s=0.0,
        music_active=True,
    ) == "pulse"
    assert coordinator.state.current_step_index == 0
    assert coordinator.state.remaining_bars == 2

    assert coordinator.update(
        _timeline(3, next_bar_at=6.0),
        now_s=4.0,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.state.remaining_bars == 1
    assert coordinator.update(
        _timeline(4, next_bar_at=8.0),
        now_s=6.0,
        sync_offset_s=0.0,
        music_active=True,
    ) == "comet"
    assert coordinator.state.remaining_bars == 1
    assert coordinator.update(
        _timeline(5, next_bar_at=10.0),
        now_s=8.0,
        sync_offset_s=0.0,
        music_active=True,
    ) == "pulse"


def test_non_repeating_sequence_finishes_cleanly() -> None:
    coordinator = _coordinator(
        AnimationSequence(
            "once", "Once", (AnimationSequenceStep("pulse", 1),), False
        )
    )
    coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=0.0,
        sync_offset_s=0.0,
        music_active=True,
    )
    assert coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.0,
        sync_offset_s=0.0,
        music_active=True,
    ) == "pulse"

    assert coordinator.update(
        _timeline(3, next_bar_at=6.0),
        now_s=4.0,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.controls_playback is False
    assert coordinator.state.active is False
    assert coordinator.state.remaining_bars == 0


def test_confidence_loss_freezes_remaining_bars_and_reanchors_on_recovery() -> None:
    coordinator = _coordinator(
        AnimationSequence(
            "pause", "Pause", (AnimationSequenceStep("pulse", 4),), True
        )
    )
    coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=0.0,
        sync_offset_s=0.0,
        music_active=True,
    )
    coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.0,
        sync_offset_s=0.0,
        music_active=True,
    )

    assert coordinator.update(
        _timeline(5, next_bar_at=10.0, confidence=0.2),
        now_s=8.0,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.state.waiting_for_clock is True
    assert coordinator.state.remaining_bars == 4

    assert coordinator.update(
        _timeline(5, next_bar_at=10.0),
        now_s=8.0,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.state.waiting_for_clock is False
    assert coordinator.state.remaining_bars == 4
    assert coordinator.state.next_transition_at_s == pytest.approx(16.0)


def test_negative_sync_offset_applies_once_before_estimated_bar_boundary() -> None:
    coordinator = _coordinator(
        AnimationSequence(
            "offset",
            "Offset",
            (AnimationSequenceStep("pulse", 1), AnimationSequenceStep("comet", 1)),
            True,
        )
    )
    coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=0.0,
        sync_offset_s=-0.085,
        music_active=True,
    )
    assert coordinator.state.next_transition_at_s == pytest.approx(1.915)
    assert coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=1.92,
        sync_offset_s=-0.085,
        music_active=True,
    ) == "pulse"
    assert coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=1.93,
        sync_offset_s=-0.085,
        music_active=True,
    ) is None
    assert coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.0,
        sync_offset_s=-0.085,
        music_active=True,
    ) is None
    assert coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=3.92,
        sync_offset_s=-0.085,
        music_active=True,
    ) == "comet"


def test_tempo_change_recalculates_future_boundary_time() -> None:
    coordinator = _coordinator(
        AnimationSequence(
            "tempo",
            "Tempo",
            (AnimationSequenceStep("pulse", 2), AnimationSequenceStep("comet", 1)),
            True,
        )
    )
    coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=0.0,
        sync_offset_s=0.0,
        music_active=True,
    )
    coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.0,
        sync_offset_s=0.0,
        music_active=True,
    )

    assert coordinator.update(
        _timeline(3, next_bar_at=6.4, bpm=100.0),
        now_s=4.0,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.state.next_transition_at_s == pytest.approx(6.4)
    assert coordinator.update(
        _timeline(4, next_bar_at=8.8, bpm=100.0),
        now_s=6.4,
        sync_offset_s=0.0,
        music_active=True,
    ) == "comet"


def test_current_bar_boundary_estimate_is_correct_from_mid_bar_position() -> None:
    coordinator = _coordinator(
        AnimationSequence(
            "mid-bar",
            "Mid bar",
            (AnimationSequenceStep("pulse", 1), AnimationSequenceStep("comet", 1)),
            True,
        )
    )
    coordinator.update(
        _timeline(1, next_bar_at=1.0, bpm=240.0),
        now_s=0.0,
        sync_offset_s=0.4,
        music_active=True,
    )
    assert coordinator.update(
        _timeline(2, next_bar_at=2.0, bpm=240.0),
        now_s=1.4,
        sync_offset_s=0.4,
        music_active=True,
    ) == "pulse"

    assert coordinator.update(
        _timeline(3, beat=2, next_bar_at=3.0, bpm=240.0),
        now_s=2.25,
        sync_offset_s=0.4,
        music_active=True,
    ) is None
    assert coordinator.state.next_transition_at_s == pytest.approx(2.4)


def test_clock_regression_reanchors_without_replaying_a_step() -> None:
    coordinator = _coordinator(
        AnimationSequence(
            "regression",
            "Regression",
            (AnimationSequenceStep("pulse", 2), AnimationSequenceStep("comet", 1)),
            True,
        )
    )
    coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=0.0,
        sync_offset_s=0.0,
        music_active=True,
    )
    assert coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.0,
        sync_offset_s=0.0,
        music_active=True,
    ) == "pulse"

    assert coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=1.9,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.state.current_step_index == 0
    assert coordinator.state.remaining_bars == 2
    assert coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.1,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.state.current_step_index == 0


def test_stalled_frame_does_not_catch_up_missed_sequence_bars() -> None:
    coordinator = _coordinator(
        AnimationSequence(
            "stall",
            "Stall",
            (AnimationSequenceStep("pulse", 2), AnimationSequenceStep("comet", 1)),
            True,
        )
    )
    coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=0.0,
        sync_offset_s=0.0,
        music_active=True,
    )
    coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.0,
        sync_offset_s=0.0,
        music_active=True,
    )

    assert coordinator.update(
        _timeline(5, next_bar_at=10.0),
        now_s=8.0,
        sync_offset_s=0.0,
        music_active=True,
    ) is None
    assert coordinator.state.current_step_index == 0
    assert coordinator.state.remaining_bars == 2
    assert coordinator.state.next_transition_at_s == pytest.approx(12.0)


def _active_snapshot(sequence: int, timestamp: float) -> AudioSnapshot:
    frame = AudioFrame(
        rms=0.8,
        bands=(0.9, 0.8, 0.5, 0.4, 0.3, 0.4, 0.5, 0.3),
        beat=True,
        beat_strength=0.9,
        sequence=sequence,
        timestamp=timestamp,
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
    return AudioSnapshot.from_parts(
        frame,
        features,
        beat_events=(BeatEvent(sequence=sequence, timestamp=timestamp, strength=0.9),),
    )


def test_dynamic_sequence_overrides_selector_and_uses_player_crossfade() -> None:
    sequence = AnimationSequence(
        "short",
        "Short",
        (AnimationSequenceStep("pulse", 1), AnimationSequenceStep("comet", 1)),
        True,
    )
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            animation_sequence=sequence,
            activity=MusicActivityConfig(activation_delay_s=0.0),
        ),
    )
    stripe = Stripe(8)

    for event in range(1, 13):
        now = (event - 1) * 0.5
        engine.step(stripe, snapshot=_active_snapshot(event, now), now_s=now)

    assert engine.player.name_at(engine.player.current_index()) == "pulse"
    assert engine.sequence_playback_state.active is True
    assert engine.sequence_playback_state.current_step_index == 0
    assert engine.player.transition_active is True
    assert engine.dynamic_selector.recent_names == []

    engine.step(stripe, snapshot=_active_snapshot(13, 6.0), now_s=6.0)

    assert engine.player.name_at(engine.player.current_index()) == "comet"
    assert engine.sequence_playback_state.current_step_index == 1
    assert engine.player.transition_active is True


def test_dynamic_sequence_keeps_music_structure_drop_accent_pipeline() -> None:
    sequence = AnimationSequence(
        "drop-check",
        "Drop check",
        (AnimationSequenceStep("pulse", 8),),
        True,
    )
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            animation_sequence=sequence,
            activity=MusicActivityConfig(activation_delay_s=0.0),
        ),
    )
    engine.set_sync_offset(-0.085)
    stripe = Stripe(42)
    beat_sequence = 0

    for index in range(45):
        now = index * 0.1
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
        events: tuple[BeatEvent, ...] = ()
        if index % 5 == 0:
            beat_sequence += 1
            events = (
                BeatEvent(
                    sequence=beat_sequence,
                    timestamp=now,
                    strength=0.9,
                ),
            )
        frame = AudioFrame(
            rms=energy,
            beat=True,
            beat_strength=0.8,
            timestamp=now,
            fresh=True,
        )
        engine.step(
            stripe,
            snapshot=AudioSnapshot.from_parts(frame, features, beat_events=events),
            now_s=now,
        )
        if index == 39:
            engine.step(
                stripe,
                snapshot=AudioSnapshot.from_parts(
                    AudioFrame(timestamp=3.925, fresh=False),
                    features,
                ),
                now_s=3.925,
            )

    assert engine.sequence_playback_state.active is True
    assert engine.music_structure_state.buildup_progress > 0.0

    drop_features = MusicFeatures(
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
            AudioFrame(timestamp=4.6, fresh=True),
            drop_features,
        ),
        now_s=4.6,
    )
    assert engine.music_structure_state.drop_event is True

    for frame in range(20):
        now = 4.61 + frame * 0.015
        engine.step(
            stripe,
            snapshot=AudioSnapshot.from_parts(
                AudioFrame(timestamp=now, fresh=False),
                drop_features,
            ),
            now_s=now,
        )

    engine.step(
        stripe,
        snapshot=AudioSnapshot.from_parts(
            AudioFrame(timestamp=4.92, fresh=False),
            drop_features,
        ),
        now_s=4.92,
    )
    assert "drop_explosion" in engine.active_effect_names


def test_dynamic_sequence_disabled_keeps_immediate_selector_behavior() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            activity=MusicActivityConfig(activation_delay_s=0.0),
        ),
    )

    engine.step(Stripe(8), snapshot=_active_snapshot(1, 0.0), now_s=0.0)

    assert engine.sequence_playback_state.active is False
    assert engine.dynamic_selector.current_name is not None


def test_finished_non_repeating_sequence_returns_to_dynamic_selector() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            animation_sequence=AnimationSequence(
                "finish",
                "Finish",
                (AnimationSequenceStep("pulse", 1),),
                False,
            ),
            activity=MusicActivityConfig(activation_delay_s=0.0),
        ),
    )
    stripe = Stripe(8)

    for event in range(1, 14):
        now = (event - 1) * 0.5
        engine.step(stripe, snapshot=_active_snapshot(event, now), now_s=now)

    assert engine.sequence_playback_state.active is False
    assert engine.sequence_playback_state.remaining_bars == 0
    assert engine.dynamic_selector.current_name is not None
    assert engine.last_decision is not None
    assert engine.last_decision.reason != "sequence"


def test_engine_rejects_utility_and_unknown_sequence_animations() -> None:
    player = AnimationPlayer.party()
    player.add_utility(RgbwTest(), 20, 1)

    with pytest.raises(ValueError, match="eligible"):
        PlaybackEngine(
            player,
            PlaybackConfig(
                animation_sequence=AnimationSequence(
                    "utility", "Utility", (AnimationSequenceStep("rgbw_test", 1),), True
                )
            ),
        )
    with pytest.raises(ValueError, match="unknown animation"):
        PlaybackEngine(
            AnimationPlayer.party(),
            PlaybackConfig(
                animation_sequence=AnimationSequence(
                    "unknown", "Unknown", (AnimationSequenceStep("not_real", 1),), True
                )
            ),
        )


def test_replacing_sequence_cancels_progress_and_inactivity_resets_session() -> None:
    first = AnimationSequence(
        "first", "First", (AnimationSequenceStep("pulse", 2),), True
    )
    second = AnimationSequence(
        "second", "Second", (AnimationSequenceStep("comet", 1),), True
    )
    engine = PlaybackEngine(AnimationPlayer.party(), PlaybackConfig(animation_sequence=first))
    coordinator = engine._sequence_coordinator
    coordinator.update(
        _timeline(1, next_bar_at=2.0),
        now_s=0.0,
        sync_offset_s=0.0,
        music_active=True,
    )
    coordinator.update(
        _timeline(2, next_bar_at=4.0),
        now_s=2.0,
        sync_offset_s=0.0,
        music_active=True,
    )
    assert engine.sequence_playback_state.active is True

    engine.set_animation_sequence(second)
    assert engine.sequence_playback_state.sequence_id == "second"
    assert engine.sequence_playback_state.active is False
    assert engine.sequence_playback_state.current_step_index is None

    engine.set_mode(PlaybackMode.DYNAMIC, now_s=3.0)
    engine.step(Stripe(8), snapshot=AudioSnapshot.silence(), now_s=3.0)
    assert engine.sequence_playback_state.active is False
    assert engine.sequence_playback_state.current_step_index is None
