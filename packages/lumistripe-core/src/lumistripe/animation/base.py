from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from colorsys import rgb_to_hsv
from dataclasses import dataclass, field, replace
from enum import Enum
from math import ceil, floor, isfinite
from typing import Any, TypeVar, cast

import numpy as np

from ..audio.types import AudioFrame, BandTuple
from ..buffers import PixelBuffer
from ..color import Color, Hsl, Hsla, Rgba
from ..controller import BrightnessController, Controller
from ..render import NATIVE_RENDER_MIN_PIXELS, native_render_dispatch_enabled

_SettingValue = TypeVar("_SettingValue")


class Animation(ABC):
    native_render_enabled = False
    native_render_min_pixels = NATIVE_RENDER_MIN_PIXELS

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        """Render with an optional native kernel, returning whether it handled the frame."""

        return False

    def configure(self, values: Mapping[str, object]) -> None:
        """Store validated configuration separately from renderer state."""

        self._animation_configuration = dict(values)

    def setting(self, name: str, default: _SettingValue) -> _SettingValue:
        """Read a catalog-defined option while keeping renderer defaults explicit."""

        values = getattr(self, "_animation_configuration", {})
        return cast(_SettingValue, values.get(name, default))

    def effective_frame(self, frame: int) -> int:
        values = getattr(self, "_animation_configuration", {})
        # Comet and Pulse own typed speed settings and apply them to their
        # motion curves. For the rest of the catalog, scaling the phase here
        # gives frame-driven renderers one consistent speed control.
        if "speed" not in values or hasattr(
            getattr(self, "settings", None), "speed"
        ):
            return frame
        speed = float(values["speed"])
        return round(frame * (0.25 + speed * 0.015))

    def effective_audio(self, audio: AudioFrame) -> AudioFrame | None:
        typed_settings = getattr(self, "settings", None)
        if hasattr(typed_settings, "music_reactivity"):
            return audio
        values = getattr(self, "_animation_configuration", {})
        reactivity = float(values.get("music_reactivity", 100)) / 100.0
        if reactivity <= 0.0:
            return None
        sensitivity = float(values.get("sensitivity", 50)) / 100.0
        gain = reactivity * (0.5 + sensitivity)
        beat_sensitivity = float(values.get("beat_sensitivity", 100)) / 100.0
        beat_gain = reactivity * beat_sensitivity
        if gain == 1.0 and beat_gain == 1.0:
            return audio
        beat_strength = audio.beat_strength * beat_gain
        beat = audio.beat and beat_gain > 0.0 and beat_strength >= 0.08
        return replace(
            audio,
            rms=min(1.0, audio.rms * gain),
            bands=cast(
                BandTuple, tuple(min(1.0, value * gain) for value in audio.bands)
            ),
            beat=beat,
            beat_strength=min(1.0, beat_strength),
        )

    def output_controller(
        self, controller: Controller, brightness: float
    ) -> Controller:
        configuration = getattr(self, "_animation_configuration", {})
        typed_settings = getattr(self, "settings", None)
        target: Controller = controller
        palette = configuration.get("palette", "rainbow")
        if palette != "rainbow" and not hasattr(typed_settings, "palette"):
            target = _PaletteController(
                target,
                cast(str, palette),
                cast(str, configuration.get("custom_color", "#7C3AED")),
            )
        return BrightnessController(target, self._output_brightness(brightness))

    def _output_brightness(self, brightness: float) -> float:
        """Return the configured output brightness before controller clamping."""

        configuration = getattr(self, "_animation_configuration", {})
        typed_settings = getattr(self, "settings", None)
        intensity = (
            1.0
            if hasattr(typed_settings, "intensity")
            else float(configuration.get("intensity", 100)) / 100.0
        )
        return brightness * intensity

    def _native_rgba(self, color: Color, brightness: float) -> tuple[int, int, int, int]:
        """Apply controller palette and brightness semantics to one native pixel."""

        red, green, blue, alpha = color.to_rgba()
        output_brightness = max(0.0, min(1.0, float(self._output_brightness(brightness))))
        mapped: Color = Rgba(red, green, blue, alpha * output_brightness)
        configuration = getattr(self, "_animation_configuration", {})
        palette = configuration.get("palette", "rainbow")
        typed_settings = getattr(self, "settings", None)
        if palette != "rainbow" and not hasattr(typed_settings, "palette"):
            custom_color = cast(str, configuration.get("custom_color", "#7C3AED"))
            if palette == "custom":
                value = int(custom_color[1:], 16)
                mapped = Rgba(
                    (value >> 16) & 0xFF,
                    (value >> 8) & 0xFF,
                    value & 0xFF,
                    mapped.to_rgba()[3],
                )
            else:
                mapped_red, mapped_green, mapped_blue, mapped_alpha = mapped.to_rgba()
                hue, saturation_value, hsv_value = rgb_to_hsv(
                    mapped_red / 255.0, mapped_green / 255.0, mapped_blue / 255.0
                )
                hue *= 360.0
                saturation = int(saturation_value * 100.0)
                lightness = int(36.0 + hsv_value * 24.0)
                from .palettes import palette_color

                mapped = palette_color(
                    cast(str, palette),
                    hue,
                    alpha=mapped_alpha,
                    custom_color=custom_color,
                    lightness=lightness,
                    saturation=saturation,
                )
        channels = mapped.as_rgba_array()
        return tuple(int(channel) for channel in channels)  # type: ignore[return-value]

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def tick(self, frame: int, controller: Controller) -> None:
        raise NotImplementedError

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        self.tick(frame, controller)

    def reset(self) -> None:
        """Reset runtime state before this animation is activated again."""

    @property
    def metadata(self):
        from ..selector import animation_metadata

        return animation_metadata(self.name)


class _PaletteController(Controller):
    """Apply palette color mapping without changing transparency."""

    def __init__(self, inner: Controller, palette: str, custom_color: str) -> None:
        self._inner = inner
        self._palette = palette
        self._custom_color = custom_color

    @property
    def length(self) -> int:
        return self._inner.length

    def pixels(self):
        return self._inner.pixels()

    def pixel(self, index: int) -> Color:
        return self._inner.pixel(index)

    def set_pixel(self, index: int, color: Color) -> None:
        self._inner.set_pixel(index, self._transform(color))

    def set_pixels(self, colors: Any) -> None:
        from collections.abc import Sequence

        if isinstance(colors, Sequence) and colors and isinstance(colors[0], Color):
            self._inner.set_pixels(
                [self._transform(cast(Color, color)) for color in colors]
            )
            return
        self._inner.set_pixels(colors)

    def fill(self, color: Color) -> None:
        self._inner.fill(self._transform(color))

    def clear(self) -> None:
        self._inner.clear()

    def flush(self) -> None:
        self._inner.flush()

    def _transform(self, color: Color) -> Color:
        from .palettes import palette_color

        red, green, blue, alpha = color.to_rgba()
        if self._palette == "custom":
            rgb_value = int(self._custom_color[1:], 16)
            return Rgba(
                (rgb_value >> 16) & 0xFF,
                (rgb_value >> 8) & 0xFF,
                rgb_value & 0xFF,
                alpha,
            )
        if isinstance(color, (Hsl, Hsla)):
            hue = float(color.h)
            saturation = int(color.s)
            lightness = int(color.lightness)
        else:
            hue, saturation_value, hsv_value = rgb_to_hsv(
                red / 255.0, green / 255.0, blue / 255.0
            )
            hue *= 360.0
            saturation = int(saturation_value * 100.0)
            lightness = int(36.0 + hsv_value * 24.0)
        return palette_color(
            self._palette,
            hue,
            alpha=alpha,
            custom_color=self._custom_color,
            lightness=lightness,
            saturation=saturation,
        )


class TransitionEffect(str, Enum):
    CROSSFADE = "crossfade"
    FADE_BLACK = "fade_black"
    WIPE = "wipe"


@dataclass(slots=True)
class _AnimationEntry:
    animation: Animation
    frame_ms: int
    frames_per_cycle: int
    automatic: bool
    definition: Any = None
    configuration: dict[str, object] = field(default_factory=dict)

    def fresh_animation(self) -> Animation:
        if self.definition is not None and hasattr(self.definition, "create"):
            return self.definition.create(self.configuration)
        animation_type: type[Animation] = type(self.animation)
        try:
            return animation_type()
        except TypeError:
            self.animation.reset()
            return self.animation

    def restart(self) -> None:
        self.animation = self.fresh_animation()


@dataclass(slots=True)
class AnimationPlayer:
    animations: list[_AnimationEntry] = field(default_factory=list)
    index: int = 0
    frame: int = 0
    brightness: float = 1.0
    audio_enabled: bool = True
    transition_ms: int = 0
    transition_effect: TransitionEffect = field(
        default=TransitionEffect.CROSSFADE, kw_only=True
    )
    _audio_snapshot: Callable[[], AudioFrame] | None = None
    _transition_duration_ms: int = 0
    _transition_started_at_s: float | None = None
    _active_transition_effect: TransitionEffect | None = None
    _transition_source: Any = None
    _transition_buffer: Any = None
    _transition_source_float: Any = None
    _transition_target_float: Any = None
    _transition_work_float: Any = None
    _transition_output: Any = None

    def add(self, animation: Animation, frame_ms: int, frames_per_cycle: int) -> None:
        self.animations.append(
            _AnimationEntry(animation, frame_ms, frames_per_cycle, True)
        )

    def add_utility(
        self, animation: Animation, frame_ms: int, frames_per_cycle: int
    ) -> None:
        self.animations.append(
            _AnimationEntry(animation, frame_ms, frames_per_cycle, False)
        )

    @classmethod
    def party(cls) -> AnimationPlayer:
        from .catalog import party_catalog

        return cls.from_catalog(party_catalog())

    @classmethod
    def from_catalog(cls, definitions: tuple[Any, ...]) -> AnimationPlayer:
        player = cls()
        for definition in definitions:
            configuration = definition.normalize_settings(
                None, allow_partial=True
            )
            animation = definition.create(configuration)
            player.animations.append(
                _AnimationEntry(
                    animation,
                    definition.frame_ms,
                    definition.frames_per_cycle,
                    definition.automatic,
                    definition,
                    configuration,
                )
            )
        return player

    def set_brightness(self, brightness: float) -> None:
        value = float(brightness)
        if not isfinite(value):
            raise ValueError("brightness must be finite")
        self.brightness = max(0.0, min(1.0, value))

    def set_animation_settings(
        self, name: str, values: object
    ) -> dict[str, object]:
        """Validate and apply settings without replacing animation runtime state."""

        entry = next(
            (item for item in self.animations if item.animation.name == name), None
        )
        if entry is None:
            raise ValueError(f"unknown animation: {name}")
        if entry.definition is None or not hasattr(entry.definition, "normalize_settings"):
            raise ValueError(f"animation {name} has no configurable settings")
        normalized = entry.definition.normalize_settings(values)
        entry.definition.apply_settings(entry.animation, normalized)
        entry.configuration = dict(normalized)
        return dict(normalized)

    def animation_settings(self, name: str) -> dict[str, object] | None:
        entry = next(
            (item for item in self.animations if item.animation.name == name), None
        )
        if entry is None or entry.definition is None or not entry.definition.settings:
            return None
        return dict(entry.configuration)

    def set_audio_snapshot(self, snapshot: Callable[[], AudioFrame]) -> None:
        self._audio_snapshot = snapshot

    def clear_audio_snapshot(self) -> None:
        self._audio_snapshot = None

    def step(
        self,
        controller: Controller,
        *,
        audio_frame: AudioFrame | None = None,
        flush: bool = True,
        now_s: float | None = None,
        transition_external: bool = False,
    ) -> float:
        _validate_transition_time(now_s)
        if not self.animations:
            return 0.05

        if (
            audio_frame is None
            and self._audio_snapshot is not None
            and self.audio_enabled
        ):
            audio_frame = self._audio_snapshot()
        if not self.audio_enabled:
            audio_frame = None
        entry = self.animations[self.index]
        animation_frame = entry.animation.effective_frame(self.frame)
        target = controller
        if self.transition_active and not transition_external:
            from ..stripe import Stripe

            if self._transition_source is None:
                self.capture_transition_source(controller.pixels())
            if (
                self._transition_buffer is None
                or self._transition_buffer.length != controller.length
            ):
                self._transition_buffer = Stripe(controller.length)
            target = self._transition_buffer
        configured_audio = (
            None
            if audio_frame is None
            else entry.animation.effective_audio(audio_frame)
        )
        rendered = (
            entry.animation._render_native(
                animation_frame, target.pixels(), configured_audio, self.brightness
            )
            if entry.animation.native_render_enabled
            and native_render_dispatch_enabled(
                target.length, entry.animation.native_render_min_pixels
            )
            else False
        )
        if not rendered:
            bright = entry.animation.output_controller(target, self.brightness)
            if configured_audio is None:
                entry.animation.tick(animation_frame, bright)
            else:
                entry.animation.tick_audio(animation_frame, bright, configured_audio)
        if self.transition_active and not transition_external:
            assert self._transition_buffer is not None
            self.apply_transition(
                controller,
                now_s=now_s,
                flush=flush,
                target_pixels=self._transition_buffer.pixels(),
            )
        elif flush:
            controller.flush()
        self.frame += 1
        return entry.frame_ms / 1000.0

    @property
    def transition_active(self) -> bool:
        return self._transition_duration_ms > 0

    @property
    def transition_progress(self) -> float:
        return self.transition_progress_at()

    def transition_progress_at(self, now_s: float | None = None) -> float:
        _validate_transition_time(now_s)
        if not self.transition_active:
            return 1.0
        started_at = self._transition_started_at_s
        if started_at is None:
            return 0.0
        now = time.monotonic() if now_s is None else now_s
        return min(
            1.0,
            max(0.0, now - started_at) * 1000.0
            / max(self._transition_duration_ms, 1),
        )

    def set_index(
        self,
        index: int,
        *,
        transition_ms: int | None = None,
        restart: bool = True,
        now_s: float | None = None,
    ) -> None:
        _validate_transition_time(now_s)
        next_index = min(max(index, 0), max(len(self.animations) - 1, 0))
        changed = next_index != self.index
        self.index = next_index
        self.frame = 0
        if self.animations and restart:
            self.animations[self.index].restart()
        duration = self.transition_ms if transition_ms is None else transition_ms
        if changed and duration > 0:
            self.begin_transition(duration_ms=duration, now_s=now_s)
        else:
            self.cancel_transition()

    def next(self) -> None:
        if not self.animations:
            return
        self.set_index((self.index + 1) % len(self.animations))

    def prev(self) -> None:
        if not self.animations:
            return
        self.set_index(len(self.animations) - 1 if self.index == 0 else self.index - 1)

    def begin_transition(
        self,
        *,
        duration_ms: int,
        source: Any = None,
        now_s: float | None = None,
        effect: TransitionEffect | None = None,
    ) -> None:
        _validate_transition_time(now_s)
        duration_ms = max(int(duration_ms), 0)
        if duration_ms == 0:
            self.cancel_transition()
            return
        self._transition_duration_ms = duration_ms
        self._transition_started_at_s = time.monotonic() if now_s is None else now_s
        self._active_transition_effect = (
            self.transition_effect if effect is None else TransitionEffect(effect)
        )
        self._transition_source = None
        if source is not None:
            self.capture_transition_source(source)

    def cancel_transition(self) -> None:
        self._transition_duration_ms = 0
        self._transition_started_at_s = None
        self._active_transition_effect = None
        self._transition_source = None

    def capture_transition_source(self, source: Any) -> None:
        pixels = np.asarray(source, dtype=np.uint8)
        if self._transition_source is None or self._transition_source.shape != pixels.shape:
            self._transition_source = np.empty_like(pixels)
        np.copyto(self._transition_source, pixels)

    def ensure_transition_source(self, source: Any) -> None:
        if self._transition_source is None:
            self.capture_transition_source(source)

    def apply_transition(
        self,
        controller: Controller,
        *,
        now_s: float | None = None,
        flush: bool = False,
        target_pixels: Any = None,
    ) -> None:
        _validate_transition_time(now_s)
        if not self.transition_active:
            if flush:
                controller.flush()
            return
        target = controller.pixels() if target_pixels is None else target_pixels
        if self._transition_source is None or self._transition_source.shape != target.shape:
            self.capture_transition_source(target)
        assert self._transition_source is not None
        shape = target.shape
        if self._transition_output is None or self._transition_output.shape != shape:
            self._transition_output = np.empty(shape, dtype=np.uint8)
        amount = self.transition_progress_at(now_s)
        effect = self._active_transition_effect or self.transition_effect

        if effect is TransitionEffect.WIPE:
            self._apply_wipe_transition(target, amount)
            controller.set_pixels(self._transition_output)
            if flush:
                controller.flush()
            if amount >= 1.0:
                self.cancel_transition()
            return

        if (
            self._transition_source_float is None
            or self._transition_source_float.shape != shape
        ):
            self._transition_source_float = np.empty(shape, dtype=np.float32)
            self._transition_target_float = np.empty(shape, dtype=np.float32)
            self._transition_work_float = np.empty(shape, dtype=np.float32)
        np.copyto(self._transition_source_float, self._transition_source)
        np.copyto(self._transition_target_float, target)
        if effect is TransitionEffect.FADE_BLACK:
            if amount <= 0.5:
                frame = self._transition_source_float
                brightness = 1.0 - amount * 2.0
            else:
                frame = self._transition_target_float
                brightness = (amount - 0.5) * 2.0
            np.multiply(frame, brightness, out=self._transition_work_float)
            self._transition_work_float[:, 3] = frame[:, 3]
        else:
            np.multiply(self._transition_source_float, 1.0 - amount, out=self._transition_work_float)
            np.multiply(self._transition_target_float, amount, out=self._transition_target_float)
            np.add(
                self._transition_work_float,
                self._transition_target_float,
                out=self._transition_work_float,
            )
        np.clip(self._transition_work_float, 0.0, 255.0, out=self._transition_work_float)
        np.copyto(self._transition_output, self._transition_work_float, casting="unsafe")
        controller.set_pixels(self._transition_output)
        if flush:
            controller.flush()
        if amount >= 1.0:
            self.cancel_transition()

    def _apply_wipe_transition(self, target: Any, amount: float) -> None:
        assert self._transition_source is not None
        assert self._transition_output is not None
        if amount <= 0.0:
            np.copyto(self._transition_output, self._transition_source)
            return
        if amount >= 1.0:
            np.copyto(self._transition_output, target)
            return

        length = target.shape[0]
        feather = max(1, min(8, round(length * 0.02)))
        boundary = amount * length
        feather_start = boundary - feather / 2.0
        feather_end = boundary + feather / 2.0
        blend_start = max(0, min(length, floor(feather_start - 0.5) + 1))
        blend_end = max(
            blend_start,
            min(length, ceil(feather_end - 0.5)),
        )

        np.copyto(self._transition_output, self._transition_source)
        np.copyto(self._transition_output[:blend_start], target[:blend_start])
        for pixel in range(blend_start, blend_end):
            incoming_weight = min(
                1.0,
                max(0.0, (feather_end - (pixel + 0.5)) / feather),
            )
            outgoing_weight = 1.0 - incoming_weight
            for channel in range(target.shape[1]):
                self._transition_output[pixel, channel] = int(
                    self._transition_source[pixel, channel] * outgoing_weight
                    + target[pixel, channel] * incoming_weight
                )

    def fresh_animation(self, name: str) -> Animation | None:
        index = self.index_of(name)
        if index is None:
            return None
        return self.animations[index].fresh_animation()

    def current_index(self) -> int:
        return self.index

    def automatic_indices(self) -> list[int]:
        return [index for index, entry in enumerate(self.animations) if entry.automatic]

    def index_of(self, name: str) -> int | None:
        for index, entry in enumerate(self.animations):
            if entry.animation.name == name:
                return index
        return None

    def name_at(self, index: int) -> str | None:
        if 0 <= index < len(self.animations):
            return self.animations[index].animation.name
        return None


def _validate_transition_time(now_s: float | None) -> None:
    if now_s is not None and not isfinite(now_s):
        raise ValueError("now_s must be finite")
