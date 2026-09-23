"""Hardware-facing rendering adapter for immutable playback plans."""

from __future__ import annotations

from dataclasses import dataclass

from ..color import Color
from ..controller import BrightnessController, Controller
from .state import RenderPlan


@dataclass(slots=True)
class PlaybackRenderer:
    """Apply a plan to a controller; policy remains in ``state.py``."""

    def render_solid(self, plan: RenderPlan, controller: Controller) -> None:
        color: Color | None = plan.decision.solid_color
        if color is None:
            raise ValueError("solid render plans require a solid color")
        BrightnessController(controller, plan.decision.brightness).fill(color)
        if plan.flush:
            controller.flush()

    def clear(self, plan: RenderPlan, controller: Controller) -> None:
        controller.clear()
        if plan.flush:
            controller.flush()


__all__ = ["PlaybackRenderer"]
