from __future__ import annotations

from dataclasses import replace

import pytest
from lumistripe import (
    AnimationPlayer,
    AudioFrame,
    AudioSnapshot,
    BeatEvent,
    DynamicSelector,
    DynamicSelectorConfig,
    MusicActivityConfig,
    MusicFeatures,
    PlaybackConfig,
    PlaybackEngine,
    PlaybackMode,
    SelectorDecision,
    Stripe,
    SwitchQuantization,
)
from lumistripe.audio.timeline import MusicalTimelineConfig, MusicalTimelineState
from lumistripe.playback import QuantizedSwitchCoordinator


def _decision(
    name: str = "next", *, current: str = "current", should_switch: bool = True
) -> SelectorDecision:
    return SelectorDecision(name, 1.0, current, should_switch, "test")


def _timeline(
    beat_index: int,
    *,
    next_beat_at: float,
    bpm: float = 120.0,
    confidence: float = 0.9,
    bars_per_phrase: int = 2,
) -> MusicalTimelineState:
    period = 60.0 / bpm
    zero_based_beat = beat_index - 1
    beats_to_bar = 4 - zero_based_beat % 4
    phrase_length = 4 * bars_per_phrase
    beats_to_phrase = phrase_length - zero_based_beat % phrase_length
    return MusicalTimelineState(
        aligned=True,
        alignment_estimated=True,
        beats_per_bar=4,
        bars_per_phrase=bars_per_phrase,
        beat_index=beat_index,
        beat_in_bar=zero_based_beat % 4 + 1,
        bar_index=zero_based_beat // 4 + 1,
        bar_in_phrase=(zero_based_beat // 4) % bars_per_phrase + 1,
        phrase_index=zero_based_beat // phrase_length + 1,
        beat_in_phrase=zero_based_beat % phrase_length + 1,
        bpm=bpm,
        confidence=confidence,
        next_beat_at=next_beat_at,
        next_bar_at=next_beat_at + (beats_to_bar - 1) * period,
        next_phrase_at=next_beat_at + (beats_to_phrase - 1) * period,
    )


def _coordinator(
    mode: SwitchQuantization,
) -> QuantizedSwitchCoordinator:
    return QuantizedSwitchCoordinator(
        mode,
        confidence_threshold=0.65,
        subdivisions_per_beat=1,
    )


@pytest.mark.parametrize(
    ("mode", "boundary_at"),
    [
        (SwitchQuantization.BEAT, 10.5),
        (SwitchQuantization.BAR, 12.0),
        (SwitchQuantization.PHRASE, 14.0),
    ],
)
def test_switch_waits_for_selected_musical_boundary(
    mode: SwitchQuantization, boundary_at: float
) -> None:
    coordinator = _coordinator(mode)
    state = _timeline(1, next_beat_at=10.5)

    assert coordinator.update(
        _decision(), state, now_s=10.0, sync_offset_s=0.0
    ) is None
    assert coordinator.update(
        _decision(), state, now_s=boundary_at - 0.01, sync_offset_s=0.0
    ) is None
    assert coordinator.update(
        _decision(), state, now_s=boundary_at + 0.01, sync_offset_s=0.0
    ) == _decision()
    assert coordinator.pending_decision is None


def test_negative_sync_offset_is_applied_and_late_boundary_is_discarded() -> None:
    coordinator = _coordinator(SwitchQuantization.BEAT)
    state = _timeline(1, next_beat_at=10.5)

    coordinator.update(_decision(), state, now_s=10.0, sync_offset_s=-0.085)
    early_commit = coordinator.update(
        _decision(), state, now_s=10.43, sync_offset_s=-0.085
    )

    assert early_commit == _decision()

    coordinator.update(_decision(), state, now_s=10.0, sync_offset_s=-0.085)
    missed = coordinator.update(
        _decision(), state, now_s=10.48, sync_offset_s=-0.085
    )

    assert missed is None
    assert coordinator._pending is not None
    assert coordinator._pending.target_position == 3


def test_candidate_replacement_keeps_boundary_and_cancel_clears_it() -> None:
    coordinator = _coordinator(SwitchQuantization.BEAT)
    state = _timeline(3, next_beat_at=20.5)
    coordinator.update(_decision("first"), state, now_s=20.0, sync_offset_s=0.0)
    target = coordinator._pending.target_position if coordinator._pending else None

    coordinator.update(_decision("replacement"), state, now_s=20.1, sync_offset_s=0.0)

    assert coordinator._pending is not None
    assert coordinator._pending.target_position == target
    assert coordinator.pending_decision is not None
    assert coordinator.pending_decision.selected_name == "replacement"

    coordinator.update(
        _decision("replacement", should_switch=False),
        state,
        now_s=20.2,
        sync_offset_s=0.0,
    )
    assert coordinator.pending_decision is None


def test_tempo_change_reprojects_future_boundary() -> None:
    coordinator = _coordinator(SwitchQuantization.BEAT)
    coordinator.update(
        _decision(), _timeline(4, next_beat_at=30.5), now_s=30.0, sync_offset_s=0.0
    )

    slower_clock = _timeline(4, next_beat_at=30.6, bpm=100.0)
    assert coordinator.update(
        _decision(), slower_clock, now_s=30.5, sync_offset_s=0.0
    ) is None
    assert coordinator._pending is not None
    assert coordinator._pending.boundary_at_s == pytest.approx(30.6)
    assert coordinator.update(
        _decision(), slower_clock, now_s=30.61, sync_offset_s=0.0
    ) == _decision()


def test_lost_confidence_cancels_pending_candidate() -> None:
    coordinator = _coordinator(SwitchQuantization.BAR)
    coordinator.update(
        _decision(), _timeline(4, next_beat_at=40.5), now_s=40.0, sync_offset_s=0.0
    )

    uncertain = _timeline(4, next_beat_at=40.5, confidence=0.4)
    assert coordinator.update(
        _decision(), uncertain, now_s=40.1, sync_offset_s=0.0
    ) is None
    assert coordinator.pending_decision is None


def test_switch_mode_changes_clear_pending_work_and_default_stays_immediate() -> None:
    assert PlaybackConfig().switch_quantization is SwitchQuantization.OFF

    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            switch_quantization=SwitchQuantization.BEAT,
        ),
    )
    engine._quantized_switcher.update(
        _decision(), _timeline(2, next_beat_at=45.5), now_s=45.0, sync_offset_s=0.0
    )
    assert engine._quantized_switcher.pending_decision is not None

    engine.set_mode(PlaybackMode.STATIC, now_s=45.1)

    assert engine._quantized_switcher.pending_decision is None


def test_stalled_and_regressed_clocks_do_not_replay_pending_boundary() -> None:
    stalled = _coordinator(SwitchQuantization.BEAT)
    stalled.update(
        _decision(), _timeline(10, next_beat_at=50.5), now_s=50.0, sync_offset_s=0.0
    )
    stalled.update(
        _decision(), _timeline(12, next_beat_at=51.5), now_s=51.0, sync_offset_s=0.0
    )

    assert stalled._pending is not None
    assert stalled._pending.target_position == 13
    assert stalled.update(
        _decision(), _timeline(12, next_beat_at=51.5), now_s=51.0, sync_offset_s=0.0
    ) is None

    regressed = _coordinator(SwitchQuantization.BEAT)
    regressed.update(
        _decision(), _timeline(10, next_beat_at=60.5), now_s=60.0, sync_offset_s=0.0
    )
    regressed.update(
        _decision(), _timeline(1, next_beat_at=70.5), now_s=70.0, sync_offset_s=0.0
    )

    assert regressed._pending is not None
    assert regressed._pending.target_position == 2
    assert regressed._pending.boundary_at_s == pytest.approx(70.5)


def test_transition_at_boundary_defers_to_next_boundary() -> None:
    coordinator = _coordinator(SwitchQuantization.BEAT)
    coordinator.update(
        _decision(), _timeline(1, next_beat_at=80.5), now_s=80.0, sync_offset_s=0.0
    )

    coordinator.update(
        _decision(), _timeline(2, next_beat_at=81.0), now_s=80.51,
        sync_offset_s=0.0, transition_active=True,
    )

    assert coordinator._pending is not None
    assert coordinator._pending.target_position == 3
    assert coordinator._pending.boundary_at_s == pytest.approx(81.0)


def test_public_diagnostics_report_position_pending_time_and_waiting_state() -> None:
    coordinator = _coordinator(SwitchQuantization.BEAT)
    state = _timeline(5, next_beat_at=90.5)
    coordinator.update(_decision(), state, now_s=90.0, sync_offset_s=-0.085)

    diagnostics = coordinator.diagnostics(
        state,
        now_s=90.1,
        sync_offset_s=-0.085,
        candidate_waiting=True,
    )

    assert diagnostics.mode is SwitchQuantization.BEAT
    assert diagnostics.beat_index == 5
    assert diagnostics.beat_in_bar == 1
    assert diagnostics.pending_animation == "next"
    assert diagnostics.next_switch_in_s == pytest.approx(0.315)
    assert diagnostics.waiting_for_clock is False
    assert diagnostics.alignment_estimated is True

    uncertain = replace(state, confidence=0.3)
    coordinator.update(_decision(), uncertain, now_s=90.2, sync_offset_s=-0.085)
    waiting = coordinator.diagnostics(
        uncertain,
        now_s=90.2,
        sync_offset_s=-0.085,
        candidate_waiting=True,
    )
    assert waiting.pending_animation is None
    assert waiting.waiting_for_clock is True


def test_live_sequencer_config_clears_pending_and_preserves_sync_offset() -> None:
    engine = PlaybackEngine(
        AnimationPlayer.party(),
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            switch_quantization=SwitchQuantization.BEAT,
        ),
    )
    engine.set_sync_offset(-0.085)
    engine._quantized_switcher.update(
        _decision(), _timeline(2, next_beat_at=100.5), now_s=100.0,
        sync_offset_s=engine.sync_offset_s,
    )
    assert engine._quantized_switcher.pending_decision is not None

    engine.set_sequencer_config(
        SwitchQuantization.PHRASE,
        MusicalTimelineConfig(bars_per_phrase=4),
    )

    diagnostics = engine.quantized_switch_diagnostics(now_s=100.1)
    assert engine.config.switch_quantization is SwitchQuantization.PHRASE
    assert engine.config.musical_timeline.bars_per_phrase == 4
    assert engine.sync_offset_s == pytest.approx(-0.085)
    assert diagnostics.pending_animation is None


def test_dynamic_selector_propose_and_commit_separate_cooldown_accounting() -> None:
    player = AnimationPlayer.party()
    start_index = player.index_of("aurora")
    assert start_index is not None
    player.set_index(start_index, transition_ms=0)
    selector = DynamicSelector(
        DynamicSelectorConfig(randomness=0.0, min_duration_s=0.0, switch_cooldown_s=0.0)
    )
    features = MusicFeatures(
        bpm=128.0,
        energy=0.9,
        energy_level=0.9,
        bass_energy=0.9,
        beat_confidence=0.9,
        drop_detected=True,
    )

    proposal = selector.propose(player, features, now_s=1.0, force_switch=True)

    assert proposal.should_switch is True
    assert player.current_index() == start_index
    assert selector.recent_names == []
    assert selector.last_switch_at_s == pytest.approx(1.0)

    assert selector.commit(player, proposal, now_s=5.0, transition_ms=0) is True
    assert player.name_at(player.current_index()) == proposal.selected_name
    assert selector.recent_names == ["aurora"]
    assert selector.last_switch_at_s == pytest.approx(5.0)


def test_dynamic_engine_quantizes_switch_after_immediate_initial_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    player = AnimationPlayer.party()
    engine = PlaybackEngine(
        player,
        PlaybackConfig(
            mode=PlaybackMode.DYNAMIC,
            switch_quantization=SwitchQuantization.BEAT,
            activity=MusicActivityConfig(activation_delay_s=0.0),
            dynamic=DynamicSelectorConfig(
                randomness=0.0,
                min_duration_s=0.0,
                switch_cooldown_s=0.0,
            ),
        ),
    )
    events = tuple(
        BeatEvent(sequence=index, timestamp=10.0 + (index - 1) * 0.5, strength=0.9)
        for index in range(1, 6)
    )
    active = AudioSnapshot.from_parts(
        AudioFrame(rms=0.8, beat=True, beat_strength=0.9, fresh=True),
        MusicFeatures(
            bpm=120.0,
            energy=0.8,
            volume=0.8,
            energy_level=0.8,
            bass_energy=0.85,
            mid_energy=0.4,
            treble_energy=0.3,
            beat=True,
            beat_strength=0.9,
            beat_confidence=0.9,
            silence=False,
        ),
        beat_events=events,
    )

    engine.step(Stripe(8), snapshot=active, now_s=12.0)
    assert engine.music_active is True
    assert engine.musical_timeline_state.confidence >= 0.65
    engine.player.cancel_transition()
    current_index = player.current_index()
    candidate_index = next(
        index
        for index in player.automatic_indices()
        if index != current_index
    )
    candidate_name = player.name_at(candidate_index)
    current_name = player.name_at(current_index)
    assert candidate_name is not None and current_name is not None

    def propose_candidate(
        selector: DynamicSelector,
        animation_player: AnimationPlayer,
        features: MusicFeatures,
        *,
        now_s: float,
        quiet: bool = False,
        force_switch: bool = False,
    ) -> SelectorDecision:
        del selector, features, quiet, force_switch
        current = animation_player.name_at(animation_player.current_index())
        return SelectorDecision(candidate_name, 1.0, current, current != candidate_name, "test")

    monkeypatch.setattr(DynamicSelector, "propose", propose_candidate)
    active_no_events = replace(active, beat_events=())

    engine.step(Stripe(8), snapshot=active_no_events, now_s=12.1)
    assert player.current_index() == current_index
    assert engine._quantized_switcher.pending_decision is not None
    engine.step(Stripe(8), snapshot=active_no_events, now_s=12.49)
    assert player.current_index() == current_index

    engine.step(Stripe(8), snapshot=active_no_events, now_s=12.51)
    assert player.name_at(player.current_index()) == candidate_name
    assert player.transition_active is True
