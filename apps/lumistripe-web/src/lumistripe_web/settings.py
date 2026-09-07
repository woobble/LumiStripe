from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

from lumistripe import (
    AudioConfig,
    AudioNormalization,
    AudioSmoothing,
    ColorCorrection,
    MusicActivityConfig,
    PlaybackMode,
)

SETTINGS_VERSION = 4
PROFILE_NAMES = ("primary", "secondary")
StripeLayout = Literal["mirrored", "continuous", "independent"]
StripeBackend = Literal["spi", "gpio"]


@dataclass(frozen=True, slots=True)
class StartupPlaybackSettings:
    mode: PlaybackMode = PlaybackMode.STATIC
    solid_color: str = "#7C3AED"
    animation: str = ""
    brightness: float = 1.0
    blackout: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PlaybackMode):
            raise TypeError("startup mode is invalid")
        if not isinstance(self.animation, str):
            raise TypeError("startup animation must be a string")
        if not isinstance(self.blackout, bool):
            raise TypeError("startup blackout must be a boolean")
        if not isinstance(self.solid_color, str):
            raise TypeError("startup solid_color must be a string")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.solid_color):
            raise ValueError("startup solid_color must be a six-digit hex color")
        if isinstance(self.brightness, bool) or not 0.0 <= self.brightness <= 1.0:
            raise ValueError("startup brightness must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class StripeOutputSettings:
    id: str
    name: str
    pixels: int
    backend: StripeBackend = "spi"
    reversed: bool = False
    spi_device: str = "/dev/spidev0.0"
    spi_speed_hz: int = 1_000_000
    chip: str = "/dev/gpiochip0"
    data_pin: int = 10
    clock_pin: int = 11

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("stripe id and name must not be empty")
        if not 1 <= self.pixels <= 4096:
            raise ValueError("stripe pixels must be between 1 and 4096")
        if self.backend not in {"spi", "gpio"}:
            raise ValueError("stripe backend must be spi or gpio")
        if self.spi_speed_hz <= 0:
            raise ValueError("SPI speed must be greater than zero")
        if self.data_pin < 0 or self.clock_pin < 0:
            raise ValueError("GPIO pins must not be negative")
        if self.data_pin == self.clock_pin:
            raise ValueError("GPIO data and clock pins must differ")


@dataclass(frozen=True, slots=True)
class StripeTopologySettings:
    layout: StripeLayout = "mirrored"
    outputs: tuple[StripeOutputSettings, ...] = ()

    def __post_init__(self) -> None:
        if self.layout not in {"mirrored", "continuous", "independent"}:
            raise ValueError("invalid stripe layout")
        if len(self.outputs) > 2:
            raise ValueError("at most two stripes can be configured")
        ids = [output.id for output in self.outputs]
        if len(ids) != len(set(ids)):
            raise ValueError("stripe ids must be unique")
        spi_devices = [o.spi_device for o in self.outputs if o.backend == "spi"]
        if len(spi_devices) != len(set(spi_devices)):
            raise ValueError("SPI devices must be unique")
        pins = [
            (o.chip, pin)
            for o in self.outputs
            if o.backend == "gpio"
            for pin in (o.data_pin, o.clock_pin)
        ]
        if len(pins) != len(set(pins)):
            raise ValueError("GPIO pins must be unique")


def default_settings_path() -> Path:
    return Path.home() / ".config" / "lumistripe" / "settings.json"


@dataclass(frozen=True, slots=True)
class AudioTuningProfile:
    target_level: float = AudioNormalization().target_level
    hardware_gain_target: float | None = None
    noise_floor: float = AudioSmoothing().noise_floor
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
        if self.hardware_gain_target is not None and not 0.0 <= self.hardware_gain_target <= 1.0:
            raise ValueError("hardware_gain_target must be between 0 and 1")
        if not 0.0 <= self.dynamic_response <= 1.0:
            raise ValueError("dynamic_response must be between 0 and 1")
        if not 0.0 <= self.noise_floor <= 1.0:
            raise ValueError("noise_floor must be between 0 and 1")
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
                noise_floor=self.noise_floor,
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
        default_factory=lambda: {name: ColorCorrection() for name in PROFILE_NAMES}
    )
    selected_audio_device: str | None = None
    audio_profiles: dict[str, AudioTuningProfile] = field(default_factory=dict)
    stripe_topology: StripeTopologySettings | None = None
    restore_last_state: bool = False
    remembered_playback: StartupPlaybackSettings = field(
        default_factory=StartupPlaybackSettings
    )


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
            if version not in {1, 2, 3, SETTINGS_VERSION}:
                raise ValueError(
                    f"expected settings version 1, 2, 3, or {SETTINGS_VERSION}"
                )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return settings, f"Dashboard settings are invalid: {exc}"

        encoded_colors = payload.get("color_correction", {})
        if not isinstance(encoded_colors, dict):
            warnings.append("color_correction must be an object")
        else:
            for name, encoded in encoded_colors.items():
                try:
                    if not isinstance(name, str) or not name:
                        raise TypeError(
                            "color correction names must be non-empty strings"
                        )
                    if not isinstance(encoded, dict):
                        raise TypeError(f"{name} profile must be an object")
                    settings.color_corrections[name] = ColorCorrection(
                        red=_channel(encoded, "red", name),
                        green=_channel(encoded, "green", name),
                        blue=_channel(encoded, "blue", name),
                    )
                except (TypeError, ValueError) as exc:
                    warnings.append(str(exc))

        if payload.get("version") in {2, 3, SETTINGS_VERSION}:
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
                            raise TypeError(
                                "audio profile names must be non-empty strings"
                            )
                        if not isinstance(encoded, dict):
                            raise TypeError(
                                f"audio profile {device_name!r} must be an object"
                            )
                        defaults = AudioTuningProfile()
                        profile_values = {
                            field_name: (
                                _optional_float_field(encoded, field_name, device_name)
                                if field_name == "hardware_gain_target"
                                else _float_field(encoded, field_name, device_name)
                                if field_name in encoded
                                else getattr(defaults, field_name)
                            )
                            for field_name in AudioTuningProfile.__dataclass_fields__
                        }
                        settings.audio_profiles[device_name] = AudioTuningProfile(
                            **cast(dict[str, Any], profile_values)
                        )
                    except (TypeError, ValueError) as exc:
                        warnings.append(str(exc))

        if payload.get("version") in {3, SETTINGS_VERSION} and "stripes" in payload:
            try:
                settings.stripe_topology = _decode_stripes(payload["stripes"])
            except (TypeError, ValueError) as exc:
                warnings.append(str(exc))

        if payload.get("version") == SETTINGS_VERSION:
            restore = payload.get("restore_last_state", False)
            if not isinstance(restore, bool):
                warnings.append("restore_last_state must be a boolean")
            else:
                settings.restore_last_state = restore
            remembered = payload.get("remembered_playback", {})
            if not isinstance(remembered, dict):
                warnings.append("remembered_playback must be an object")
            else:
                try:
                    remembered_settings = StartupPlaybackSettings(
                        mode=PlaybackMode(remembered.get("mode", "static")),
                        solid_color=remembered.get("solid_color", "#7C3AED"),
                        animation=remembered.get("animation", ""),
                        brightness=float(remembered.get("brightness", 1.0)),
                        blackout=remembered.get("blackout", False),
                    )
                    settings.remembered_playback = remembered_settings
                except (TypeError, ValueError) as exc:
                    warnings.append(str(exc))

        warning = None
        if warnings:
            warning = "Dashboard settings contain invalid values: " + "; ".join(
                warnings
            )
        return settings, warning

    def save(self, profiles: dict[str, ColorCorrection]) -> None:
        settings, _ = self.load_all()
        settings.color_corrections = {
            name: correction for name, correction in profiles.items()
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

    def save_stripes(self, topology: StripeTopologySettings) -> None:
        settings, _ = self.load_all()
        settings.stripe_topology = topology
        self.save_all(settings)

    def save_startup(
        self, restore_last_state: bool, remembered: StartupPlaybackSettings
    ) -> None:
        settings, _ = self.load_all()
        settings.restore_last_state = restore_last_state
        settings.remembered_playback = remembered
        self.save_all(settings)

    def save_all(self, settings: DashboardSettings) -> None:
        payload = {
            "version": SETTINGS_VERSION,
            "color_correction": {
                name: {
                    "red": settings.color_corrections.get(name, ColorCorrection()).red,
                    "green": settings.color_corrections.get(
                        name, ColorCorrection()
                    ).green,
                    "blue": settings.color_corrections.get(
                        name, ColorCorrection()
                    ).blue,
                }
                for name in settings.color_corrections
            },
            "selected_audio_device": settings.selected_audio_device,
            "audio_profiles": {
                device_name: {
                    field_name: getattr(profile, field_name)
                    for field_name in AudioTuningProfile.__dataclass_fields__
                }
                for device_name, profile in settings.audio_profiles.items()
            },
            "stripes": (
                None
                if settings.stripe_topology is None
                else {
                    "layout": settings.stripe_topology.layout,
                    "outputs": [
                        {
                            field_name: getattr(output, field_name)
                            for field_name in StripeOutputSettings.__dataclass_fields__
                        }
                        for output in settings.stripe_topology.outputs
                    ],
                }
            ),
            "restore_last_state": settings.restore_last_state,
            "remembered_playback": {
                "mode": settings.remembered_playback.mode.value,
                "solid_color": settings.remembered_playback.solid_color,
                "animation": settings.remembered_playback.animation,
                "brightness": settings.remembered_playback.brightness,
                "blackout": settings.remembered_playback.blackout,
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


def _decode_stripes(encoded: object) -> StripeTopologySettings | None:
    if encoded is None:
        return None
    if not isinstance(encoded, dict):
        raise TypeError("stripes must be an object or null")
    layout = encoded.get("layout", "mirrored")
    outputs = encoded.get("outputs", [])
    if not isinstance(layout, str) or not isinstance(outputs, list):
        raise TypeError("stripe layout must be a string and outputs must be a list")
    decoded: list[StripeOutputSettings] = []
    defaults = StripeOutputSettings(id="default", name="Stripe", pixels=80)
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            raise TypeError(f"stripe {index + 1} must be an object")
        values = {
            field_name: output.get(field_name, getattr(defaults, field_name))
            for field_name in StripeOutputSettings.__dataclass_fields__
        }
        try:
            decoded.append(StripeOutputSettings(**values))
        except TypeError as exc:
            raise TypeError(f"invalid stripe {index + 1}: {exc}") from exc
    return StripeTopologySettings(layout=layout, outputs=tuple(decoded))  # type: ignore[arg-type]


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


def _optional_float_field(encoded: dict[object, object], name: str, profile: str) -> float | None:
    value = encoded.get(name)
    if value is None:
        return None
    return _float_field(encoded, name, profile)
