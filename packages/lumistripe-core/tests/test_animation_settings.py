from __future__ import annotations

import numpy as np
import pytest
from lumistripe import AnimationPlayer, AudioFrame, Comet, Pulse, Stripe
from lumistripe.animation.catalog import animation_catalog

LOUD_AUDIO = AudioFrame(
    rms=0.9,
    bands=(0.95, 0.9, 0.8, 0.75, 0.6, 0.8, 0.9, 0.95),
    beat=True,
    beat_strength=1.0,
)


def _definition(name: str):
    return next(item for item in animation_catalog() if item.name == name)


def test_comet_and_pulse_publish_typed_settings_and_validate_values() -> None:
    comet = _definition("comet")
    pulse = _definition("pulse")

    assert {option.id for option in comet.settings} == {
        "speed",
        "tail_length",
        "intensity",
        "direction",
        "palette",
        "custom_color",
        "music_reactivity",
    }
    assert {option.id for option in pulse.settings} == {
        "speed",
        "minimum_intensity",
        "maximum_intensity",
        "palette",
        "custom_color",
        "pulse_curve",
        "music_reactivity",
    }
    assert comet.normalize_settings(None, allow_partial=True)["direction"] == "bounce"

    invalid_comet = comet.normalize_settings(None, allow_partial=True)
    invalid_comet["speed"] = 101
    with pytest.raises(ValueError, match="speed must be between"):
        comet.normalize_settings(invalid_comet)

    invalid_pulse = pulse.normalize_settings(None, allow_partial=True)
    invalid_pulse["minimum_intensity"] = 90
    invalid_pulse["maximum_intensity"] = 20
    with pytest.raises(ValueError, match="minimum intensity"):
        pulse.normalize_settings(invalid_pulse)


@pytest.mark.parametrize("name,animation_type", [("comet", Comet), ("pulse", Pulse)])
def test_player_live_settings_survive_reselection_and_are_isolated(
    name: str, animation_type: type[Comet | Pulse]
) -> None:
    left = AnimationPlayer.party()
    right = AnimationPlayer.party()
    index = left.index_of(name)
    assert index is not None
    definition = _definition(name)
    values = definition.normalize_settings(None, allow_partial=True)
    values["speed"] = 91

    live = left.animations[index].animation
    assert isinstance(live, animation_type)
    if isinstance(live, Comet):
        live.position = 7.0
    left.set_animation_settings(name, values)
    assert left.animations[index].animation is live
    assert left.animation_settings(name) == values
    assert right.animation_settings(name) != values

    left.set_index(index, restart=True)
    restarted = left.animations[index].animation
    assert isinstance(restarted, animation_type)
    assert restarted.settings.speed == 91
    assert right.animation_settings(name) != values


def test_comet_audio_can_be_disabled_and_reactivity_changes_motion() -> None:
    settings = _definition("comet").normalize_settings(None, allow_partial=True)
    settings["music_reactivity"] = 0
    standard = Comet()
    audio_disabled = Comet()
    standard.set_settings(settings)
    audio_disabled.set_settings(settings)
    first, second = Stripe(40), Stripe(40)
    standard.tick(20, first)
    audio_disabled.tick_audio(20, second, LOUD_AUDIO)
    np.testing.assert_array_equal(first.pixels(), second.pixels())

    settings["music_reactivity"] = 100
    reactive = Comet()
    reactive.set_settings(settings)
    moving_audio = AudioFrame(
        rms=0.9,
        bands=(0.95, 0.9, 0.8, 0.75, 0.6, 0.8, 0.9, 0.95),
        beat=False,
        beat_strength=0.0,
    )
    reactive.tick_audio(20, Stripe(40), moving_audio)
    assert reactive.position > standard.position


def test_pulse_audio_can_be_disabled_and_beats_boost_the_pulse() -> None:
    settings = _definition("pulse").normalize_settings(None, allow_partial=True)
    settings["music_reactivity"] = 0
    standard = Pulse()
    audio_disabled = Pulse()
    standard.set_settings(settings)
    audio_disabled.set_settings(settings)
    first, second = Stripe(16), Stripe(16)
    standard.tick(60, first)
    audio_disabled.tick_audio(60, second, LOUD_AUDIO)
    np.testing.assert_array_equal(first.pixels(), second.pixels())

    settings["music_reactivity"] = 100
    reactive = Pulse()
    reactive.set_settings(settings)
    reactive_stripe = Stripe(16)
    reactive.tick_audio(60, reactive_stripe, LOUD_AUDIO)
    assert reactive_stripe.pixels()[:, 3].mean() > first.pixels()[:, 3].mean()


def test_comet_tail_scales_with_strip_length() -> None:
    settings = _definition("comet").normalize_settings(None, allow_partial=True)
    settings["tail_length"] = 20
    short = Comet()
    long = Comet()
    short.set_settings(settings)
    long.set_settings(settings)
    short_stripe, long_stripe = Stripe(20), Stripe(100)
    short.tick(0, short_stripe)
    long.tick(0, long_stripe)

    assert np.count_nonzero(short_stripe.pixels()[:, 3]) < np.count_nonzero(
        long_stripe.pixels()[:, 3]
    )
