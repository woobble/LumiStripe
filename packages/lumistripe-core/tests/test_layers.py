from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pytest
from lumistripe import (
    Animation,
    AnimationPlayer,
    AnimationRole,
    AudioFrame,
    AudioSnapshot,
    Effect,
    EffectCategory,
    EffectScheduler,
    EffectSchedulerConfig,
    EffectTriggerResult,
    ElectricStorm,
    LayeredRenderer,
    MusicFeatures,
    PixelExplosion,
    Rgb,
    Rgba,
    Stripe,
    TransitionEffect,
    animation_metadata,
)
from lumistripe.audio import MusicStructurePhase, MusicStructureState
from lumistripe.audio.types import BeatClockState, BeatEvent
from lumistripe.controller import Controller
from lumistripe.effects import BeatWave
from lumistripe.effects.layers import ActiveEffect, EffectDefinition


@dataclass(slots=True)
class Red(Animation):
    @property
    def name(self) -> str:
        return "red"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(255, 0, 0))


@dataclass(slots=True)
class Blue(Animation):
    @property
    def name(self) -> str:
        return "blue"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(0, 0, 255))


@dataclass(slots=True)
class GreenEffect(Effect):
    @property
    def name(self) -> str:
        return "green_effect"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgba(0, 255, 0, 1.0))


def _event_snapshot() -> AudioSnapshot:
    frame = AudioFrame(
        rms=0.8,
        bands=(0.9, 0.8, 0.6, 0.5, 0.4, 0.7, 0.8, 0.7),
        beat=True,
        beat_strength=0.9,
        fresh=True,
    )
    features = MusicFeatures(
        energy=0.8,
        bass_energy=0.85,
        mid_energy=0.5,
        treble_energy=0.7,
        onset_strength=0.8,
        beat=True,
        beat_strength=0.9,
        beat_confidence=0.9,
        drop_detected=True,
        musical_impact=1.0,
    )
    return AudioSnapshot.from_parts(frame, features)


def test_dynamic_metadata_allows_strobes_while_preserving_effect_roles() -> None:
    assert animation_metadata("aurora").role is AnimationRole.BASE
    assert animation_metadata("beat_wave").role is AnimationRole.EFFECT
    assert isinstance(BeatWave(), Effect)
    for name in ("strobe", "rainbow_strobe", "red_blackout_strobe", "police"):
        assert animation_metadata(name).dynamic_safe is True

    player = AnimationPlayer.party()
    automatic = {player.name_at(index) for index in player.automatic_indices()}
    assert {"strobe", "rainbow_strobe", "red_blackout_strobe", "police"} <= automatic


def test_scheduler_limits_layers_to_one_effect_per_category() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()

    scheduler.update(player, _event_snapshot(), now_s=1.0)

    assert len(scheduler.active) == 2
    scheduler.update(player, _event_snapshot(), now_s=1.3)
    assert len(scheduler.active) == 2


def test_scheduler_consumes_timestamped_beat_event_only_once() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    event_snapshot = replace(
        _event_snapshot(),
        beat_events=(BeatEvent(991, 1.0, 0.9),),
        beat_event_stream=True,
    )

    scheduler.update(player, event_snapshot, now_s=1.0)
    assert scheduler._rhythmic_status.result is EffectTriggerResult.ACTIVATED

    scheduler.update(player, event_snapshot, now_s=3.0)

    assert scheduler._rhythmic_status.result is EffectTriggerResult.BELOW_THRESHOLD


def test_scheduler_does_not_fall_back_to_persistent_beat_from_event_stream() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    snapshot = replace(
        _event_snapshot(),
        beat_events=(BeatEvent(993, 1.0, 0.9),),
        beat_event_stream=True,
    )

    scheduler.update(player, snapshot, now_s=1.0)
    assert scheduler._rhythmic_status.result is EffectTriggerResult.ACTIVATED

    scheduler.update(player, replace(snapshot, beat_events=()), now_s=3.0)

    assert scheduler._rhythmic_status.result is EffectTriggerResult.BELOW_THRESHOLD


def test_scheduler_keeps_legacy_beat_fallback_without_event_stream() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    snapshot = _event_snapshot()

    scheduler.update(player, snapshot, now_s=1.0)
    scheduler.update(player, snapshot, now_s=3.0)

    assert scheduler._rhythmic_status.result is EffectTriggerResult.ACTIVATED


def test_scheduler_applies_positive_offset_to_timestamped_events() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    base = _event_snapshot()
    snapshot = replace(
        base,
        frame=replace(base.frame, beat=False),
        features=replace(base.features, beat=False),
        beat_events=(BeatEvent(992, 1.0, 0.9),),
    )

    scheduler.update(player, snapshot, now_s=1.05, sync_offset_s=0.1)
    assert scheduler._rhythmic_status.result is EffectTriggerResult.BELOW_THRESHOLD
    scheduler.update(
        player, replace(snapshot, beat_events=()), now_s=1.11, sync_offset_s=0.1
    )

    assert scheduler._rhythmic_status.result is EffectTriggerResult.ACTIVATED


def test_scheduler_reschedules_pending_event_when_sync_offset_changes() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    base = _event_snapshot()
    snapshot = replace(
        base,
        frame=replace(base.frame, beat=False),
        features=replace(base.features, beat=False),
        beat_events=(BeatEvent(994, 1.0, 0.9),),
        beat_event_stream=True,
    )

    scheduler.update(player, snapshot, now_s=1.05, sync_offset_s=0.1)
    scheduler.update(player, replace(snapshot, beat_events=()), now_s=1.15, sync_offset_s=0.2)
    assert scheduler._rhythmic_status.result is EffectTriggerResult.BELOW_THRESHOLD

    scheduler.update(player, replace(snapshot, beat_events=()), now_s=1.21, sync_offset_s=0.2)

    assert scheduler._rhythmic_status.result is EffectTriggerResult.ACTIVATED


def test_scheduler_predicts_negative_offset_before_next_beat() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    snapshot = replace(
        _event_snapshot(),
        frame=AudioFrame(rms=0.8, bands=(0.9, 0.8, 0.6, 0.5, 0.4, 0.7, 0.8, 0.7)),
        features=replace(_event_snapshot().features, beat=False, drop_detected=False),
        beat_clock=BeatClockState(
            bpm=120.0,
            phase=0.9,
            confidence=0.9,
            beat_index=5,
            last_beat_at=1.5,
            next_beat_at=2.0,
        ),
    )

    scheduler.update(player, snapshot, now_s=1.95, sync_offset_s=-0.1)

    assert scheduler._rhythmic_status.result is EffectTriggerResult.ACTIVATED
    scheduler.update(player, _event_snapshot(), now_s=1.3)
    assert len(scheduler.active) == 2


def test_scheduler_deduplicates_detected_onsets_by_musical_grid_index() -> None:
    scheduler = EffectScheduler(
        EffectSchedulerConfig(seed=4, subdivisions_per_beat=4)
    )
    player = AnimationPlayer.party()
    period = 60.0 / 128.0
    clock = BeatClockState(
        bpm=128.0,
        phase=0.85,
        confidence=0.95,
        beat_index=10,
        last_beat_at=1.0,
        next_beat_at=1.0 + period,
    )
    event = BeatEvent(1001, 1.0 + period, 0.9)
    snapshot = replace(
        _event_snapshot(),
        beat_events=(event,),
        beat_event_stream=True,
        beat_clock=clock,
    )

    scheduler.update(player, snapshot, now_s=1.4)

    assert tuple(scheduler._recent_grid_indices) == (44,)
    assert len(scheduler._pending_beats) == 1
    assert scheduler._pending_beats[0].event == event

    scheduler.update(player, replace(snapshot, beat_events=()), now_s=1.48)
    scheduler.update(
        player,
        replace(snapshot, beat_events=(BeatEvent(1002, event.timestamp, 0.9),)),
        now_s=1.5,
    )

    assert tuple(scheduler._recent_grid_indices) == (44,)
    assert not scheduler._pending_beats
    assert scheduler._rhythmic_status.result is EffectTriggerResult.BELOW_THRESHOLD


def test_scheduler_keeps_sixteenth_grid_ticks_separate_from_visual_cooldowns() -> None:
    scheduler = EffectScheduler(
        EffectSchedulerConfig(seed=4, subdivisions_per_beat=4)
    )
    player = AnimationPlayer.party()
    base = replace(
        _event_snapshot(),
        frame=replace(_event_snapshot().frame, beat=False, beat_strength=0.0),
        features=replace(_event_snapshot().features, beat=False, beat_strength=0.0),
        beat_events=(),
        beat_event_stream=True,
    )
    beat_period = 60.0 / 128.0
    results: list[EffectTriggerResult] = []

    for frame in range(76):
        now_s = frame * 0.02
        beat_index = int(now_s / beat_period)
        last_beat_at = beat_index * beat_period
        clock = BeatClockState(
            bpm=128.0,
            phase=(now_s - last_beat_at) / beat_period,
            confidence=0.95,
            beat_index=beat_index,
            last_beat_at=last_beat_at,
            next_beat_at=last_beat_at + beat_period,
        )
        scheduler.update(
            player,
            replace(base, beat_clock=clock),
            now_s=now_s,
        )
        results.append(scheduler._rhythmic_status.result)

    assert tuple(scheduler._recent_grid_indices) == tuple(range(1, 14))
    assert EffectTriggerResult.ACTIVATED in results
    assert any(
        result in {EffectTriggerResult.COOLDOWN, EffectTriggerResult.CATEGORY_ACTIVE}
        for result in results
    )


def test_scheduler_catches_up_grid_indices_after_a_late_render() -> None:
    scheduler = EffectScheduler(
        EffectSchedulerConfig(seed=4, subdivisions_per_beat=4)
    )
    base = replace(
        _event_snapshot(),
        frame=replace(_event_snapshot().frame, beat=False, beat_strength=0.0),
        features=replace(_event_snapshot().features, beat=False, beat_strength=0.0),
        beat_events=(),
        beat_event_stream=True,
    )
    period = 60.0 / 128.0
    start_clock = BeatClockState(
        bpm=128.0,
        phase=0.0,
        confidence=0.95,
        beat_index=0,
        last_beat_at=0.0,
        next_beat_at=period,
    )
    late_clock = replace(start_clock, phase=0.85)

    scheduler.update(
        AnimationPlayer.party(), replace(base, beat_clock=start_clock), now_s=0.0
    )
    scheduler.update(
        AnimationPlayer.party(), replace(base, beat_clock=late_clock), now_s=0.4
    )

    assert tuple(scheduler._recent_grid_indices) == (1, 2, 3, 4)
    assert [beat.grid_index for beat in scheduler._pending_beats] == [4]
    diagnostics = scheduler.diagnostics()
    assert diagnostics.late_ticks_dropped == 2
    assert diagnostics.late_ticks_coalesced == 0

    scheduler.update(
        AnimationPlayer.party(), replace(base, beat_clock=late_clock), now_s=0.48
    )
    assert not scheduler._pending_beats
    assert scheduler.diagnostics().late_ticks_dropped == 2


def test_scheduler_coalesces_fresh_overdue_events_and_reports_jitter() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    snapshot = replace(
        _event_snapshot(),
        beat_events=(
            BeatEvent(1010, 0.36, 0.9),
            BeatEvent(1011, 0.38, 0.9),
            BeatEvent(1012, 0.39, 0.9),
        ),
        beat_event_stream=True,
        beat_clock=None,
    )

    scheduler.update(player, snapshot, now_s=0.4)

    diagnostics = scheduler.diagnostics()
    assert not scheduler._pending_beats
    assert diagnostics.late_ticks_dropped == 0
    assert diagnostics.late_ticks_coalesced == 2
    assert diagnostics.beat_lateness_p95_ms == pytest.approx(10.0)


def test_scheduler_suppresses_wild_effects_during_quiet_passages() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    quiet_event = replace(
        _event_snapshot(),
        features=replace(_event_snapshot().features, musical_impact=0.3),
    )

    scheduler.update(AnimationPlayer.party(), quiet_event, now_s=1.0)

    assert scheduler.active == []
    diagnostics = scheduler.diagnostics()
    assert diagnostics.rhythmic.result is EffectTriggerResult.BELOW_THRESHOLD
    assert diagnostics.accent.result is EffectTriggerResult.BELOW_THRESHOLD


def test_scheduler_response_controls_quiet_floor_and_impact_threshold() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))

    scheduler.set_response(0.0)
    assert scheduler.quiet_floor == pytest.approx(0.65)
    assert scheduler.impact_threshold == pytest.approx(0.85)

    scheduler.set_response(1.0)
    assert scheduler.quiet_floor == pytest.approx(0.25)
    assert scheduler.impact_threshold == pytest.approx(0.60)

    with pytest.raises(ValueError, match="response must be between zero and one"):
        scheduler.set_response(1.01)


def _structure_drop_snapshot(*, clock_confidence: float = 0.9) -> AudioSnapshot:
    base = _event_snapshot()
    return replace(
        base,
        frame=AudioFrame(fresh=True),
        features=replace(
            base.features,
            beat=False,
            beat_strength=0.0,
            beat_confidence=0.0,
            onset_strength=0.0,
            drop_detected=False,
            musical_impact=0.7,
        ),
        beat_events=(),
        beat_event_stream=True,
        beat_clock=BeatClockState(
            bpm=120.0,
            confidence=clock_confidence,
            next_beat_at=2.0,
        ),
    )


def test_structure_drop_accent_uses_next_beat_and_negative_sync_offset() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    base_snapshot = _structure_drop_snapshot()
    snapshot = replace(
        base_snapshot,
        features=replace(
            base_snapshot.features,
            onset_strength=0.8,
            drop_detected=True,
        ),
    )
    drop = MusicStructureState(
        phase=MusicStructurePhase.DROP,
        confidence=0.95,
        buildup_progress=1.0,
        drop_event=True,
    )

    scheduler.update(
        player,
        snapshot,
        now_s=1.0,
        sync_offset_s=-0.085,
        structure_state=drop,
    )
    assert scheduler._pending_drop_accent_at_s == pytest.approx(1.915)
    assert scheduler.diagnostics().drop_accent.status.value == "pending"
    assert scheduler.active_names == ()

    scheduler.update(
        player,
        snapshot,
        now_s=1.916,
        sync_offset_s=-0.085,
        structure_state=replace(drop, drop_event=False),
    )

    assert scheduler.active_names == ("drop_explosion",)
    assert scheduler.active[0].intensity == 1.0
    assert scheduler.diagnostics().accent.result is EffectTriggerResult.ACTIVATED
    assert scheduler.diagnostics().drop_accent.status.value == "activated"

    scheduler.update(
        player,
        snapshot,
        now_s=1.92,
        sync_offset_s=-0.085,
        structure_state=replace(drop, drop_event=False),
    )
    assert scheduler.active_names == ("drop_explosion",)


def test_pending_structure_drop_tracks_sync_offset_changes() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    snapshot = _structure_drop_snapshot()
    drop = MusicStructureState(
        phase=MusicStructurePhase.DROP,
        confidence=0.95,
        buildup_progress=1.0,
        drop_event=True,
    )

    scheduler.update(player, snapshot, now_s=1.0, structure_state=drop)
    assert scheduler._pending_drop_accent_at_s == pytest.approx(2.0)

    scheduler.update(
        player,
        snapshot,
        now_s=1.5,
        sync_offset_s=-0.085,
        structure_state=replace(drop, drop_event=False),
    )

    assert scheduler._pending_drop_accent_at_s == pytest.approx(1.915)


def test_structure_drop_accent_is_skipped_when_late_or_clock_is_uncertain() -> None:
    drop = MusicStructureState(
        phase=MusicStructurePhase.DROP,
        confidence=0.95,
        buildup_progress=1.0,
        drop_event=True,
    )

    late_scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    late_snapshot = _structure_drop_snapshot()
    late_scheduler.update(
        player,
        late_snapshot,
        now_s=1.0,
        sync_offset_s=-0.085,
        structure_state=drop,
    )
    late_scheduler.update(
        player,
        late_snapshot,
        now_s=2.1,
        sync_offset_s=-0.085,
        structure_state=replace(drop, drop_event=False),
    )
    assert late_scheduler.active_names == ()
    assert late_scheduler.diagnostics().late_ticks_dropped == 1
    assert late_scheduler.diagnostics().drop_accent.status.value == "skipped"
    assert late_scheduler.diagnostics().drop_accent.reason == "late"

    uncertain_scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    uncertain_scheduler.update(
        player,
        _structure_drop_snapshot(clock_confidence=0.5),
        now_s=1.0,
        structure_state=drop,
    )
    assert uncertain_scheduler._pending_drop_accent_at_s is None
    assert uncertain_scheduler.active_names == ()
    assert uncertain_scheduler.diagnostics().drop_accent.status.value == "skipped"
    assert uncertain_scheduler.diagnostics().drop_accent.reason == "beat-clock-uncertain"


@pytest.mark.parametrize(
    ("state", "expected_result"),
    (
        (None, EffectTriggerResult.BELOW_THRESHOLD),
        (
            MusicStructureState(
                phase=MusicStructurePhase.BREAKDOWN,
                confidence=0.9,
            ),
            EffectTriggerResult.BELOW_THRESHOLD,
        ),
        (
            MusicStructureState(
                phase=MusicStructurePhase.BUILDUP,
                confidence=0.9,
                buildup_progress=1.0,
            ),
            EffectTriggerResult.ACTIVATED,
        ),
    ),
)
def test_structure_phase_adjusts_effect_trigger_activity(
    state: MusicStructureState | None,
    expected_result: EffectTriggerResult,
) -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    base = _event_snapshot()
    snapshot = replace(
        base,
        frame=AudioFrame(fresh=True),
        features=replace(
            base.features,
            beat=False,
            beat_strength=0.0,
            beat_confidence=0.0,
            onset_strength=0.6,
            drop_detected=False,
            musical_impact=0.75,
        ),
        beat_events=(),
        beat_event_stream=True,
        beat_clock=None,
    )

    scheduler.update(player, snapshot, now_s=1.0, structure_state=state)

    assert scheduler.diagnostics().accent.result is expected_result


def test_structure_modulation_keeps_base_output_within_player_brightness() -> None:
    def render_with_state(state: MusicStructureState | None) -> int:
        player = AnimationPlayer()
        player.add(Red(), 50, 10)
        player.set_brightness(0.4)
        renderer = LayeredRenderer(EffectScheduler(EffectSchedulerConfig(seed=2)))
        snapshot = AudioSnapshot.from_parts(
            AudioFrame(fresh=True), MusicFeatures(musical_impact=1.0)
        )
        stripe = Stripe(4)
        renderer.render(player, stripe, snapshot, now_s=1.0, structure_state=state)
        return int(stripe.pixels()[0, 0])

    groove = render_with_state(
        MusicStructureState(phase=MusicStructurePhase.GROOVE, confidence=0.9)
    )
    breakdown = render_with_state(
        MusicStructureState(phase=MusicStructurePhase.BREAKDOWN, confidence=0.9)
    )
    buildup = render_with_state(
        MusicStructureState(
            phase=MusicStructurePhase.BUILDUP,
            confidence=0.9,
            buildup_progress=0.5,
        )
    )

    assert breakdown < buildup < groove
    assert groove <= 102


def test_scheduler_diagnostics_describe_layers_budget_and_triggers() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    scheduler.update(AnimationPlayer.party(), _event_snapshot(), now_s=1.0)

    diagnostics = scheduler.diagnostics(now_s=1.25)

    assert len(diagnostics.active) == 2
    assert {effect.category for effect in diagnostics.active} == {
        EffectCategory.RHYTHMIC,
        EffectCategory.ACCENT,
    }
    assert all(effect.elapsed_s == pytest.approx(0.25) for effect in diagnostics.active)
    assert all(0.0 < effect.progress < 1.0 for effect in diagnostics.active)
    assert diagnostics.overlay_strength <= diagnostics.overlay_limit
    assert diagnostics.rhythmic.result is EffectTriggerResult.ACTIVATED
    assert diagnostics.accent.result is EffectTriggerResult.ACTIVATED
    assert diagnostics.rhythmic_cooldown_remaining_s == pytest.approx(0.0)
    assert diagnostics.accent_cooldown_remaining_s == pytest.approx(1.0)


def test_scheduler_diagnostics_explain_suppressed_triggers() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()
    snapshot = _event_snapshot()
    scheduler.update(player, snapshot, now_s=1.0)

    scheduler.update(player, snapshot, now_s=1.1)
    active = scheduler.diagnostics()
    assert active.rhythmic.result is EffectTriggerResult.CATEGORY_ACTIVE
    assert active.accent.result is EffectTriggerResult.CATEGORY_ACTIVE

    scheduler.clear_active()
    scheduler.update(player, snapshot, now_s=1.2, allow_triggers=False)
    transition = scheduler.diagnostics()
    assert transition.rhythmic.result is EffectTriggerResult.TRANSITION
    assert transition.accent.result is EffectTriggerResult.TRANSITION

    scheduler.update(player, AudioSnapshot.silence(), now_s=2.0)
    quiet = scheduler.diagnostics()
    assert quiet.rhythmic.result is EffectTriggerResult.BELOW_THRESHOLD
    assert quiet.accent.result is EffectTriggerResult.BELOW_THRESHOLD


@pytest.mark.parametrize(
    "kwargs",
    (
        {"max_active_effects": 3},
        {"max_overlay_strength": 0.0},
        {"beat_confidence_threshold": 1.1},
        {"accent_cooldown_s": -0.1},
    ),
)
def test_scheduler_config_rejects_invalid_values(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        EffectSchedulerConfig(**kwargs)  # type: ignore[arg-type]


def test_scheduler_effects_expire_and_can_trigger_again() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=1))
    player = AnimationPlayer.party()
    scheduler.update(player, _event_snapshot(), now_s=0.0)

    scheduler.update(player, _event_snapshot(), now_s=3.0)

    assert scheduler.active_names
    assert len(scheduler.active_names) <= 2


def test_layered_renderer_composes_base_and_two_effects() -> None:
    player = AnimationPlayer.party()
    index = player.index_of("aurora")
    assert index is not None
    player.set_index(index, transition_ms=0)
    renderer = LayeredRenderer(EffectScheduler(EffectSchedulerConfig(seed=2)))
    stripe = Stripe(42)

    renderer.render(player, stripe, _event_snapshot(), now_s=1.0)

    assert len(renderer.active_effect_names) == 2
    assert stripe.pixels()[:, :3].max() > 0
    np.testing.assert_array_equal(stripe.pixels()[:, 3], np.full(42, 255))


def test_layered_renderer_keeps_quiet_base_but_reserves_full_brightness_for_peaks() -> None:
    def render_at(impact: float) -> int:
        player = AnimationPlayer()
        player.add(Red(), 50, 10)
        renderer = LayeredRenderer(EffectScheduler(EffectSchedulerConfig(seed=2)))
        stripe = Stripe(4)
        snapshot = AudioSnapshot.from_parts(
            AudioFrame(fresh=True),
            MusicFeatures(musical_impact=impact),
        )
        renderer.render(player, stripe, snapshot, now_s=1.0)
        return int(stripe.pixels()[0, 0])

    quiet_red = render_at(0.2)
    peak_red = render_at(1.0)

    assert 100 < quiet_red < 180
    assert peak_red == 255


def test_player_crossfades_automatic_switches() -> None:
    player = AnimationPlayer(transition_ms=100)
    player.add(Red(), 50, 10)
    player.add(Blue(), 50, 10)
    stripe = Stripe(4)
    player.step(stripe, now_s=0.0)

    player.set_index(1, now_s=1.0)
    assert player.transition_progress_at(1.0) == pytest.approx(0.0)
    player.step(stripe, now_s=1.0)
    player.step(stripe, now_s=1.05)
    assert player.transition_progress_at(1.05) == pytest.approx(0.5)
    midpoint = stripe.pixels()[0, :3].copy()
    player.step(stripe, now_s=1.1)
    assert player.transition_progress_at(1.1) == pytest.approx(1.0)

    assert 110 <= int(midpoint[0]) <= 140
    assert midpoint[1] == 0
    assert 110 <= int(midpoint[2]) <= 140
    np.testing.assert_array_equal(stripe.pixels()[:, :3], [[0, 0, 255]] * 4)


def test_interrupted_transition_starts_from_currently_visible_frame() -> None:
    player = AnimationPlayer(transition_ms=100)
    player.add(Red(), 50, 10)
    player.add(Blue(), 50, 10)
    player.add(Red(), 50, 10)
    stripe = Stripe(2)
    player.step(stripe, now_s=0.0)

    player.set_index(1, now_s=1.0)
    player.step(stripe, now_s=1.05)
    visible = stripe.pixels().copy()

    player.set_index(2, now_s=1.06)
    player.step(stripe, now_s=1.06)

    np.testing.assert_array_equal(stripe.pixels(), visible)
    assert player.transition_progress_at(1.06) == pytest.approx(0.0)


def test_transition_progress_uses_elapsed_time_across_irregular_frames() -> None:
    player = AnimationPlayer(transition_ms=200)
    player.add(Red(), 20, 10)
    player.add(Blue(), 80, 10)
    stripe = Stripe(1)
    player.step(stripe, now_s=2.0)
    player.set_index(1, now_s=3.0)

    player.step(stripe, now_s=3.03)
    assert player.transition_progress_at(3.03) == pytest.approx(0.15)
    player.step(stripe, now_s=3.17)
    assert player.transition_progress_at(3.17) == pytest.approx(0.85)
    player.step(stripe, now_s=3.2)

    np.testing.assert_array_equal(stripe.pixels()[0, :3], [0, 0, 255])


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (TransitionEffect.CROSSFADE, {
            0.0: [255, 0, 0, 255],
            0.25: [191, 0, 63, 255],
            0.5: [127, 0, 127, 255],
            0.75: [63, 0, 191, 255],
            1.0: [0, 0, 255, 255],
        }),
        (TransitionEffect.FADE_BLACK, {
            0.0: [255, 0, 0, 255],
            0.25: [127, 0, 0, 255],
            0.5: [0, 0, 0, 255],
            0.75: [0, 0, 127, 255],
            1.0: [0, 0, 255, 255],
        }),
    ],
)
def test_transition_effects_render_deterministic_frames_at_elapsed_progress(
    effect: TransitionEffect, expected: dict[float, list[int]]
) -> None:
    source = np.tile(np.array([255, 0, 0, 255], dtype=np.uint8), (4, 1))
    incoming = np.tile(np.array([0, 0, 255, 255], dtype=np.uint8), (4, 1))

    for progress, color in expected.items():
        player = AnimationPlayer(transition_effect=effect)
        stripe = Stripe(4)
        player.begin_transition(duration_ms=100, source=source, now_s=2.0)
        stripe.set_pixels(incoming)

        player.apply_transition(stripe, now_s=2.0 + progress * 0.1)

        np.testing.assert_array_equal(stripe.pixels(), np.tile(color, (4, 1)))


def test_fade_black_preserves_partial_alpha_at_transition_endpoints() -> None:
    source = np.array([[180, 30, 20, 128], [90, 120, 240, 64]], dtype=np.uint8)
    incoming = np.array([[15, 200, 50, 192], [255, 100, 10, 160]], dtype=np.uint8)
    player = AnimationPlayer(transition_effect=TransitionEffect.FADE_BLACK)
    stripe = Stripe(2)
    player.begin_transition(duration_ms=100, source=source, now_s=0.0)

    stripe.set_pixels(incoming)
    player.apply_transition(stripe, now_s=0.0)
    np.testing.assert_array_equal(stripe.pixels(), source)

    stripe.set_pixels(incoming)
    player.apply_transition(stripe, now_s=0.05)
    np.testing.assert_array_equal(stripe.pixels()[:, :3], np.zeros((2, 3), dtype=np.uint8))
    np.testing.assert_array_equal(stripe.pixels()[:, 3], source[:, 3])

    stripe.set_pixels(incoming)
    player.apply_transition(stripe, now_s=0.1)
    np.testing.assert_array_equal(stripe.pixels(), incoming)


@pytest.mark.parametrize(
    ("progress", "indices", "expected"),
    [
        (0.0, [0, 24, 25, 26, 99], [[255, 0, 0, 255]] * 5),
        (0.25, [0, 23, 24, 25, 26, 99], [
            [0, 0, 255, 255], [0, 0, 255, 255], [63, 0, 191, 255],
            [191, 0, 63, 255], [255, 0, 0, 255], [255, 0, 0, 255],
        ]),
        (0.5, [0, 48, 49, 50, 51, 52, 99], [
            [0, 0, 255, 255], [0, 0, 255, 255], [63, 0, 191, 255],
            [191, 0, 63, 255], [255, 0, 0, 255], [255, 0, 0, 255],
            [255, 0, 0, 255],
        ]),
        (0.75, [0, 73, 74, 75, 76, 99], [
            [0, 0, 255, 255], [0, 0, 255, 255], [63, 0, 191, 255],
            [191, 0, 63, 255], [255, 0, 0, 255], [255, 0, 0, 255],
        ]),
        (1.0, [0, 24, 25, 26, 99], [[0, 0, 255, 255]] * 5),
    ],
)
def test_wipe_reveals_incoming_frame_left_to_right_with_a_soft_edge(
    progress: float,
    indices: list[int],
    expected: list[list[int]],
) -> None:
    source = np.tile(np.array([255, 0, 0, 255], dtype=np.uint8), (100, 1))
    incoming = np.tile(np.array([0, 0, 255, 255], dtype=np.uint8), (100, 1))
    player = AnimationPlayer(transition_effect=TransitionEffect.WIPE)
    stripe = Stripe(100)
    player.begin_transition(duration_ms=100, source=source, now_s=3.0)
    stripe.set_pixels(incoming)

    player.apply_transition(stripe, now_s=3.0 + progress * 0.1)

    np.testing.assert_array_equal(stripe.pixels()[indices], expected)


@pytest.mark.parametrize("length", [1, 2, 39, 40, 401, 4096])
def test_wipe_supports_different_strip_lengths(length: int) -> None:
    source = np.tile(np.array([255, 0, 0, 255], dtype=np.uint8), (length, 1))
    incoming = np.tile(np.array([0, 0, 255, 255], dtype=np.uint8), (length, 1))
    player = AnimationPlayer(transition_effect=TransitionEffect.WIPE)
    stripe = Stripe(length)
    player.begin_transition(duration_ms=100, source=source, now_s=0.0)
    stripe.set_pixels(incoming)

    player.apply_transition(stripe, now_s=0.05)

    assert stripe.pixels().shape == (length, 4)
    if length == 1:
        np.testing.assert_array_equal(stripe.pixels()[0], [127, 0, 127, 255])
    else:
        np.testing.assert_array_equal(stripe.pixels()[0], incoming[0])
        np.testing.assert_array_equal(stripe.pixels()[-1], source[-1])


def test_transition_effect_is_latched_until_the_active_transition_finishes() -> None:
    source = np.tile(np.array([255, 0, 0, 255], dtype=np.uint8), (2, 1))
    incoming = np.tile(np.array([0, 0, 255, 255], dtype=np.uint8), (2, 1))
    player = AnimationPlayer(transition_effect=TransitionEffect.FADE_BLACK)
    stripe = Stripe(2)
    stripe.set_pixels(incoming)
    player.begin_transition(duration_ms=100, source=source, now_s=1.0)

    player.apply_transition(stripe, now_s=1.025)
    before_setting_change = stripe.pixels().copy()
    player.transition_effect = TransitionEffect.WIPE
    player.apply_transition(stripe, now_s=1.025)

    np.testing.assert_array_equal(stripe.pixels(), before_setting_change)


@pytest.mark.parametrize("effect", list(TransitionEffect))
def test_interrupted_effect_transition_starts_from_the_visible_frame(
    effect: TransitionEffect,
) -> None:
    source = np.tile(np.array([255, 0, 0, 255], dtype=np.uint8), (2, 1))
    incoming = np.tile(np.array([0, 0, 255, 255], dtype=np.uint8), (2, 1))
    next_frame = np.tile(np.array([0, 255, 0, 255], dtype=np.uint8), (2, 1))
    player = AnimationPlayer(transition_effect=effect)
    stripe = Stripe(2)
    stripe.set_pixels(incoming)
    player.begin_transition(duration_ms=100, source=source, now_s=1.0)
    player.apply_transition(stripe, now_s=1.025)
    visible = stripe.pixels().copy()

    player.begin_transition(duration_ms=100, source=visible, now_s=1.03)
    stripe.set_pixels(next_frame)
    player.apply_transition(stripe, now_s=1.03)

    np.testing.assert_array_equal(stripe.pixels(), visible)


def test_layered_renderer_crossfades_after_composing_the_incoming_frame() -> None:
    player = AnimationPlayer(transition_ms=100)
    player.add(Red(), 50, 10)
    player.add(Blue(), 50, 10)
    stripe = Stripe(2)
    renderer = LayeredRenderer(EffectScheduler(EffectSchedulerConfig(seed=1)))
    quiet = AudioSnapshot.from_parts(
        AudioFrame(), MusicFeatures(musical_impact=1.0)
    )

    renderer.render(player, stripe, quiet, now_s=0.0)
    renderer.scheduler.active.append(
        ActiveEffect(
            EffectDefinition("green_effect", EffectCategory.ACCENT, 1.0, 1.0),
            GreenEffect(),
            0.01,
            AudioFrame(),
        )
    )
    renderer.render(player, stripe, quiet, now_s=0.01)
    np.testing.assert_array_equal(stripe.pixels()[0, :3], [255, 191, 0])

    player.set_index(1, transition_ms=0)
    player.begin_transition(duration_ms=100, source=stripe.pixels(), now_s=1.0)
    renderer.render(player, stripe, quiet, now_s=1.05)

    np.testing.assert_array_equal(stripe.pixels()[0, :3], [127, 95, 127])


def test_reselection_replaces_stateful_animation_instance() -> None:
    player = AnimationPlayer.party()
    index = player.index_of("pixel_explosion")
    assert index is not None
    player.set_index(index, transition_ms=0)
    player.step(Stripe(8), audio_frame=_event_snapshot().frame)
    before = player.animations[index].animation
    assert isinstance(before, PixelExplosion)
    assert before.bursts

    player.set_index(index, transition_ms=0)
    after = player.animations[index].animation

    assert after is not before
    assert isinstance(after, PixelExplosion)
    assert after.bursts == []


def test_reactive_event_collections_remain_bounded() -> None:
    frame = _event_snapshot().frame
    stripe = Stripe(42)
    pixel = PixelExplosion()
    wave = BeatWave()
    storm = ElectricStorm()

    for tick in range(200):
        pixel.tick_audio(tick, stripe, frame)
        wave.tick_audio(tick, stripe, frame)
        storm.tick_audio(tick, stripe, frame)

    assert len(pixel.bursts) <= 12
    assert len(wave.waves) <= 8
    assert len(storm.streaks) <= 8


def test_non_flashing_bases_are_temporally_stable_under_steady_audio() -> None:
    steady = AudioFrame(
        rms=0.45,
        bands=(0.5, 0.45, 0.4, 0.38, 0.35, 0.3, 0.28, 0.25),
    )
    for entry in AnimationPlayer.party().animations:
        metadata = animation_metadata(entry.animation)
        if metadata.role is not AnimationRole.BASE or metadata.tags & {
            "flash",
            "strobe",
        }:
            continue
        stripe = Stripe(42)
        previous: np.ndarray | None = None
        deltas: list[float] = []
        for frame in range(120):
            entry.animation.tick_audio(frame, stripe, steady)
            pixels = stripe.pixels()
            visible = pixels[:, :3].astype(np.float32) * (
                pixels[:, 3:4].astype(np.float32) / 255.0
            )
            if previous is not None:
                deltas.append(float(np.abs(visible - previous).mean()))
            previous = visible.copy()
        assert np.percentile(deltas, 95) <= 52.0, entry.animation.name
