"""Export controlled Python and native-C golden frames for Rust effects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lumistripe.animation.catalog import party_catalog
from lumistripe.audio import AudioFrame
from lumistripe.color import Rgba
from lumistripe.stripe import Stripe

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "rust/crates/lumistripe-engine-effects/golden_frames.json"
PIXEL_COUNT = 64
BRIGHTNESS = 0.73
DELTA_NS = 20_000_000

CASES: dict[str, dict[str, object]] = {
    "hard_beat": {"decay": 42, "duty_cycle": 44, "intensity": 91},
    "confetti": {
        "count": 5,
        "spread": 67,
        "fade_time": 58,
        "intensity": 91,
        "palette": "rainbow",
    },
    "bass_drop": {"speed": 56, "decay": 48, "intensity": 90, "palette": "neon"},
    "beat_explosion": {"speed": 55, "width": 34, "intensity": 90, "palette": "neon", "beat_sensitivity": 88},
    "beat_ripple": {"speed": 54, "width": 32, "decay": 53, "intensity": 90, "palette": "neon"},
    "beat_tunnel": {"speed": 52, "width": 33, "intensity": 90, "palette": "neon", "beat_sensitivity": 88},
    "beat_wave": {"speed": 56, "width": 32, "intensity": 90, "palette": "neon", "beat_sensitivity": 88},
    "club_flash": {"flash_rate": 58, "duty_cycle": 31, "beat_sensitivity": 91, "intensity": 88, "palette": "neon"},
    "color_burst": {"speed": 58, "spread": 64, "decay": 48, "intensity": 90, "palette": "rainbow"},
    "drop_explosion": {"speed": 57, "decay": 48, "duty_cycle": 34, "intensity": 90, "music_reactivity": 91},
    "drop_wave": {"speed": 55, "width": 33, "intensity": 90, "palette": "neon"},
    "shockwave": {"speed": 55, "width": 35, "intensity": 90, "palette": "neon", "beat_sensitivity": 95},
    "center_burst": {"speed": 61, "width": 34, "intensity": 88, "palette": "neon", "beat_sensitivity": 90},
    "mirror_flash": {"decay": 57, "width": 42, "intensity": 89, "palette": "neon", "beat_sensitivity": 92},
    "spectrum_flash": {"sensitivity": 56, "width": 36, "decay": 48, "intensity": 90, "palette": "neon"},
    "electric_storm": {"count": 5, "decay": 51, "flash_rate": 57, "intensity": 90, "palette": "rainbow"},
    "firework_burst": {"count": 4, "spread": 63, "fade_time": 56, "intensity": 90, "palette": "neon"},
    "lightning_strike": {"width": 33, "spread": 62, "decay": 48, "intensity": 90, "palette": "neon"},
    "pixel_explosion": {"count": 5, "spread": 64, "decay": 48, "intensity": 90, "palette": "rainbow"},
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


def audio_frame(value: dict[str, object] | None, index: int) -> AudioFrame | None:
    if value is None:
        return None
    return AudioFrame(
        rms=float(value["rms"]),
        bands=tuple(float(band) for band in value["bands"]),  # type: ignore[arg-type]
        beat=bool(value["beat"]),
        beat_strength=float(value["beat_strength"]),
        sequence=index + 1,
        timestamp=index * DELTA_NS / 1_000_000_000,
        fresh=True,
    )


def render_case(name: str, settings: dict[str, object], variant: str) -> dict[str, Any]:
    definition = next(item for item in party_catalog() if item.name == name)
    effect = definition.create(settings)
    if hasattr(effect, "_rng"):
        effect._rng.seed(12_345)
    stripe = Stripe(PIXEL_COUNT)
    frames: list[str] = []
    for index, wire_audio in enumerate(AUDIO):
        if name == "beat_ripple" and variant == "python":
            stripe.fill(Rgba(0, 0, 0, 0.0))
        audio = audio_frame(wire_audio, index)
        configured_audio = None if audio is None else effect.effective_audio(audio)
        frame = effect.effective_frame(index)
        if variant == "native":
            rendered = effect._render_native(
                frame,
                stripe.pixels(),
                configured_audio,
                BRIGHTNESS,
            )
            if not rendered:
                raise RuntimeError(f"native renderer is unavailable for {name}")
        elif configured_audio is None:
            effect.tick(frame, effect.output_controller(stripe, BRIGHTNESS))
        else:
            effect.tick_audio(
                frame,
                effect.output_controller(stripe, BRIGHTNESS),
                configured_audio,
            )
        frames.append(stripe.pixels().tobytes().hex())
    return {
        "name": name,
        "settings": settings,
        "pixel_count": PIXEL_COUNT,
        "brightness": BRIGHTNESS,
        "delta_ns": DELTA_NS,
        "audio": list(AUDIO),
        "frames_hex": frames,
        "variant": variant,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    cases = [
        render_case(name, settings, variant)
        for name, settings in CASES.items()
        for variant in ("python", "native")
    ]
    generated = json.dumps({"schema_version": 1, "cases": cases}, indent=2) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text() != generated:
            raise SystemExit(f"{OUTPUT.relative_to(ROOT)} is stale; rerun this script")
        return
    OUTPUT.write_text(generated)


if __name__ == "__main__":
    main()
