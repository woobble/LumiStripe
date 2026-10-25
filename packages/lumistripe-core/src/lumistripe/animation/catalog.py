"""The single typed registry for animations and audio-triggered effects."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from ..selector import AnimationMetadata, AnimationRole, animation_metadata
from .base import Animation, AnimationPlayer
from .settings import (
    COMET_SETTING_DEFINITIONS,
    PULSE_SETTING_DEFINITIONS,
    AnimationSettingDefinition,
    AnimationSettingType,
    AnimationSettingValue,
    AnimationSettingValues,
    normalize_animation_settings,
)

if TYPE_CHECKING:
    from ..effects.definitions import EffectDefinition


@dataclass(frozen=True, slots=True)
class AnimationDefinition:
    """All metadata required to instantiate and select one visual entry."""

    name: str
    factory: Callable[[], Animation]
    frame_ms: int
    frames_per_cycle: int
    automatic: bool
    metadata: AnimationMetadata | None = None
    display_name: str | None = None
    role: AnimationRole | None = None
    dynamic_metadata: AnimationMetadata | None = None
    effect_metadata: EffectDefinition | None = None
    settings: tuple[AnimationSettingDefinition, ...] = ()
    configuration_applier: Callable[
        [Animation, Mapping[str, AnimationSettingValue]], None
    ] | None = None
    configuration_validator: Callable[
        [Mapping[str, AnimationSettingValue]], None
    ] | None = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            return
        if self.display_name is None:
            object.__setattr__(self, "display_name", self.name.replace("_", " ").title())
        if self.role is None:
            object.__setattr__(self, "role", self.metadata.role)
        if self.dynamic_metadata is None:
            object.__setattr__(self, "dynamic_metadata", self.metadata)

    def normalize_settings(
        self,
        values: object | None,
        *,
        allow_partial: bool = False,
    ) -> AnimationSettingValues:
        if not self.settings:
            if values not in (None, {}):
                raise ValueError(f"animation {self.name} has no configurable settings")
            return {}
        normalized = normalize_animation_settings(
            self.settings, values, allow_partial=allow_partial
        )
        if self.configuration_validator is not None:
            self.configuration_validator(normalized)
        return normalized

    def apply_settings(
        self, animation: Animation, values: object | None = None
    ) -> AnimationSettingValues:
        normalized = self.normalize_settings(values, allow_partial=True)
        animation.configure(normalized)
        if self.configuration_applier is not None:
            self.configuration_applier(animation, normalized)
        return normalized

    def create(self, values: object | None = None) -> Animation:
        animation = self.factory()
        self.apply_settings(animation, values)
        return animation


class _ConfigurableAnimation(Protocol):
    def set_settings(self, values: Mapping[str, object]) -> None: ...


def _apply_animation_settings(
    animation: Animation, values: Mapping[str, AnimationSettingValue]
) -> None:
    configurable = cast(_ConfigurableAnimation, animation)
    configurable.set_settings(values)


def _validate_pulse_settings(values: Mapping[str, AnimationSettingValue]) -> None:
    minimum = cast(float, values["minimum_intensity"])
    maximum = cast(float, values["maximum_intensity"])
    if minimum > maximum:
        raise ValueError("minimum intensity must not exceed maximum intensity")


def _number_setting(
    id: str,
    label: str,
    default: int,
    description: str,
    *,
    minimum: int = 0,
    maximum: int = 100,
    unit: str = "%",
) -> AnimationSettingDefinition:
    return AnimationSettingDefinition(
        id=id,
        label=label,
        type=AnimationSettingType.NUMBER,
        default=default,
        minimum=minimum,
        maximum=maximum,
        step=1,
        unit=unit,
        description=description,
    )


def _enum_setting(
    id: str,
    label: str,
    default: str,
    choices: tuple[str, ...],
    description: str,
) -> AnimationSettingDefinition:
    return AnimationSettingDefinition(
        id=id,
        label=label,
        type=AnimationSettingType.ENUM,
        default=default,
        choices=choices,
        description=description,
    )


SPEED = _number_setting("speed", "Speed", 50, "How quickly the animation advances.")
INTENSITY = _number_setting(
    "intensity", "Intensity", 100, "Maximum brightness of the animation."
)
MUSIC_REACTIVITY = _number_setting(
    "music_reactivity", "Music reactivity", 100, "How strongly audio changes the animation."
)
DENSITY = _number_setting(
    "density", "Density", 40, "How many particles or details appear at once."
)
PARTICLE_SIZE = _number_setting(
    "particle_size", "Particle size", 35, "Size of each visible particle."
)
TAIL_LENGTH = _number_setting(
    "tail_length", "Trail length", 8, "Trail length relative to the strip."
)
WIDTH = _number_setting("width", "Width", 30, "Width relative to the strip.")
SPREAD = _number_setting("spread", "Spread", 60, "How far the effect spreads along the strip.")
FREQUENCY = _number_setting(
    "frequency", "Frequency", 50, "Spacing of repeated waves or pulses."
)
FADE_TIME = _number_setting(
    "fade_time", "Fade time", 50, "How long trails and particles remain visible."
)
DECAY = _number_setting("decay", "Decay", 50, "How quickly the effect fades away.")
COOLING = _number_setting("cooling", "Cooling", 50, "How quickly heat dissipates.")
SPARKING = _number_setting("sparking", "Sparking", 35, "How often new sparks appear.")
SENSITIVITY = _number_setting(
    "sensitivity", "Sensitivity", 50, "Audio level required to produce a response."
)
BEAT_SENSITIVITY = _number_setting(
    "beat_sensitivity", "Beat sensitivity", 100, "How strongly detected beats trigger accents."
)
FLASH_RATE = _number_setting(
    "flash_rate", "Flash rate", 50, "Flash timing from slow to fast.", minimum=1
)
DUTY_CYCLE = _number_setting(
    "duty_cycle", "On time", 30, "Fraction of each flash cycle spent illuminated.",
    minimum=5, maximum=90,
)
RED_SWEEP_DUTY_CYCLE = _number_setting(
    "duty_cycle", "On time", 90, "Fraction of each sweep cycle spent illuminated.",
    minimum=5, maximum=100,
)
RED_CHASE_DUTY_CYCLE = _number_setting(
    "duty_cycle", "On time", 50, "Fraction of each block pattern that is illuminated.",
    minimum=5, maximum=90,
)
SEPARATION = _number_setting(
    "separation", "Separation", 50, "Distance between moving elements relative to strip length."
)
COLOR_SPREAD = _number_setting(
    "color_spread", "Color spread", 50, "How quickly color changes across the strip."
)
VARIATION = _number_setting(
    "variation", "Variation", 50, "Amount of natural variation between particles or flashes."
)
COUNT = _number_setting(
    "count", "Count", 4, "Number of simultaneous elements.", minimum=1, maximum=12,
    unit="",
)
CHASE_COUNT = _number_setting(
    "count", "Count", 3, "Number of staggered light groups.", minimum=1, maximum=12,
    unit="",
)
DIRECTION = _enum_setting(
    "direction", "Direction", "bounce", ("forward", "reverse", "bounce"),
    "Travel forward, reverse, or bounce between both ends.",
)
PULSE_CURVE = _enum_setting(
    "pulse_curve", "Pulse curve", "sine", ("sine", "smoothstep", "sharp"),
    "Shape of the pulse between its dim and bright phases.",
)
PATTERN = _enum_setting(
    "pattern", "Pattern", "alternate", ("alternate", "double", "solid"),
    "Arrangement of repeated flashes.",
)
PALETTE = _enum_setting(
    "palette", "Color palette", "rainbow", ("rainbow", "neon", "fire", "custom"),
    "Choose a built-in palette or use one custom color.",
)
CUSTOM_COLOR = AnimationSettingDefinition(
    id="custom_color",
    label="Custom color",
    type=AnimationSettingType.COLOR,
    default="#7C3AED",
    description="Color used when the palette is Custom.",
    visible_when_option="palette",
    visible_when_value="custom",
)
PALETTE_SETTINGS = (PALETTE, CUSTOM_COLOR)


def _schema(*options: AnimationSettingDefinition) -> tuple[AnimationSettingDefinition, ...]:
    return options


def _effect_definitions() -> dict[str, EffectDefinition]:
    from ..effects.definitions import ACCENT_EFFECTS, RHYTHMIC_EFFECTS

    return {
        definition.name: definition
        for definition in (*RHYTHMIC_EFFECTS, *ACCENT_EFFECTS)
    }


def _entry(
    factory: Callable[[], Animation],
    frame_ms: int,
    frames_per_cycle: int,
    *,
    automatic: bool = True,
    effects: dict[str, EffectDefinition],
    settings: tuple[AnimationSettingDefinition, ...] = (),
    configuration_applier: Callable[
        [Animation, Mapping[str, AnimationSettingValue]], None
    ] | None = None,
    configuration_validator: Callable[
        [Mapping[str, AnimationSettingValue]], None
    ] | None = None,
) -> AnimationDefinition:
    instance = factory()
    metadata = animation_metadata(instance.name)
    return AnimationDefinition(
        name=instance.name,
        factory=factory,
        frame_ms=frame_ms,
        frames_per_cycle=frames_per_cycle,
        automatic=automatic,
        metadata=metadata,
        display_name=instance.name.replace("_", " ").title(),
        role=metadata.role,
        dynamic_metadata=metadata,
        effect_metadata=effects.get(instance.name),
        settings=settings,
        configuration_applier=configuration_applier,
        configuration_validator=configuration_validator,
    )


def party_catalog() -> tuple[AnimationDefinition, ...]:
    """Build the canonical party registry in playback order."""

    from ..effects import (
        BassDrop,
        BeatExplosion,
        BeatRipple,
        BeatTunnel,
        BeatWave,
        CenterBurst,
        ClubFlash,
        ColorBurst,
        Confetti,
        DropExplosion,
        DropWave,
        ElectricStorm,
        FireworkBurst,
        HardBeat,
        LightningStrike,
        MirrorFlash,
        PixelExplosion,
        Shockwave,
        SpectrumFlash,
    )
    from .aurora import Aurora
    from .bouncing_ball import BouncingBall
    from .bpm import Bpm
    from .color_wipe import ColorWipe
    from .comet import Comet
    from .comet_storm import CometStorm
    from .dance_floor import DanceFloor
    from .disco_comet import DiscoComet
    from .disco_sparkle import DiscoSparkle
    from .dual_comet import DualComet
    from .dual_laser import DualLaser
    from .fire import Fire
    from .glow_rush import GlowRush
    from .juggle import Juggle
    from .laser_sweep import LaserSweep
    from .neon_confetti import NeonConfetti
    from .neon_storm import NeonStorm
    from .peak_mirror import PeakMirror
    from .plasma_rave import PlasmaRave
    from .police import Police
    from .pulse import Pulse
    from .rainbow import Rainbow
    from .rainbow_cycle import RainbowCycle
    from .rainbow_strobe import RainbowStrobe
    from .rave_pulse import RavePulse
    from .rave_scanner import RaveScanner
    from .red_rave import RedBlackoutStrobe, RedRaveChase, RedRaveSweep
    from .sinelon import Sinelon
    from .strobe import Strobe
    from .strobe_chase import StrobeChase
    from .theater_chase import TheaterChase
    from .twinkle import Twinkle
    from .wave import Wave

    effects = _effect_definitions()
    definitions = (
        _entry(RainbowCycle, 15, 300, effects=effects, settings=_schema(SPEED, INTENSITY, *PALETTE_SETTINGS, COLOR_SPREAD, MUSIC_REACTIVITY)),
        _entry(
            Pulse,
            20,
            180,
            effects=effects,
            settings=PULSE_SETTING_DEFINITIONS,
            configuration_applier=_apply_animation_settings,
            configuration_validator=_validate_pulse_settings,
        ),
        _entry(Confetti, 15, 220, effects=effects, settings=_schema(COUNT, SPREAD, FADE_TIME, INTENSITY, *PALETTE_SETTINGS)),
        _entry(
            Comet,
            16,
            240,
            effects=effects,
            settings=COMET_SETTING_DEFINITIONS,
            configuration_applier=_apply_animation_settings,
        ),
        _entry(Shockwave, 16, 180, effects=effects, settings=_schema(SPEED, WIDTH, INTENSITY, *PALETTE_SETTINGS, BEAT_SENSITIVITY)),
        _entry(TheaterChase, 30, 220, effects=effects, settings=_schema(SPEED, CHASE_COUNT, INTENSITY, *PALETTE_SETTINGS)),
        _entry(Aurora, 18, 260, effects=effects, settings=_schema(SPEED, FREQUENCY, INTENSITY, *PALETTE_SETTINGS, MUSIC_REACTIVITY)),
        _entry(ColorWipe, 18, 220, effects=effects, settings=_schema(SPEED, TAIL_LENGTH, DIRECTION, *PALETTE_SETTINGS, MUSIC_REACTIVITY)),
        _entry(Fire, 20, 300, effects=effects, settings=_schema(COOLING, SPARKING, INTENSITY, MUSIC_REACTIVITY)),
        _entry(PeakMirror, 18, 220, effects=effects, settings=_schema(SENSITIVITY, DECAY, WIDTH, INTENSITY, *PALETTE_SETTINGS)),
        _entry(Wave, 18, 240, effects=effects, settings=_schema(SPEED, FREQUENCY, INTENSITY, *PALETTE_SETTINGS, MUSIC_REACTIVITY)),
        _entry(Twinkle, 15, 200, effects=effects, settings=_schema(DENSITY, FADE_TIME, PARTICLE_SIZE, INTENSITY, *PALETTE_SETTINGS)),
        _entry(BouncingBall, 20, 200, effects=effects, settings=_schema(SPEED, PARTICLE_SIZE, DIRECTION, INTENSITY, *PALETTE_SETTINGS)),
        _entry(DualComet, 16, 220, effects=effects, settings=_schema(SPEED, TAIL_LENGTH, SEPARATION, INTENSITY, *PALETTE_SETTINGS)),
        _entry(Rainbow, 20, 200, effects=effects, settings=_schema(SPEED, INTENSITY, *PALETTE_SETTINGS, COLOR_SPREAD)),
        _entry(Police, 25, 160, effects=effects, settings=_schema(SPEED, DUTY_CYCLE, INTENSITY, PATTERN)),
        _entry(Juggle, 18, 240, effects=effects, settings=_schema(SPEED, COUNT, PARTICLE_SIZE, INTENSITY, *PALETTE_SETTINGS)),
        _entry(Sinelon, 18, 200, effects=effects, settings=_schema(SPEED, FADE_TIME, INTENSITY, *PALETTE_SETTINGS, MUSIC_REACTIVITY)),
        _entry(Strobe, 12, 160, effects=effects, settings=_schema(FLASH_RATE, DUTY_CYCLE, INTENSITY, *PALETTE_SETTINGS)),
        _entry(Bpm, 20, 120, effects=effects, settings=_schema(BEAT_SENSITIVITY, DECAY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(BeatWave, 20, 200, effects=effects, settings=_schema(SPEED, WIDTH, INTENSITY, *PALETTE_SETTINGS, BEAT_SENSITIVITY)),
        _entry(DiscoSparkle, 12, 160, effects=effects, settings=_schema(DENSITY, FADE_TIME, VARIATION, INTENSITY, *PALETTE_SETTINGS)),
        _entry(BeatExplosion, 16, 140, effects=effects, settings=_schema(SPEED, WIDTH, INTENSITY, *PALETTE_SETTINGS, BEAT_SENSITIVITY)),
        _entry(CometStorm, 14, 180, effects=effects, settings=_schema(COUNT, SPEED, TAIL_LENGTH, INTENSITY, *PALETTE_SETTINGS)),
        _entry(LaserSweep, 12, 140, effects=effects, settings=_schema(SPEED, WIDTH, DIRECTION, INTENSITY, *PALETTE_SETTINGS)),
        _entry(PlasmaRave, 18, 220, effects=effects, settings=_schema(SPEED, FREQUENCY, INTENSITY, *PALETTE_SETTINGS, MUSIC_REACTIVITY)),
        _entry(FireworkBurst, 15, 160, effects=effects, settings=_schema(COUNT, SPREAD, FADE_TIME, INTENSITY, *PALETTE_SETTINGS)),
        _entry(LightningStrike, 10, 100, effects=effects, settings=_schema(SPREAD, WIDTH, DECAY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(BeatTunnel, 18, 200, effects=effects, settings=_schema(SPEED, WIDTH, INTENSITY, *PALETTE_SETTINGS, BEAT_SENSITIVITY)),
        _entry(DropExplosion, 20, 180, effects=effects, settings=_schema(SPEED, DECAY, DUTY_CYCLE, INTENSITY, MUSIC_REACTIVITY)),
        _entry(BassDrop, 20, 200, effects=effects, settings=_schema(SPEED, DECAY, INTENSITY, *PALETTE_SETTINGS, BEAT_SENSITIVITY)),
        _entry(RavePulse, 14, 160, effects=effects, settings=_schema(SPEED, PULSE_CURVE, INTENSITY, *PALETTE_SETTINGS, MUSIC_REACTIVITY)),
        _entry(NeonStorm, 12, 140, effects=effects, settings=_schema(COUNT, SPEED, DECAY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(PixelExplosion, 14, 140, effects=effects, settings=_schema(COUNT, SPREAD, DECAY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(DualLaser, 14, 160, effects=effects, settings=_schema(SPEED, WIDTH, SEPARATION, INTENSITY, *PALETTE_SETTINGS)),
        _entry(RainbowStrobe, 10, 120, effects=effects, settings=_schema(FLASH_RATE, DUTY_CYCLE, INTENSITY, COLOR_SPREAD, *PALETTE_SETTINGS)),
        _entry(BeatRipple, 18, 160, effects=effects, settings=_schema(SPEED, WIDTH, DECAY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(DanceFloor, 18, 200, effects=effects, settings=_schema(SPEED, FREQUENCY, INTENSITY, *PALETTE_SETTINGS, MUSIC_REACTIVITY)),
        _entry(ElectricStorm, 12, 120, effects=effects, settings=_schema(COUNT, FLASH_RATE, DECAY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(GlowRush, 16, 200, effects=effects, settings=_schema(SPEED, WIDTH, FREQUENCY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(HardBeat, 12, 100, effects=effects, settings=_schema(BEAT_SENSITIVITY, DECAY, DUTY_CYCLE, INTENSITY)),
        _entry(ClubFlash, 12, 120, effects=effects, settings=_schema(BEAT_SENSITIVITY, FLASH_RATE, DUTY_CYCLE, INTENSITY, *PALETTE_SETTINGS)),
        _entry(ColorBurst, 16, 180, effects=effects, settings=_schema(SPEED, SPREAD, DECAY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(DiscoComet, 14, 180, effects=effects, settings=_schema(SPEED, TAIL_LENGTH, DIRECTION, INTENSITY, *PALETTE_SETTINGS)),
        _entry(RaveScanner, 12, 140, effects=effects, settings=_schema(SPEED, WIDTH, DIRECTION, INTENSITY, *PALETTE_SETTINGS)),
        _entry(RedRaveSweep, 16, 180, effects=effects, settings=_schema(SPEED, WIDTH, RED_SWEEP_DUTY_CYCLE, INTENSITY)),
        _entry(RedRaveChase, 16, 160, effects=effects, settings=_schema(SPEED, COUNT, RED_CHASE_DUTY_CYCLE, INTENSITY)),
        _entry(RedBlackoutStrobe, 24, 120, effects=effects, settings=_schema(FLASH_RATE, DUTY_CYCLE, INTENSITY)),
        _entry(NeonConfetti, 12, 150, effects=effects, settings=_schema(COUNT, SPREAD, FADE_TIME, INTENSITY, *PALETTE_SETTINGS)),
        _entry(StrobeChase, 12, 120, effects=effects, settings=_schema(SPEED, COUNT, DUTY_CYCLE, INTENSITY, *PALETTE_SETTINGS)),
        _entry(CenterBurst, 16, 160, effects=effects, settings=_schema(SPEED, WIDTH, INTENSITY, *PALETTE_SETTINGS, BEAT_SENSITIVITY)),
        _entry(MirrorFlash, 14, 140, effects=effects, settings=_schema(WIDTH, DECAY, INTENSITY, *PALETTE_SETTINGS, BEAT_SENSITIVITY)),
        _entry(SpectrumFlash, 16, 180, effects=effects, settings=_schema(SENSITIVITY, WIDTH, DECAY, INTENSITY, *PALETTE_SETTINGS)),
        _entry(DropWave, 18, 180, effects=effects, settings=_schema(SPEED, WIDTH, DECAY, INTENSITY, *PALETTE_SETTINGS)),
    )
    return validate_animation_catalog(definitions)


def _definition_from_entry(entry: Any) -> AnimationDefinition:
    if isinstance(entry.definition, AnimationDefinition):
        return entry.definition
    metadata = animation_metadata(entry.animation)
    return AnimationDefinition(
        name=entry.animation.name,
        factory=type(entry.animation),
        frame_ms=entry.frame_ms,
        frames_per_cycle=entry.frames_per_cycle,
        automatic=entry.automatic,
        metadata=metadata,
        display_name=entry.animation.name.replace("_", " ").title(),
        role=metadata.role,
        dynamic_metadata=metadata,
        effect_metadata=_effect_definitions().get(entry.animation.name),
    )


def animation_catalog(
    player: AnimationPlayer | None = None,
) -> tuple[AnimationDefinition, ...]:
    """Return the canonical catalog or a validated custom player's entries."""

    if player is None:
        return party_catalog()
    return validate_animation_catalog(
        _definition_from_entry(entry) for entry in player.animations
    )


def dynamic_sequence_eligible(definition: AnimationDefinition) -> bool:
    """Return whether a catalog entry is safe as a Dynamic sequence base."""

    metadata = definition.dynamic_metadata
    return (
        definition.automatic
        and definition.role is AnimationRole.BASE
        and metadata is not None
        and metadata.role is AnimationRole.BASE
        and metadata.dynamic_safe
        and definition.effect_metadata is None
    )


def validate_animation_catalog(
    definitions: Iterable[AnimationDefinition],
) -> tuple[AnimationDefinition, ...]:
    normalized = tuple(definitions)
    names = [definition.name for definition in normalized]
    display_names = [definition.display_name for definition in normalized]
    if any(not name for name in names):
        raise ValueError("animation names must not be empty")
    if len(names) != len(set(names)):
        raise ValueError("animation names must be unique")
    if any(not display_name for display_name in display_names):
        raise ValueError("animation display names are required")
    if len(display_names) != len(set(display_names)):
        raise ValueError("animation display names must be unique")
    for definition in normalized:
        if not callable(definition.factory):
            raise TypeError(f"{definition.name} factory is missing")
        if definition.metadata is None:
            raise ValueError(f"{definition.name} metadata is missing")
        if definition.metadata.name != definition.name:
            raise ValueError(f"{definition.name} metadata name does not match")
        if definition.role is None:
            raise ValueError(f"{definition.name} role is missing")
        if definition.dynamic_metadata is None:
            raise ValueError(f"{definition.name} dynamic metadata is missing")
        if definition.frame_ms <= 0:
            raise ValueError(f"{definition.name} frame duration must be positive")
        if definition.frames_per_cycle <= 0:
            raise ValueError(f"{definition.name} cycle length must be positive")
        if definition.effect_metadata is not None and definition.effect_metadata.name != definition.name:
            raise ValueError(f"{definition.name} effect metadata name does not match")
    return normalized


__all__ = [
    "AnimationDefinition",
    "animation_catalog",
    "dynamic_sequence_eligible",
    "party_catalog",
    "validate_animation_catalog",
]
