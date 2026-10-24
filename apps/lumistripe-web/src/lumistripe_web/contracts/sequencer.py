"""Quantized animation sequencing settings contracts."""

from __future__ import annotations

from typing import Literal, cast

from lumistripe.playback import AnimationSequence, AnimationSequenceStep
from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    SequencerDiagnostics,
    SequencerSettingsRequest,
    SequencerSettingsResponse,
)


class AnimationSequenceStepContract(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, str_strip_whitespace=True
    )

    animation_name: str = Field(min_length=1, max_length=100)
    duration_bars: Literal[1, 2, 4, 8]

    def to_core(self) -> AnimationSequenceStep:
        return AnimationSequenceStep(
            animation_name=self.animation_name,
            duration_bars=self.duration_bars,
        )


class AnimationSequenceContract(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, str_strip_whitespace=True
    )

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    repeat: bool
    steps: tuple[AnimationSequenceStepContract, ...] = Field(
        min_length=1, max_length=32
    )

    @classmethod
    def from_core(cls, sequence: AnimationSequence) -> AnimationSequenceContract:
        return cls(
            id=sequence.id,
            name=sequence.name,
            repeat=sequence.repeat,
            steps=tuple(
                AnimationSequenceStepContract(
                    animation_name=step.animation_name,
                    duration_bars=cast(Literal[1, 2, 4, 8], step.duration_bars),
                )
                for step in sequence.steps
            ),
        )


class SequenceDefinitionRequest(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, str_strip_whitespace=True
    )

    name: str = Field(min_length=1, max_length=80)
    repeat: bool
    steps: list[AnimationSequenceStepContract] = Field(min_length=1, max_length=32)

    def to_core(self, sequence_id: str) -> AnimationSequence:
        return AnimationSequence(
            id=sequence_id,
            name=self.name,
            repeat=self.repeat,
            steps=tuple(step.to_core() for step in self.steps),
        )


class SequenceCreateRequest(SequenceDefinitionRequest):
    id: str = Field(min_length=1, max_length=64)

    def to_sequence(self) -> AnimationSequence:
        return self.to_core(self.id)


class SequenceActiveRequest(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, str_strip_whitespace=True
    )

    sequence_id: str | None = Field(default=..., min_length=1, max_length=64)


class SequenceLibraryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequences: tuple[AnimationSequenceContract, ...] = ()
    active_sequence_id: str | None = None


__all__ = [
    "AnimationSequenceContract",
    "AnimationSequenceStepContract",
    "SequenceActiveRequest",
    "SequenceCreateRequest",
    "SequenceDefinitionRequest",
    "SequenceLibraryResponse",
    "SequencerDiagnostics",
    "SequencerSettingsRequest",
    "SequencerSettingsResponse",
]
