from __future__ import annotations

import json
from pathlib import Path

from lumistripe import ColorCorrection, PlaybackMode
from lumistripe_web.settings import (
    AudioTuningProfile,
    CalibrationSettingsStore,
    StartupPlaybackSettings,
    StripeOutputSettings,
    StripeTopologySettings,
)


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
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 4
    assert not list(path.parent.glob("*.tmp"))


def test_stripe_topology_round_trip_preserves_empty_and_mixed_outputs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    store = CalibrationSettingsStore(path)
    topology = StripeTopologySettings(
        layout="continuous",
        outputs=(
            StripeOutputSettings(id="left", name="Left", pixels=41, backend="spi"),
            StripeOutputSettings(
                id="right",
                name="Right",
                pixels=80,
                backend="gpio",
                data_pin=20,
                clock_pin=21,
                reversed=True,
            ),
        ),
    )

    store.save_stripes(topology)
    loaded, warning = store.load_all()
    assert warning is None
    assert loaded.stripe_topology == topology

    store.save_stripes(StripeTopologySettings(layout="independent"))
    loaded, _ = store.load_all()
    assert loaded.stripe_topology == StripeTopologySettings(layout="independent")


def test_invalid_settings_fall_back_to_neutral_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"version": 1, "color_correction": {"primary": {"red": 999}}}')

    profiles, warning = CalibrationSettingsStore(path).load()

    assert profiles["primary"] == ColorCorrection()
    assert warning is not None
    assert "invalid" in warning.lower()


def test_version_one_settings_migrate_when_audio_profile_is_saved(
    tmp_path: Path,
) -> None:
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


def test_invalid_audio_profile_does_not_discard_color_correction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        '{"version": 2, "color_correction": {"primary": {"red": 200, "green": 210, "blue": 220}}, "audio_profiles": {"USB Mic": {"target_level": 99}}}',
        encoding="utf-8",
    )

    settings, warning = CalibrationSettingsStore(path).load_all()

    assert settings.color_corrections["primary"] == ColorCorrection(200, 210, 220)
    assert settings.audio_profiles == {}
    assert warning is not None


def test_startup_settings_round_trip_and_legacy_versions_default_to_disabled(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    store = CalibrationSettingsStore(path)
    remembered = StartupPlaybackSettings(
        mode=PlaybackMode.SOLID,
        solid_color="#AABBCC",
        animation="rainbow_cycle",
        brightness=0.35,
        blackout=True,
    )

    store.save_startup(True, remembered)
    loaded, warning = store.load_all()

    assert warning is None
    assert loaded.restore_last_state is True
    assert loaded.remembered_playback == remembered

    path.write_text('{"version": 3, "color_correction": {}}', encoding="utf-8")
    legacy, warning = store.load_all()
    assert warning is None
    assert legacy.restore_last_state is False
