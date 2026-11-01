"""Export Python reference frames for Rust multi-output scene layouts."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lumistripe.animation.catalog import party_catalog
from lumistripe.audio import AudioFrame
from lumistripe.controller import (
    CompositeController,
    Controller,
    ScaledMultiController,
    ZoneMappingController,
)
from lumistripe.stripe import Stripe

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "rust/crates/lumistripe-engine-runtime/golden_layouts.json"
DELTA_NS = 20_000_000
BRIGHTNESS = 0.73
ANIMATION = "rainbow"
SETTINGS: dict[str, object] = {
    "palette": "neon",
    "speed": 65,
    "color_spread": 70,
}
AUDIO: tuple[dict[str, object] | None, ...] = (
    None,
    {
        "rms": 0.44,
        "bands": [0.82, 0.64, 0.48, 0.31, 0.57, 0.26, 0.41, 0.19],
        "beat": True,
        "beat_strength": 0.91,
    },
    {
        "rms": 0.29,
        "bands": [0.62, 0.51, 0.71, 0.55, 0.36, 0.47, 0.25, 0.14],
        "beat": False,
        "beat_strength": 0.12,
    },
    {
        "rms": 0.67,
        "bands": [0.91, 0.76, 0.58, 0.63, 0.49, 0.52, 0.38, 0.28],
        "beat": True,
        "beat_strength": 0.78,
    },
)


@dataclass(frozen=True, slots=True)
class Layout:
    name: str
    layout: str
    pixel_counts: tuple[int, ...]
    zone_counts: tuple[int, ...]
    zone_patterns: tuple[str, ...]

    @property
    def logical_lengths(self) -> tuple[int, ...]:
        return tuple(
            (pixels + zones - 1) // zones
            for pixels, zones in zip(self.pixel_counts, self.zone_counts, strict=True)
        )

    @property
    def scene_pixel_count(self) -> int | None:
        if self.layout == "continuous":
            return sum(self.logical_lengths)
        if self.layout == "mirrored" and len(set(self.logical_lengths)) > 1:
            return max(self.logical_lengths)
        return None

    @property
    def scene_offsets(self) -> tuple[int, ...]:
        if self.layout != "continuous":
            return (0,) * len(self.pixel_counts)
        offsets: list[int] = []
        offset = 0
        for length in self.logical_lengths:
            offsets.append(offset)
            offset += length
        return tuple(offsets)

    @property
    def scene_resample(self) -> bool:
        return self.layout == "mirrored" and len(set(self.logical_lengths)) > 1


LAYOUTS = (
    Layout(
        name="independent_unequal_outputs",
        layout="independent",
        pixel_counts=(5, 3),
        zone_counts=(1, 1),
        zone_patterns=("repeat", "repeat"),
    ),
    Layout(
        name="continuous_unequal_zoned_outputs",
        layout="continuous",
        pixel_counts=(5, 4),
        zone_counts=(2, 1),
        zone_patterns=("alternate", "repeat"),
    ),
    Layout(
        name="mirrored_unequal_zoned_outputs",
        layout="mirrored",
        pixel_counts=(8, 3),
        zone_counts=(2, 1),
        zone_patterns=("alternate", "repeat"),
    ),
)


def _audio_frame(value: dict[str, object] | None, index: int) -> AudioFrame | None:
    if value is None:
        return None
    bands = value["bands"]
    assert isinstance(bands, list)
    return AudioFrame(
        rms=float(value["rms"]),
        bands=tuple(float(band) for band in bands),
        beat=bool(value["beat"]),
        beat_strength=float(value["beat_strength"]),
        sequence=index + 1,
        timestamp=index * DELTA_NS / 1_000_000_000,
        fresh=True,
    )


def _animation() -> Any:
    definition = next(item for item in party_catalog() if item.name == ANIMATION)
    animation = definition.create(SETTINGS)
    if hasattr(animation, "_rng"):
        animation._rng.seed(12_345)
    return animation


def _tick(animation: Any, controller: Controller, index: int) -> None:
    audio = _audio_frame(AUDIO[index], index)
    output = animation.output_controller(controller, BRIGHTNESS)
    frame = animation.effective_frame(index)
    if audio is None:
        animation.tick(frame, output)
    else:
        configured_audio = animation.effective_audio(audio)
        if configured_audio is None:
            animation.tick(frame, output)
        else:
            animation.tick_audio(frame, output, configured_audio)
    output.flush()


def _render_layout(layout: Layout) -> dict[str, object]:
    physical = [Stripe(pixel_count) for pixel_count in layout.pixel_counts]
    zoned = tuple(
        ZoneMappingController(stripe, zones, pattern)
        for stripe, zones, pattern in zip(
            physical, layout.zone_counts, layout.zone_patterns, strict=True
        )
    )
    shared_controller: Controller | None
    animations: tuple[Any, ...]
    if layout.layout == "continuous":
        shared_controller = CompositeController(zoned)
        animations = (_animation(),)
    elif layout.layout == "mirrored":
        shared_controller = ScaledMultiController(zoned)
        animations = (_animation(),)
    else:
        shared_controller = None
        animations = tuple(_animation() for _ in zoned)

    frames: list[dict[str, object]] = []
    for index, audio in enumerate(AUDIO):
        if shared_controller is None:
            for animation, controller in zip(animations, zoned, strict=True):
                _tick(animation, controller, index)
        else:
            _tick(animations[0], shared_controller, index)
        frames.append(
            {
                "timestamp_ns": index * DELTA_NS,
                "delta_ns": DELTA_NS,
                "audio": audio,
                "outputs_hex": [stripe.pixels().tobytes().hex() for stripe in physical],
            }
        )

    return {
        "name": layout.name,
        "layout": layout.layout,
        "animation": ANIMATION,
        "settings": SETTINGS,
        "brightness": BRIGHTNESS,
        "pixel_counts": layout.pixel_counts,
        "zone_counts": layout.zone_counts,
        "zone_patterns": layout.zone_patterns,
        "scene_pixel_count": layout.scene_pixel_count,
        "scene_offsets": layout.scene_offsets,
        "scene_resample": layout.scene_resample,
        "frames": frames,
    }


def golden_payload() -> dict[str, object]:
    return {"schema_version": 1, "cases": [_render_layout(layout) for layout in LAYOUTS]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = json.dumps(golden_payload(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text() != generated:
            print(f"{OUTPUT.relative_to(ROOT)} is stale; run this script to regenerate it")
            return 1
        return 0
    OUTPUT.write_text(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
