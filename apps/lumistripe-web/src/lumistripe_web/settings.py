from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from lumistripe import ColorCorrection

SETTINGS_VERSION = 1
PROFILE_NAMES = ("primary", "secondary")


def default_settings_path() -> Path:
    return Path.home() / ".config" / "lumistripe" / "settings.json"


class CalibrationSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def load(self) -> tuple[dict[str, ColorCorrection], str | None]:
        profiles = {name: ColorCorrection() for name in PROFILE_NAMES}
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return profiles, None
        except OSError as exc:
            return profiles, f"Could not read color calibration settings: {exc}"

        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("version") != SETTINGS_VERSION:
                raise ValueError(f"expected settings version {SETTINGS_VERSION}")
            encoded_profiles = payload.get("color_correction")
            if not isinstance(encoded_profiles, dict):
                raise TypeError("color_correction must be an object")
            for name in PROFILE_NAMES:
                encoded = encoded_profiles.get(name)
                if encoded is None:
                    continue
                if not isinstance(encoded, dict):
                    raise TypeError(f"{name} profile must be an object")
                profiles[name] = ColorCorrection(
                    red=_channel(encoded, "red", name),
                    green=_channel(encoded, "green", name),
                    blue=_channel(encoded, "blue", name),
                )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return (
                {name: ColorCorrection() for name in PROFILE_NAMES},
                f"Color calibration settings are invalid: {exc}",
            )
        return profiles, None

    def save(self, profiles: dict[str, ColorCorrection]) -> None:
        payload = {
            "version": SETTINGS_VERSION,
            "color_correction": {
                name: {
                    "red": profiles.get(name, ColorCorrection()).red,
                    "green": profiles.get(name, ColorCorrection()).green,
                    "blue": profiles.get(name, ColorCorrection()).blue,
                }
                for name in PROFILE_NAMES
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.chmod(0o600)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _channel(encoded: dict[object, object], channel: str, profile: str) -> int:
    value = encoded.get(channel)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{profile}.{channel} must be an integer")
    if not 0 <= value <= 255:
        raise ValueError(f"{profile}.{channel} must be between 0 and 255")
    return value
