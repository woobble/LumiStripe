from __future__ import annotations

import random

import numpy as np
import pytest
from lumistripe import AudioFrame, Stripe
from lumistripe.animation.catalog import AnimationDefinition, party_catalog
from lumistripe.animation.settings import (
    AnimationSettingDefinition,
    AnimationSettingType,
)

QUIET_AUDIO = AudioFrame(
    rms=0.015,
    bands=(0.01, 0.012, 0.014, 0.013, 0.011, 0.012, 0.01, 0.009),
)
LOUD_BEAT = AudioFrame(
    rms=0.92,
    bands=(0.95, 0.88, 0.76, 0.7, 0.58, 0.72, 0.86, 0.94),
    beat=True,
    beat_strength=0.98,
)


def _alternate(option: AnimationSettingDefinition):
    if option.type is AnimationSettingType.NUMBER:
        assert option.minimum is not None and option.maximum is not None
        return option.maximum if option.default != option.maximum else option.minimum
    if option.type is AnimationSettingType.BOOLEAN:
        return not option.default
    if option.type is AnimationSettingType.ENUM:
        return next(choice for choice in option.choices if choice != option.default)
    if option.type is AnimationSettingType.COLOR:
        return "#12ABEF" if option.default != "#12ABEF" else "#EF1245"
    raise AssertionError(f"unsupported option type: {option.type}")


def _render_frames(
    definition: AnimationDefinition,
    values: dict[str, object],
    *,
    audio_reactive: bool,
) -> np.ndarray:
    random.seed(0x4C53)
    np.random.seed(0x4C53)
    animation = definition.create(values)
    stripe = Stripe(60)
    frames: list[np.ndarray] = []
    for frame in range(96):
        output = animation.output_controller(stripe, 1.0)
        configured_frame = animation.effective_frame(frame)
        if audio_reactive:
            audio = LOUD_BEAT if frame % 8 == 0 else QUIET_AUDIO
            configured_audio = animation.effective_audio(audio)
            if configured_audio is None:
                animation.tick(configured_frame, output)
            else:
                animation.tick_audio(configured_frame, output, configured_audio)
        else:
            animation.tick(configured_frame, output)
        frames.append(stripe.pixels().copy())
    return np.stack(frames)


@pytest.mark.parametrize(
    "definition",
    party_catalog(),
    ids=lambda definition: definition.name,
)
def test_every_catalog_option_is_valid_at_its_boundaries_and_changes_output(
    definition: AnimationDefinition,
) -> None:
    defaults = definition.normalize_settings(None, allow_partial=True)
    for option in definition.settings:
        # Check schema defaults and both numeric endpoints before exercising the
        # renderer, so a dead control cannot pass only because it validates.
        assert defaults[option.id] == option.default
        if option.type is AnimationSettingType.NUMBER:
            assert option.minimum is not None and option.maximum is not None
            for endpoint in (option.minimum, option.maximum):
                endpoint_values = dict(defaults)
                endpoint_values[option.id] = endpoint
                if option.id == "minimum_intensity" and endpoint > defaults["maximum_intensity"]:
                    endpoint_values["maximum_intensity"] = endpoint
                elif option.id == "maximum_intensity" and endpoint < defaults["minimum_intensity"]:
                    endpoint_values["minimum_intensity"] = endpoint
                definition.normalize_settings(endpoint_values)

        baseline = dict(defaults)
        changed = dict(defaults)
        if option.visible_when_option is not None:
            baseline[option.visible_when_option] = option.visible_when_value
            changed[option.visible_when_option] = option.visible_when_value
        changed[option.id] = _alternate(option)
        if option.id == "maximum_intensity" and changed[option.id] < baseline["minimum_intensity"]:
            baseline["minimum_intensity"] = changed[option.id]
            changed["minimum_intensity"] = changed[option.id]
        elif option.id == "minimum_intensity" and changed[option.id] > baseline["maximum_intensity"]:
            baseline["maximum_intensity"] = changed[option.id]
            changed["maximum_intensity"] = changed[option.id]

        # A custom palette is required before custom-color values can affect
        # rendered pixels. Keep both compared configurations in that mode.
        if option.id == "custom_color":
            baseline["palette"] = "custom"
            changed["palette"] = "custom"
        elif option.id == "palette" and changed[option.id] == "custom":
            baseline["custom_color"] = "#7C3AED"
            changed["custom_color"] = "#7C3AED"

        baseline = definition.normalize_settings(baseline)
        changed = definition.normalize_settings(changed)
        baseline_static = _render_frames(definition, baseline, audio_reactive=False)
        changed_static = _render_frames(definition, changed, audio_reactive=False)
        if np.array_equal(baseline_static, changed_static):
            baseline_audio = _render_frames(definition, baseline, audio_reactive=True)
            changed_audio = _render_frames(definition, changed, audio_reactive=True)
            assert not np.array_equal(baseline_audio, changed_audio), (
                f"{definition.name}.{option.id} accepted an alternate value but "
                "changed neither deterministic static nor audio output"
            )
