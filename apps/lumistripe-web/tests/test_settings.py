from __future__ import annotations

import json
from pathlib import Path

from lumistripe import ColorCorrection
from lumistripe_web.settings import CalibrationSettingsStore


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
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    assert not list(path.parent.glob("*.tmp"))


def test_invalid_settings_fall_back_to_neutral_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"version": 1, "color_correction": {"primary": {"red": 999}}}')

    profiles, warning = CalibrationSettingsStore(path).load()

    assert profiles["primary"] == ColorCorrection()
    assert warning is not None
    assert "invalid" in warning.lower()
