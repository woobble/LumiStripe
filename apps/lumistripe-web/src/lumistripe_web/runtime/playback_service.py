"""Pure playback policy decisions used by the single-writer worker."""

from __future__ import annotations

from dataclasses import dataclass

from lumistripe.playback import AudioSource, PlaybackMode


class PlaybackPolicyError(ValueError):
    """The requested mode cannot run with the selected audio topology."""


@dataclass(frozen=True, slots=True)
class PlaybackDecision:
    """Side-effect-free result of validating a requested playback mode."""

    mode: PlaybackMode
    audio_source: AudioSource

    @property
    def requires_audio(self) -> bool:
        return self.mode is PlaybackMode.DYNAMIC


class PlaybackPolicy:
    """Validate mode/audio combinations without touching playback engines."""

    def decide(
        self,
        mode: PlaybackMode,
        configured_audio_source: AudioSource,
        *,
        audio_input_available: bool,
        unavailable_message: str | None = None,
    ) -> PlaybackDecision:
        if mode is not PlaybackMode.DYNAMIC:
            return PlaybackDecision(mode=mode, audio_source=AudioSource.OFF)
        if configured_audio_source is AudioSource.OFF:
            raise PlaybackPolicyError("dynamic mode requires demo or microphone audio")
        if (
            configured_audio_source
            in {AudioSource.MIC, AudioSource.BLUETOOTH, AudioSource.SPOTIFY}
            and not audio_input_available
        ):
            raise PlaybackPolicyError(
                unavailable_message or "the selected audio input is unavailable"
            )
        return PlaybackDecision(mode=mode, audio_source=configured_audio_source)


__all__ = ["PlaybackDecision", "PlaybackPolicy", "PlaybackPolicyError"]
