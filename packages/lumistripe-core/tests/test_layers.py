from __future__ import annotations

from dataclasses import dataclass

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
    Stripe,
    animation_metadata,
)
from lumistripe.controller import Controller
from lumistripe.effects import BeatWave


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
    )
    return AudioSnapshot.from_parts(frame, features)


def test_dynamic_roles_keep_effects_and_strobes_out_of_base_selection() -> None:
    assert animation_metadata("aurora").role is AnimationRole.BASE
    assert animation_metadata("beat_wave").role is AnimationRole.EFFECT
    assert isinstance(BeatWave(), Effect)
    for name in ("strobe", "rainbow_strobe", "police"):
        assert animation_metadata(name).dynamic_safe is False

    player = AnimationPlayer.party()
    automatic = {player.name_at(index) for index in player.automatic_indices()}
    assert {"strobe", "rainbow_strobe", "police"} <= automatic


def test_scheduler_limits_layers_to_one_effect_per_category() -> None:
    scheduler = EffectScheduler(EffectSchedulerConfig(seed=4))
    player = AnimationPlayer.party()

    scheduler.update(player, _event_snapshot(), now_s=1.0)

    assert len(scheduler.active) == 2
    assert {effect.definition.category for effect in scheduler.active} == {
        EffectCategory.RHYTHMIC,
        EffectCategory.ACCENT,
    }
    scheduler.update(player, _event_snapshot(), now_s=1.3)
    assert len(scheduler.active) == 2


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


def test_player_crossfades_automatic_switches() -> None:
    player = AnimationPlayer(transition_ms=100)
    player.add(Red(), 50, 10)
    player.add(Blue(), 50, 10)
    stripe = Stripe(4)
    player.step(stripe)

    player.set_index(1)
    assert player.transition_progress == pytest.approx(0.0)
    player.step(stripe)
    assert player.transition_progress == pytest.approx(0.5)
    midpoint = stripe.pixels()[0, :3].copy()
    player.step(stripe)
    assert player.transition_progress == pytest.approx(1.0)

    assert 110 <= int(midpoint[0]) <= 140
    assert midpoint[1] == 0
    assert 110 <= int(midpoint[2]) <= 140
    np.testing.assert_array_equal(stripe.pixels()[:, :3], [[0, 0, 255]] * 4)


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


def test_dynamic_bases_are_temporally_stable_under_steady_audio() -> None:
    steady = AudioFrame(
        rms=0.45,
        bands=(0.5, 0.45, 0.4, 0.38, 0.35, 0.3, 0.28, 0.25),
    )
    for entry in AnimationPlayer.party().animations:
        metadata = animation_metadata(entry.animation)
        if metadata.role is not AnimationRole.BASE or not metadata.dynamic_safe:
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
