import pytest

from lumistripe import (
    AnimationPlayer,
    AnimationScoringEngine,
    AutoSelectorConfig,
    DJModeSelector,
    MusicFeatures,
    Shockwave,
    animation_metadata,
)


def _bass_drop_features() -> MusicFeatures:
    return MusicFeatures(
        bpm=128.0,
        bpm_confidence=0.8,
        energy=0.9,
        volume=0.9,
        energy_level=0.9,
        bass=0.9,
        bass_energy=0.9,
        mid_energy=0.3,
        treble_energy=0.2,
        beat=True,
        beat_strength=0.9,
        beat_confidence=0.9,
        drop_detected=True,
    )


def test_animation_metadata_defaults_for_unknown_animation() -> None:
    metadata = animation_metadata("unknown_effect")

    assert metadata.name == "unknown_effect"
    assert metadata.energy_min == 0.0
    assert metadata.energy_max == 1.0
    assert metadata.weight == pytest.approx(1.0)


def test_representative_animation_metadata_is_available() -> None:
    metadata = animation_metadata(Shockwave())

    assert metadata.name == "shockwave"
    assert metadata.supports_drops is True
    assert metadata.prefers_bass > 0.0


def test_scoring_engine_prefers_drop_animation_on_drop() -> None:
    player = AnimationPlayer.party()
    engine = AnimationScoringEngine(AutoSelectorConfig(randomness=0.0, seed=1))

    ranked = engine.rank(player.animations, _bass_drop_features())

    assert ranked[0].name in {"shockwave", "bass_drop", "drop_wave", "drop_explosion"}
    assert ranked[0].score > ranked[-1].score
    assert ranked[0].reasons


def test_scoring_engine_penalizes_recent_animation() -> None:
    engine = AnimationScoringEngine(AutoSelectorConfig(randomness=0.0))
    player = AnimationPlayer.party()
    shockwave = next(entry.animation for entry in player.animations if entry.animation.name == "shockwave")

    fresh = engine.score_animation(shockwave, _bass_drop_features())
    fatigued = engine.score_animation(shockwave, _bass_drop_features(), recent_names=("shockwave",))

    assert fatigued.score < fresh.score


def test_dj_selector_honors_min_duration_before_switching() -> None:
    player = AnimationPlayer.party()
    player.set_index(player.index_of("aurora") or 0)
    selector = DJModeSelector(AutoSelectorConfig(randomness=0.0, min_duration_s=12, switch_cooldown_s=8))

    first = selector.update(player, _bass_drop_features(), now_s=0.0)
    early = selector.update(player, _bass_drop_features(), now_s=5.0)

    assert first.should_switch is False
    assert early.should_switch is False
    assert player.name_at(player.current_index()) == "aurora"


def test_dj_selector_can_switch_on_drop_after_cooldown() -> None:
    player = AnimationPlayer.party()
    player.set_index(player.index_of("aurora") or 0)
    selector = DJModeSelector(AutoSelectorConfig(randomness=0.0, min_duration_s=12, switch_cooldown_s=8))

    selector.update(player, _bass_drop_features(), now_s=0.0)
    decision = selector.update(player, _bass_drop_features(), now_s=16.0)

    assert decision.should_switch is True
    assert decision.reason == "drop"
    assert player.name_at(player.current_index()) in {"shockwave", "bass_drop", "drop_wave", "drop_explosion"}
