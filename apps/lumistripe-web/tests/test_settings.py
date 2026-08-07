from __future__ import annotations

import json
from pathlib import Path

from lumistripe import ColorCorrection
from lumistripe_web.settings import AudioTuningProfile, CalibrationSettingsStore


def test_missing_settings_use_neutral_profiles(tmp_path: Path) -> None:
    profiles, warning = CalibrationSettingsStore(tmp_path / "settings.json").load()

    assert warning is None
    assert profiles == {
        "primary": ColorCorrection(),
        "secondary": ColorCorrection(),
    }


def test_settings_round_trip_both_profiles_atomically(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.json"
    store = CalibrationSettingsStore(path)

    store.save(
        {
            "primary": ColorCorrection(255, 220, 180),
            "secondary": ColorCorrection(200, 210, 220),
        }
    )
    profiles, warning = store.load()

    assert warning is None
    assert profiles["primary"] == ColorCorrection(255, 220, 180)
    assert profiles["secondary"] == ColorCorrection(200, 210, 220)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2
    assert not list(path.parent.glob("*.tmp"))


def test_invalid_settings_fall_back_to_neutral_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"version": 1, "color_correction": {"primary": {"red": 999}}}')

    profiles, warning = CalibrationSettingsStore(path).load()

    assert profiles["primary"] == ColorCorrection()
    assert warning is not None
    assert "invalid" in warning.lower()


def test_version_one_settings_migrate_when_audio_profile_is_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        '{"version": 1, "color_correction": {"primary": {"red": 200, "green": 210, "blue": 220}}}',
        encoding="utf-8",
    )
    store = CalibrationSettingsStore(path)

    settings, warning = store.load_all()
    assert warning is None
    assert settings.color_corrections["primary"] == ColorCorrection(200, 210, 220)

    profile = AudioTuningProfile(target_level=0.5, dynamic_response=0.8)
    store.save_audio("USB Mic", {"USB Mic": profile})
    migrated, warning = store.load_all()

    assert warning is None
    assert migrated.selected_audio_device == "USB Mic"
    assert migrated.audio_profiles["USB Mic"] == profile
    assert migrated.color_corrections["primary"] == ColorCorrection(200, 210, 220)


def test_legacy_audio_profile_defaults_missing_dynamic_response(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    profile = AudioTuningProfile()
    encoded = {
        name: getattr(profile, name)
        for name in AudioTuningProfile.__dataclass_fields__
        if name != "dynamic_response"
    }
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "color_correction": {},
                "audio_profiles": {"USB Mic": encoded},
            }
        ),
        encoding="utf-8",
    )

    settings, warning = CalibrationSettingsStore(path).load_all()

    assert warning is None
    assert settings.audio_profiles["USB Mic"].dynamic_response == 0.65


def test_invalid_audio_profile_does_not_discard_color_correction(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        '{"version": 2, "color_correction": {"primary": {"red": 200, "green": 210, "blue": 220}}, "audio_profiles": {"USB Mic": {"target_level": 99}}}',
        encoding="utf-8",
    )

    settings, warning = CalibrationSettingsStore(path).load_all()

    assert settings.color_corrections["primary"] == ColorCorrection(200, 210, 220)
    assert settings.audio_profiles == {}
    assert warning is not None
