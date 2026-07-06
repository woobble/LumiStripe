import json

import pytest

from lumistripe import (
    PCM2902_PROFILE,
    AudioAnalysis,
    load_mic_profile,
    match_builtin_mic_profile,
    mic_profile_from_dict,
    write_mic_profile,
)


def test_builtin_pcm2902_profile_matches_device_name() -> None:
    profile = match_builtin_mic_profile("Texas Instruments PCM2902 Audio Codec")

    assert profile is PCM2902_PROFILE
    assert profile.mic_noise_floor == pytest.approx(0.0137)
    assert profile.mic_target_level == pytest.approx(0.403)
    assert profile.idle_threshold_scale == pytest.approx(0.92)


def test_load_mic_profile_auto_requires_matching_device() -> None:
    assert load_mic_profile("auto", device_name="USB PCM2902 Input") is PCM2902_PROFILE

    with pytest.raises(ValueError, match="no built-in mic profile"):
        load_mic_profile("auto", device_name="Unknown Microphone")


def test_mic_profile_loads_custom_json_and_builds_audio_config(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "name": "test",
                "device_patterns": ["Test Mic"],
                "mic_noise_floor": 0.02,
                "mic_target_level": 0.4,
                "idle_threshold_scale": 1.5,
                "analysis": {
                    "drop_bass_threshold": 0.6,
                    "section_change_threshold": 0.2,
                },
            }
        ),
        encoding="utf-8",
    )

    profile = load_mic_profile(path)
    config = profile.audio_config()

    assert profile.name == "test"
    assert profile.device_patterns == ("Test Mic",)
    assert config.smoothing.noise_floor == pytest.approx(0.02)
    assert config.normalization.target_level == pytest.approx(0.4)
    assert config.analysis.drop_bass_threshold == pytest.approx(0.6)
    assert config.analysis.section_change_threshold == pytest.approx(0.2)
    assert config.analysis.drop_bass_delta_threshold == pytest.approx(AudioAnalysis().drop_bass_delta_threshold)


def test_write_mic_profile_round_trips(tmp_path) -> None:
    path = tmp_path / "profile.json"
    profile = mic_profile_from_dict({"name": "saved", "mic_noise_floor": 0.01})

    write_mic_profile(path, profile)

    loaded = load_mic_profile(path)
    assert loaded.name == "saved"
    assert loaded.mic_noise_floor == pytest.approx(0.01)
