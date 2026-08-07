from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from lumistripe import (
    AudioConfig,
    AudioNormalization,
    AudioSmoothing,
    ColorCorrection,
    MusicActivityConfig,
)

SETTINGS_VERSION = 2
PROFILE_NAMES = ("primary", "secondary")


def default_settings_path() -> Path:
    return Path.home() / ".config" / "lumistripe" / "settings.json"


@dataclass(frozen=True, slots=True)
class AudioTuningProfile:
    target_level: float = AudioNormalization().target_level
    dynamic_response: float = 0.65
    rms_attack: float = AudioSmoothing().rms_attack
    rms_release: float = AudioSmoothing().rms_release
    band_attack: float = AudioSmoothing().band_attack
    band_release: float = AudioSmoothing().band_release
    beat_release: float = AudioSmoothing().beat_release
    energy_threshold: float = MusicActivityConfig().energy_threshold
    onset_threshold: float = MusicActivityConfig().onset_threshold
    beat_density_threshold: float = MusicActivityConfig().beat_density_threshold
    brightness_threshold: float = MusicActivityConfig().brightness_threshold
    spectral_balance_ratio: float = MusicActivityConfig().spectral_balance_ratio

    def __post_init__(self) -> None:
        if not 0.1 <= self.target_level <= 0.8:
            raise ValueError("target_level must be between 0.1 and 0.8")
        if not 0.0 <= self.dynamic_response <= 1.0:
            raise ValueError("dynamic_response must be between 0 and 1")
        for name in (
            "rms_attack",
            "rms_release",
            "band_attack",
            "band_release",
            "beat_release",
        ):
            if not 0.01 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between 0.01 and 1.0")
        for name in (
            "energy_threshold",
            "onset_threshold",
            "beat_density_threshold",
            "brightness_threshold",
            "spectral_balance_ratio",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    def audio_config(self) -> AudioConfig:
        defaults = AudioConfig()
        return replace(
            defaults,
            smoothing=replace(
                defaults.smoothing,
                rms_attack=self.rms_attack,
                rms_release=self.rms_release,
                band_attack=self.band_attack,
                band_release=self.band_release,
                beat_release=self.beat_release,
            ),
            normalization=replace(
                defaults.normalization,
                target_level=self.target_level,
            ),
        )

    def activity_config(self) -> MusicActivityConfig:
        return replace(
            MusicActivityConfig(),
            energy_threshold=self.energy_threshold,
            onset_threshold=self.onset_threshold,
            beat_density_threshold=self.beat_density_threshold,
            brightness_threshold=self.brightness_threshold,
            spectral_balance_ratio=self.spectral_balance_ratio,
        )


@dataclass(slots=True)
class DashboardSettings:
    color_corrections: dict[str, ColorCorrection] = field(
        default_factory=lambda: {
            name: ColorCorrection() for name in PROFILE_NAMES
        }
    )
    selected_audio_device: str | None = None
    audio_profiles: dict[str, AudioTuningProfile] = field(default_factory=dict)


class CalibrationSettingsStore:
    """Versioned dashboard settings store kept under the legacy public name."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def load(self) -> tuple[dict[str, ColorCorrection], str | None]:
        settings, warning = self.load_all()
        return settings.color_corrections, warning

    def load_all(self) -> tuple[DashboardSettings, str | None]:
        settings = DashboardSettings()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return settings, None
        except OSError as exc:
            return settings, f"Could not read dashboard settings: {exc}"

        warnings: list[str] = []
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError("settings must be an object")
            version = payload.get("version")
            if version not in {1, SETTINGS_VERSION}:
                raise ValueError(f"expected settings version 1 or {SETTINGS_VERSION}")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return settings, f"Dashboard settings are invalid: {exc}"

        encoded_colors = payload.get("color_correction", {})
        if not isinstance(encoded_colors, dict):
            warnings.append("color_correction must be an object")
        else:
            for name in PROFILE_NAMES:
                encoded = encoded_colors.get(name)
                if encoded is None:
                    continue
                try:
                    if not isinstance(encoded, dict):
                        raise TypeError(f"{name} profile must be an object")
                    settings.color_corrections[name] = ColorCorrection(
                        red=_channel(encoded, "red", name),
                        green=_channel(encoded, "green", name),
                        blue=_channel(encoded, "blue", name),
                    )
                except (TypeError, ValueError) as exc:
                    warnings.append(str(exc))

        if payload.get("version") == SETTINGS_VERSION:
            selected = payload.get("selected_audio_device")
            if selected is not None and not isinstance(selected, str):
                warnings.append("selected_audio_device must be a string or null")
            elif selected:
                settings.selected_audio_device = selected

            encoded_profiles = payload.get("audio_profiles", {})
            if not isinstance(encoded_profiles, dict):
                warnings.append("audio_profiles must be an object")
            else:
                for device_name, encoded in encoded_profiles.items():
                    try:
                        if not isinstance(device_name, str) or not device_name:
                            raise TypeError("audio profile names must be non-empty strings")
                        if not isinstance(encoded, dict):
                            raise TypeError(f"audio profile {device_name!r} must be an object")
                        defaults = AudioTuningProfile()
                        settings.audio_profiles[device_name] = AudioTuningProfile(
                            **{
                                field_name: (
                                    _float_field(encoded, field_name, device_name)
                                    if field_name in encoded
                                    else getattr(defaults, field_name)
                                )
                                for field_name in AudioTuningProfile.__dataclass_fields__
                            }
                        )
                    except (TypeError, ValueError) as exc:
                        warnings.append(str(exc))

        warning = None
        if warnings:
            warning = "Dashboard settings contain invalid values: " + "; ".join(warnings)
        return settings, warning

    def save(self, profiles: dict[str, ColorCorrection]) -> None:
        settings, _ = self.load_all()
        settings.color_corrections = {
            name: profiles.get(name, ColorCorrection()) for name in PROFILE_NAMES
        }
        self.save_all(settings)

    def save_audio(
        self,
        selected_device: str,
        profiles: dict[str, AudioTuningProfile],
    ) -> None:
        settings, _ = self.load_all()
        settings.selected_audio_device = selected_device
        settings.audio_profiles = dict(profiles)
        self.save_all(settings)

    def save_all(self, settings: DashboardSettings) -> None:
        payload = {
            "version": SETTINGS_VERSION,
            "color_correction": {
                name: {
                    "red": settings.color_corrections.get(name, ColorCorrection()).red,
                    "green": settings.color_corrections.get(name, ColorCorrection()).green,
                    "blue": settings.color_corrections.get(name, ColorCorrection()).blue,
                }
                for name in PROFILE_NAMES
            },
            "selected_audio_device": settings.selected_audio_device,
            "audio_profiles": {
                device_name: {
                    field_name: getattr(profile, field_name)
                    for field_name in AudioTuningProfile.__dataclass_fields__
                }
                for device_name, profile in settings.audio_profiles.items()
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


def _float_field(encoded: dict[object, object], name: str, profile: str) -> float:
    value = encoded.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{profile}.{name} must be a number")
    return float(value)
