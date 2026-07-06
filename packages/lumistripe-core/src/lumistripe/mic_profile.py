from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .audio import AudioAnalysis, AudioConfig, AudioNormalization, AudioSmoothing


@dataclass(frozen=True, slots=True)
class MicProfile:
    name: str
    device_patterns: tuple[str, ...] = ()
    mic_noise_floor: float | None = None
    mic_target_level: float | None = None
    idle_threshold_scale: float | None = None
    analysis: AudioAnalysis = field(default_factory=AudioAnalysis)

    def audio_config(
        self,
        *,
        target_level: float | None = None,
        noise_floor: float | None = None,
    ) -> AudioConfig:
        return AudioConfig(
            smoothing=AudioSmoothing(
                noise_floor=noise_floor
                if noise_floor is not None
                else (self.mic_noise_floor if self.mic_noise_floor is not None else AudioSmoothing().noise_floor)
            ),
            normalization=AudioNormalization(
                target_level=target_level
                if target_level is not None
                else (self.mic_target_level if self.mic_target_level is not None else AudioNormalization().target_level)
            ),
            analysis=self.analysis,
        )

    def with_tuning(
        self,
        *,
        mic_noise_floor: float | None = None,
        mic_target_level: float | None = None,
        idle_threshold_scale: float | None = None,
        analysis: AudioAnalysis | None = None,
    ) -> MicProfile:
        return replace(
            self,
            mic_noise_floor=mic_noise_floor if mic_noise_floor is not None else self.mic_noise_floor,
            mic_target_level=mic_target_level if mic_target_level is not None else self.mic_target_level,
            idle_threshold_scale=idle_threshold_scale if idle_threshold_scale is not None else self.idle_threshold_scale,
            analysis=analysis or self.analysis,
        )

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "name": self.name,
            "device_patterns": list(self.device_patterns),
            "analysis": asdict(self.analysis),
        }
        if self.mic_noise_floor is not None:
            data["mic_noise_floor"] = self.mic_noise_floor
        if self.mic_target_level is not None:
            data["mic_target_level"] = self.mic_target_level
        if self.idle_threshold_scale is not None:
            data["idle_threshold_scale"] = self.idle_threshold_scale
        return data


PCM2902_PROFILE = MicProfile(
    name="pcm2902",
    device_patterns=(
        "Texas Instruments PCM2902 Audio Codec",
        "PCM2902",
    ),
    mic_noise_floor=0.0137,
    mic_target_level=0.403,
    idle_threshold_scale=0.92,
)

BUILTIN_MIC_PROFILES: dict[str, MicProfile] = {
    PCM2902_PROFILE.name: PCM2902_PROFILE,
}


def match_builtin_mic_profile(device_name: str | None) -> MicProfile | None:
    if not device_name:
        return None
    normalized = device_name.casefold()
    for profile in BUILTIN_MIC_PROFILES.values():
        if any(pattern.casefold() in normalized for pattern in profile.device_patterns):
            return profile
    return None


def load_mic_profile(identifier: str | Path, *, device_name: str | None = None) -> MicProfile:
    key = str(identifier)
    if key == "auto":
        profile = match_builtin_mic_profile(device_name)
        if profile is None:
            raise ValueError("no built-in mic profile matched the selected audio device")
        return profile
    if key in BUILTIN_MIC_PROFILES:
        return BUILTIN_MIC_PROFILES[key]
    path = Path(identifier).expanduser()
    if path.exists():
        return mic_profile_from_dict(json.loads(path.read_text(encoding="utf-8")))
    raise ValueError(f"unknown mic profile: {identifier}")


def mic_profile_from_dict(data: dict[str, Any]) -> MicProfile:
    name = str(data.get("name") or "custom")
    patterns = data.get("device_patterns", ())
    device_patterns: tuple[str, ...]
    if isinstance(patterns, str):
        device_patterns = (patterns,)
    else:
        device_patterns = tuple(str(pattern) for pattern in patterns)
    analysis_data = data.get("analysis") or {}
    if not isinstance(analysis_data, dict):
        raise ValueError("mic profile analysis must be an object")
    return MicProfile(
        name=name,
        device_patterns=device_patterns,
        mic_noise_floor=_optional_float(data.get("mic_noise_floor")),
        mic_target_level=_optional_float(data.get("mic_target_level")),
        idle_threshold_scale=_optional_float(data.get("idle_threshold_scale")),
        analysis=AudioAnalysis(
            drop_bass_threshold=float(analysis_data.get("drop_bass_threshold", AudioAnalysis().drop_bass_threshold)),
            drop_bass_delta_threshold=float(
                analysis_data.get("drop_bass_delta_threshold", AudioAnalysis().drop_bass_delta_threshold)
            ),
            drop_onset_threshold=float(analysis_data.get("drop_onset_threshold", AudioAnalysis().drop_onset_threshold)),
            section_change_threshold=float(
                analysis_data.get("section_change_threshold", AudioAnalysis().section_change_threshold)
            ),
            section_onset_threshold=float(
                analysis_data.get("section_onset_threshold", AudioAnalysis().section_onset_threshold)
            ),
        ),
    )


def write_mic_profile(path: str | Path, profile: MicProfile) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("mic profile numeric values must be numbers")
    if isinstance(value, str | int | float):
        return float(value)
    raise ValueError("mic profile numeric values must be numbers")
