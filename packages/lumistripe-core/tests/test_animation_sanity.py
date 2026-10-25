from __future__ import annotations

import numpy as np
from lumistripe import (
    AudioFrame,
    Aurora,
    BouncingBall,
    ColorWipe,
    Comet,
    Confetti,
    DualComet,
    Fire,
    PeakMirror,
    Pulse,
    Rainbow,
    RainbowCycle,
    Shockwave,
    Stripe,
    TheaterChase,
    Twinkle,
    Wave,
)
from lumistripe.animation.catalog import party_catalog
from lumistripe.effects.base import Effect

QUIET_AUDIO = AudioFrame(
    rms=0.01,
    bands=(0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01),
    beat=False,
    beat_strength=0.0,
)


PUNCH_AUDIO = AudioFrame(
    rms=0.88,
    bands=(0.92, 0.82, 0.64, 0.52, 0.42, 0.74, 0.82, 0.9),
    beat=True,
    beat_strength=1.0,
)


CHANGED_ANIMATIONS = [
    RainbowCycle(),
    Pulse(),
    Confetti(),
    Comet(),
    ColorWipe(),
    TheaterChase(),
    Aurora(),
    Fire(),
    PeakMirror(),
    Wave(),
    Twinkle(),
    BouncingBall(),
    DualComet(),
    Rainbow(),
    Shockwave(),
]


def _assert_valid_pixels(stripe: Stripe) -> None:
    pixels = stripe.pixels()
    assert pixels.shape[1] == 4
    assert int(pixels[:, 3].min()) >= 0
    assert int(pixels[:, 3].max()) <= 255


def test_changed_animations_render_on_small_strips() -> None:
    for animation in CHANGED_ANIMATIONS:
        stripe = Stripe(1)
        animation.tick(3, stripe)
        _assert_valid_pixels(stripe)

        stripe = Stripe(3)
        animation.tick_audio(5, stripe, QUIET_AUDIO)
        _assert_valid_pixels(stripe)

        stripe = Stripe(8)
        animation.tick_audio(11, stripe, PUNCH_AUDIO)
        _assert_valid_pixels(stripe)


def test_bouncing_ball_moves_in_manual_mode() -> None:
    stripe = Stripe(8)
    animation = BouncingBall()

    animation.tick(0, stripe)
    first = np.copy(stripe.pixels()[:, 3])
    animation.tick(1, stripe)
    second = np.copy(stripe.pixels()[:, 3])

    assert not np.array_equal(first, second)


def test_every_catalog_definition_has_valid_settings_and_renders_at_scale() -> None:
    lengths = (1, 8, 60, 300, 600)
    definitions = party_catalog()

    assert len(definitions) == 54
    assert all(3 <= len(item.settings) <= 7 for item in definitions)
    for definition in definitions:
        values = definition.normalize_settings(None, allow_partial=True)
        assert set(values) == {option.id for option in definition.settings}
        animation = definition.create(values)
        for length in lengths:
            standard = Stripe(length)
            animation.tick(37, standard)
            assert np.isfinite(standard.pixels()).all()

            reactive = Stripe(length)
            animation.tick_audio(38, reactive, QUIET_AUDIO)
            animation.tick_audio(39, reactive, PUNCH_AUDIO)
            pixels = reactive.pixels()
            assert np.isfinite(pixels).all()
            assert pixels.shape == (length, 4)


def test_catalog_settings_survive_restart_and_remain_strip_local() -> None:
    from lumistripe import AnimationPlayer

    left = AnimationPlayer.party()
    right = AnimationPlayer.party()
    for definition in party_catalog():
        if not definition.settings:
            continue
        values = definition.normalize_settings(None, allow_partial=True)
        option = definition.settings[0]
        if option.type.value == "number":
            candidate = option.maximum if values[option.id] != option.maximum else option.minimum
            assert candidate is not None
            values[option.id] = candidate
        elif option.choices:
            values[option.id] = next(
                choice for choice in option.choices if choice != values[option.id]
            )

        index = left.index_of(definition.name)
        assert index is not None
        left.set_animation_settings(definition.name, values)
        left.set_index(index, restart=True)
        assert left.animation_settings(definition.name) == values
        fresh = left.fresh_animation(definition.name)
        assert fresh is not None
        assert fresh.setting(option.id, values[option.id]) == values[option.id]
        assert right.animation_settings(definition.name) != values
        assert left.animations[index].animation is not right.animations[index].animation


def test_effect_configuration_preserves_transparent_overlay_pixels() -> None:
    from lumistripe.color import Rgba

    definition = next(item for item in party_catalog() if item.name == "beat_wave")
    values = definition.normalize_settings(None, allow_partial=True)
    values.update({"palette": "custom", "custom_color": "#12ABEF", "intensity": 60})
    animation = definition.create(values)
    assert isinstance(animation, Effect)

    stripe = Stripe(60)
    stripe.fill(Rgba(0, 0, 0, 0.0))
    output = animation.output_controller(stripe, 1.0)
    animation.tick_audio(0, output, PUNCH_AUDIO)

    pixels = stripe.pixels()
    assert np.isfinite(pixels).all()
    assert np.any(pixels[:, 3] < 255)
    assert np.any(pixels[:, 3] > 0)


def test_universal_intensity_setting_scales_and_comet_is_not_double_scaled() -> None:
    rainbow_definition = next(item for item in party_catalog() if item.name == "rainbow")
    defaults = rainbow_definition.normalize_settings(None, allow_partial=True)
    unmodified = rainbow_definition.create(defaults)
    baseline = Stripe(16)
    unmodified.tick(8, unmodified.output_controller(baseline, 1.0))

    dimmed_values = dict(defaults)
    dimmed_values["intensity"] = 0
    dimmed = rainbow_definition.create(dimmed_values)
    dimmed_stripe = Stripe(16)
    dimmed.tick(8, dimmed.output_controller(dimmed_stripe, 1.0))
    assert np.any(baseline.pixels()[:, 3] > 0)
    assert not np.any(dimmed_stripe.pixels()[:, 3] > 0)

    comet_definition = next(item for item in party_catalog() if item.name == "comet")
    comet_values = comet_definition.normalize_settings(None, allow_partial=True)
    comet_values["intensity"] = 45
    configured = comet_definition.create(comet_values)
    direct = Stripe(30)
    configured.tick(20, direct)
    through_player = comet_definition.create(comet_values)
    via_controller = Stripe(30)
    through_player.tick(20, through_player.output_controller(via_controller, 1.0))
    np.testing.assert_array_equal(direct.pixels(), via_controller.pixels())
