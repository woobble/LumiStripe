"""Output topology primitives used by the runtime worker."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt
from lumistripe import (
    Color,
    ColorCorrectionController,
    Controller,
    OutputPowerEstimate,
    PixelBuffer,
    ScaledMultiController,
    ZoneMappingController,
    apply_power_budget,
    estimate_frame_power,
)
from lumistripe.render.kernels import apply_alpha_scale_into, correct_rgba_into

from ..models import PowerBudgetState, PowerOutputState
from ..settings import StripeTopologySettings


class OutputGateController(Controller):
    """Suppress output flushes while preserving the latest rendered frame."""

    def __init__(self, inner: Controller) -> None:
        self._inner = inner
        self._blackout = False
        self._last_successful_update_at: datetime | None = None
        self._last_successful_update_monotonic: float | None = None

    @property
    def blackout(self) -> bool:
        return self._blackout

    @property
    def last_successful_update_at(self) -> datetime | None:
        return self._last_successful_update_at

    @property
    def last_successful_update_age_seconds(self) -> float | None:
        if self._last_successful_update_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self._last_successful_update_monotonic)

    def _record_successful_update(self) -> None:
        self._last_successful_update_at = datetime.now(UTC)
        self._last_successful_update_monotonic = time.monotonic()

    def set_blackout(self, enabled: bool) -> None:
        if enabled == self._blackout:
            return
        if enabled:
            buffered_frame = self._inner.pixels().copy()
            self._inner.clear()
            self._inner.force_flush()
            self._record_successful_update()
            self._inner.set_pixels(buffered_frame)
            self._blackout = True
            return
        self._blackout = False
        self.force_flush()

    @property
    def length(self) -> int:
        return self._inner.length

    def pixels(self) -> PixelBuffer:
        return self._inner.pixels()

    def pixel(self, index: int) -> Color:
        return self._inner.pixel(index)

    def set_pixel(self, index: int, color: Color) -> None:
        self._inner.set_pixel(index, color)

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        self._inner.set_pixels(colors)

    def fill(self, color: Color) -> None:
        self._inner.fill(color)

    def clear(self) -> None:
        self._inner.clear()

    def flush(self) -> None:
        if not self._blackout:
            self._inner.flush()
            self._record_successful_update()

    def force_flush(self) -> None:
        if not self._blackout:
            self._inner.force_flush()
            self._record_successful_update()

    def close(self) -> None:
        self._inner.close()


@dataclass(slots=True)
class _OutputFrameBuffers:
    """Reusable worker-owned buffers for one physical output."""

    source: PixelBuffer
    mapped: PixelBuffer
    corrected: PixelBuffer
    scaled: PixelBuffer
    correction: npt.NDArray[np.uint16]
    rgb_scratch: npt.NDArray[np.uint16]
    alpha_scratch: npt.NDArray[np.float64]

    @classmethod
    def allocate(cls, length: int, logical_length: int) -> _OutputFrameBuffers:
        return cls(
            source=np.empty((length, 4), dtype=np.uint8),
            mapped=np.empty((logical_length, 4), dtype=np.uint8),
            corrected=np.empty((length, 4), dtype=np.uint8),
            scaled=np.empty((length, 4), dtype=np.uint8),
            correction=np.full(3, 255, dtype=np.uint16),
            rgb_scratch=np.empty((length, 3), dtype=np.uint16),
            alpha_scratch=np.empty(length, dtype=np.float64),
        )


def allocate_output_frame_buffers(
    gates: Sequence[OutputGateController],
    renderers: Sequence[ZoneMappingController],
) -> tuple[_OutputFrameBuffers, ...]:
    """Allocate reusable transform buffers when the output topology changes."""
    return tuple(
        _OutputFrameBuffers.allocate(
            gate.length,
            renderers[index].length if index < len(renderers) else gate.length,
        )
        for index, gate in enumerate(gates)
    )


def power_budget_state_for(
    topology: StripeTopologySettings,
    *,
    estimated_watts: float = 0.0,
    applied_scale: float = 1.0,
    limiting_output_id: str | None = None,
    outputs: tuple[PowerOutputState, ...] = (),
) -> PowerBudgetState:
    """Build the immutable dashboard view of a topology's power budget."""
    shared_supply_enabled = topology.shared_supply_enabled
    return PowerBudgetState(
        enabled=topology.power_limiting_enabled,
        budget_watts=topology.effective_power_budget_watts,
        estimated_watts=estimated_watts,
        applied_scale=applied_scale,
        limiting_output_id=limiting_output_id,
        outputs=outputs,
        shared_supply_enabled=shared_supply_enabled,
        supply_capacity_watts=(
            topology.supply_capacity_watts if shared_supply_enabled else None
        ),
        pi_reserve_watts=topology.pi_reserve_watts if shared_supply_enabled else None,
        available_led_watts=(
            topology.available_led_watts if shared_supply_enabled else None
        ),
    )


def flush_power_limited_outputs(
    topology: StripeTopologySettings,
    controller: OutputGateController | None,
    raw_controller: Controller | None,
    gates: Sequence[OutputGateController],
    renderers: Sequence[ZoneMappingController],
    corrections: Sequence[ColorCorrectionController],
    buffers: Sequence[_OutputFrameBuffers],
) -> tuple[PowerBudgetState, tuple[PixelBuffer | None, ...] | None]:
    """Estimate, limit, flush and prepare reusable preview frames for outputs."""
    if not gates:
        if controller is not None:
            controller.flush()
        return power_budget_state_for(topology), None

    shared_blackout = (
        topology.layout != "independent"
        and controller is not None
        and controller.blackout
    )
    if len(renderers) == len(gates):
        if topology.layout == "mirrored" and isinstance(
            raw_controller, ScaledMultiController
        ):
            mapped_frames = raw_controller.output_pixels_into(
                tuple(frame_buffers.mapped for frame_buffers in buffers)
            )
            for renderer, frame in zip(renderers, mapped_frames, strict=True):
                renderer.set_pixels(frame)
        source_frames = tuple(renderer.output_pixels() for renderer in renderers)
        for gate, frame in zip(gates, source_frames, strict=True):
            gate.set_pixels(frame)
    elif topology.layout == "mirrored" and isinstance(
        raw_controller, ScaledMultiController
    ):
        source_frames = raw_controller.output_pixels_into(
            tuple(frame_buffers.mapped for frame_buffers in buffers)
        )
        for gate, frame in zip(gates, source_frames, strict=True):
            gate.set_pixels(frame)
    else:
        for gate, frame_buffers in zip(gates, buffers, strict=True):
            np.copyto(frame_buffers.source, gate.pixels())
        source_frames = tuple(frame_buffers.source for frame_buffers in buffers)

    estimates: list[OutputPowerEstimate] = []
    for index, (output, gate, frame, frame_buffers) in enumerate(
        zip(topology.outputs, gates, source_frames, buffers, strict=True)
    ):
        if index < len(corrections):
            correction = corrections[index].correction
            frame_buffers.correction[:] = (
                correction.red,
                correction.green,
                correction.blue,
            )
        correct_rgba_into(
            frame,
            frame_buffers.corrected,
            frame_buffers.correction,
            frame_buffers.rgb_scratch,
        )
        watts = 0.0
        if not shared_blackout and not gate.blackout:
            watts = estimate_frame_power(
                frame_buffers.corrected,
                voltage_v=output.voltage_v,
                full_white_current_a=output.full_white_current_a,
            )
        estimates.append(
            OutputPowerEstimate(
                output_id=output.id,
                watts=watts,
                limit_watts=output.power_limit_watts,
            )
        )

    result = apply_power_budget(
        estimates,
        enabled=topology.power_limiting_enabled and not shared_blackout,
        budget_watts=topology.effective_power_budget_watts,
    )
    state = power_budget_state_for(
        topology,
        estimated_watts=result.estimated_watts,
        applied_scale=result.applied_scale,
        limiting_output_id=result.limiting_output_id,
        outputs=tuple(
            PowerOutputState(
                output_id=estimate.output_id,
                estimated_watts=estimate.watts,
                limit_watts=estimate.limit_watts,
                applied_scale=result.applied_scale,
            )
            for estimate in result.outputs
        ),
    )

    preview_frames: list[PixelBuffer | None] = []
    try:
        for gate, frame, frame_buffers in zip(gates, source_frames, buffers, strict=True):
            if shared_blackout or gate.blackout:
                preview_frames.append(None)
                continue
            apply_alpha_scale_into(
                frame,
                frame_buffers.corrected,
                frame_buffers.scaled,
                result.applied_scale,
                frame_buffers.alpha_scratch,
            )
            gate.set_pixels(frame_buffers.scaled)
            gate.flush()
            preview_frames.append(frame_buffers.corrected)
    finally:
        for gate, frame in zip(gates, source_frames, strict=True):
            gate.set_pixels(frame)

    return state, tuple(preview_frames)


def copy_output_pixels(controller: Controller) -> np.ndarray:
    """Return an owned frame for preview and power-budget calculations."""

    return controller.pixels().copy()


__all__ = ["OutputGateController", "copy_output_pixels"]
