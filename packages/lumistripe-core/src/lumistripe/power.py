from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from .buffers import PixelBuffer


@dataclass(frozen=True, slots=True)
class OutputPowerEstimate:
    """Estimated electrical load for one physical output."""

    output_id: str
    watts: float
    limit_watts: float | None = None
    applied_scale: float = 1.0

    def __post_init__(self) -> None:
        if not self.output_id.strip():
            raise ValueError("output_id must not be empty")
        if self.watts < 0.0:
            raise ValueError("watts must not be negative")
        if self.limit_watts is not None and self.limit_watts <= 0.0:
            raise ValueError("limit_watts must be greater than zero")
        if not 0.0 <= self.applied_scale <= 1.0:
            raise ValueError("applied_scale must be between zero and one")


@dataclass(frozen=True, slots=True)
class PowerBudgetResult:
    """Power estimate and the brightness scale selected for a frame."""

    enabled: bool
    budget_watts: float | None
    estimated_watts: float
    applied_scale: float
    limiting_output_id: str | None
    outputs: tuple[OutputPowerEstimate, ...]


def estimate_frame_power(
    pixels: PixelBuffer | npt.ArrayLike,
    *,
    voltage_v: float,
    full_white_current_a: float,
) -> float:
    """Estimate watts from an RGBA frame.

    ``full_white_current_a`` is the current drawn by one pixel when all three
    RGB channels are at 255 and alpha is fully on. Alpha is treated as an
    additional intensity multiplier, which matches the renderer's brightness
    representation.
    """

    if voltage_v <= 0.0:
        raise ValueError("voltage_v must be greater than zero")
    if full_white_current_a <= 0.0:
        raise ValueError("full_white_current_a must be greater than zero")
    frame = np.asarray(pixels)
    if frame.ndim != 2 or frame.shape[1] < 4:
        raise ValueError("power estimation requires an RGBA pixel buffer")
    if frame.shape[0] == 0:
        return 0.0
    normalized = np.clip(frame[:, :4].astype(np.float64), 0.0, 255.0) / 255.0
    channel_duty = normalized[:, :3].mean(axis=1) * normalized[:, 3]
    current_a = float(channel_duty.sum()) * full_white_current_a
    return current_a * voltage_v


def apply_power_budget(
    estimates: Sequence[OutputPowerEstimate],
    *,
    enabled: bool,
    budget_watts: float | None,
) -> PowerBudgetResult:
    """Choose one global scale from global and per-output limits."""

    if budget_watts is not None and budget_watts <= 0.0:
        raise ValueError("budget_watts must be greater than zero")
    output_values = tuple(estimates)
    total_watts = sum(max(0.0, estimate.watts) for estimate in output_values)
    if not enabled:
        return PowerBudgetResult(
            enabled=False,
            budget_watts=budget_watts,
            estimated_watts=total_watts,
            applied_scale=1.0,
            limiting_output_id=None,
            outputs=output_values,
        )
    if budget_watts is None:
        raise ValueError("an enabled power budget requires budget_watts")

    candidates: list[tuple[float, str | None]] = []
    if total_watts > 0.0:
        candidates.append((budget_watts / total_watts, None))
    for estimate in output_values:
        if estimate.limit_watts is None or estimate.watts <= 0.0:
            continue
        candidates.append((estimate.limit_watts / estimate.watts, estimate.output_id))

    if not candidates:
        scale = 1.0
        limiting_output_id = None
    else:
        scale, limiting_output_id = min(candidates, key=lambda item: item[0])
        scale = min(1.0, max(0.0, scale))

    return PowerBudgetResult(
        enabled=True,
        budget_watts=budget_watts,
        estimated_watts=total_watts,
        applied_scale=scale,
        limiting_output_id=limiting_output_id,
        outputs=tuple(
            OutputPowerEstimate(
                output_id=estimate.output_id,
                watts=estimate.watts,
                limit_watts=estimate.limit_watts,
                applied_scale=scale,
            )
            for estimate in output_values
        ),
    )
