"""Output-facing imports for controllers and physical stripe backends."""

from .composition import (
    BrightnessController,
    ColorCorrectionController,
    CompositeController,
    DualController,
    MultiController,
    ReversedController,
    ScaledMultiController,
)
from .controller import Controller, NullController
from .hardware import Config, GPIOStripe, SPIConfig, SPIStripe, Stripe, SubStripe

__all__ = [
    "BrightnessController",
    "ColorCorrectionController",
    "CompositeController",
    "Config",
    "Controller",
    "DualController",
    "GPIOStripe",
    "MultiController",
    "NullController",
    "ReversedController",
    "SPIConfig",
    "SPIStripe",
    "ScaledMultiController",
    "Stripe",
    "SubStripe",
]
