from types import SimpleNamespace

import pytest
from lumistripe import AudioSource, CycleOrder, CycleTiming
from lumistripe_app_support import (
    ActivityPolicyOptions,
    build_activity_config,
    build_audio_config,
    build_cycling_config,
    build_dynamic_selector_config,
    parse_audio_source,
)


def test_build_audio_config_overrides_only_requested_levels() -> None:
    config = build_audio_config(target_level=0.5, noise_floor=0.02)

    assert config.normalization.target_level == 0.5
    assert config.smoothing.noise_floor == 0.02


def test_build_policy_helpers_are_argument_parser_independent() -> None:
    options = SimpleNamespace(
        cycle_order=CycleOrder.SEQUENTIAL,
        cycle_timing=CycleTiming.FIXED,
        cycle_interval=4.0,
        dynamic_seed=7,
        dynamic_min_duration=3.0,
        dynamic_max_duration=9.0,
        dynamic_switch_cooldown=2.0,
        dynamic_drop_cooldown=1.0,
        dynamic_randomness=0.4,
        dynamic_history_size=5,
    )

    cycling = build_cycling_config(options)
    selector = build_dynamic_selector_config(options)
    activity = build_activity_config(
        ActivityPolicyOptions(
            idle_enter_frames=8,
            idle_threshold_scale=1.2,
            activation_delay_s=0.25,
        )
    )

    assert cycling.interval_s == 4.0
    assert selector.seed == 7
    assert activity.idle_enter_frames == 8


def test_parse_audio_source_rejects_bluetooth_for_local_apps() -> None:
    assert parse_audio_source("demo") is AudioSource.DEMO
    with pytest.raises(ValueError, match="Bluetooth"):
        parse_audio_source("bluetooth")


def test_parse_audio_source_rejects_spotify_for_local_apps() -> None:
    with pytest.raises(ValueError, match="Spotify"):
        parse_audio_source("spotify")
