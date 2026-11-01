"""Export controlled Python and native-C golden frames for Rust renderers."""

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
OUTPUT = ROOT / "rust/crates/lumistripe-engine-animation-state/golden_frames.json"
PIXEL_COUNT = 64
BRIGHTNESS = 0.73
DELTA_NS = 20_000_000

CASES: dict[str, dict[str, object]] = {
    "rainbow": {"palette": "neon", "color_spread": 70},
    "rainbow_cycle": {"palette": "fire", "speed": 65},
    "theater_chase": {
        "palette": "custom",
        "custom_color": "#12ABCD",
        "count": 4,
    },
    "strobe": {"palette": "neon", "flash_rate": 73, "duty_cycle": 42},
    "pulse": {
        "palette": "fire",
        "pulse_curve": "smoothstep",
        "minimum_intensity": 17,
        "maximum_intensity": 88,
        "music_reactivity": 83,
    },
    "police": {"pattern": "double", "speed": 72, "duty_cycle": 44},
    "bpm": {"palette": "neon", "decay": 37, "beat_sensitivity": 80},
    "wave": {"palette": "neon", "frequency": 64},
    "sinelon": {"palette": "rainbow", "fade_time": 61},
    "bouncing_ball": {
        "direction": "bounce",
        "speed": 61,
        "particle_size": 42,
        "palette": "neon",
    },
    "color_wipe": {
        "direction": "bounce",
        "speed": 58,
        "tail_length": 15,
        "music_reactivity": 85,
        "palette": "rainbow",
    },
    "juggle": {
        "count": 6,
        "speed": 57,
        "particle_size": 38,
        "palette": "neon",
    },
    "comet": {
        "speed": 63,
        "tail_length": 14,
        "intensity": 88,
        "direction": "bounce",
        "palette": "neon",
        "music_reactivity": 82,
    },
    "aurora": {"frequency": 63, "palette": "rainbow"},
    "fire": {
        "cooling": 57,
        "sparking": 48,
        "intensity": 91,
        "music_reactivity": 84,
    },
    "twinkle": {
        "density": 51,
        "fade_time": 62,
        "particle_size": 40,
        "intensity": 87,
        "palette": "rainbow",
    },
    "rainbow_strobe": {
        "flash_rate": 72,
        "duty_cycle": 43,
        "intensity": 89,
        "color_spread": 67,
        "palette": "rainbow",
    },
    "laser_sweep": {
        "speed": 58,
        "width": 35,
        "direction": "bounce",
        "intensity": 94,
        "palette": "rainbow",
    },
    "disco_sparkle": {
        "density": 58,
        "fade_time": 64,
        "variation": 71,
        "intensity": 92,
        "palette": "rainbow",
    },
    "dual_comet": {
        "speed": 56,
        "tail_length": 13,
        "separation": 68,
        "intensity": 90,
        "palette": "rainbow",
    },
    "dance_floor": {"frequency": 63, "intensity": 87, "palette": "neon"},
    "red_rave_sweep": {
        "speed": 61,
        "width": 34,
        "duty_cycle": 74,
        "intensity": 88,
    },
    "red_rave_chase": {
        "speed": 56,
        "count": 4,
        "duty_cycle": 47,
        "intensity": 90,
    },
    "red_blackout_strobe": {
        "flash_rate": 70,
        "duty_cycle": 35,
        "intensity": 91,
    },
    "glow_rush": {
        "speed": 58,
        "width": 36,
        "frequency": 62,
        "intensity": 89,
        "palette": "neon",
    },
    "rave_pulse": {
        "pulse_curve": "smoothstep",
        "intensity": 86,
        "palette": "rainbow",
        "music_reactivity": 82,
    },
    "rave_scanner": {
        "speed": 55,
        "width": 34,
        "direction": "bounce",
        "intensity": 91,
        "palette": "neon",
    },
    "neon_confetti": {
        "count": 5,
        "spread": 63,
        "fade_time": 57,
        "intensity": 91,
        "palette": "neon",
    },
    "strobe_chase": {
        "count": 5,
        "speed": 62,
        "duty_cycle": 44,
        "intensity": 89,
        "palette": "neon",
    },
    "plasma_rave": {
        "speed": 57,
        "frequency": 63,
        "intensity": 91,
        "palette": "neon",
        "music_reactivity": 83,
    },
    "dual_laser": {
        "speed": 56,
        "width": 32,
        "separation": 58,
        "intensity": 90,
        "palette": "neon",
    },
    "disco_comet": {
        "speed": 57,
        "tail_length": 12,
        "direction": "bounce",
        "intensity": 88,
        "palette": "neon",
    },
    "neon_storm": {
        "count": 5,
        "speed": 59,
        "decay": 54,
        "intensity": 92,
        "palette": "rainbow",
    },
    "peak_mirror": {
        "sensitivity": 56,
        "decay": 48,
        "width": 37,
        "intensity": 90,
        "palette": "neon",
    },
    "shockwave": {
        "speed": 55,
        "width": 35,
        "intensity": 90,
        "palette": "neon",
        "beat_sensitivity": 95,
    },
    "center_burst": {
        "speed": 61,
        "width": 34,
        "intensity": 88,
        "palette": "neon",
        "beat_sensitivity": 90,
    },
    "mirror_flash": {
        "decay": 57,
        "width": 42,
        "intensity": 89,
        "palette": "neon",
        "beat_sensitivity": 92,
    },
    "spectrum_flash": {
        "sensitivity": 56,
        "width": 36,
        "decay": 48,
        "intensity": 90,
        "palette": "neon",
    },
    "club_flash": {
        "flash_rate": 58,
        "duty_cycle": 31,
        "beat_sensitivity": 91,
        "intensity": 88,
        "palette": "neon",
    },
    "color_burst": {
        "speed": 58,
        "spread": 64,
        "decay": 48,
        "intensity": 90,
        "palette": "rainbow",
    },
    "drop_explosion": {
        "speed": 57,
        "decay": 48,
        "duty_cycle": 34,
        "intensity": 90,
        "music_reactivity": 91,
    },
    "drop_wave": {
        "speed": 55,
        "width": 33,
        "intensity": 90,
        "palette": "neon",
    },
    "bass_drop": {
        "speed": 56,
        "decay": 48,
        "intensity": 90,
        "palette": "neon",
    },
    "beat_explosion": {
        "speed": 55,
        "width": 34,
        "intensity": 90,
        "palette": "neon",
        "beat_sensitivity": 88,
    },
    "beat_ripple": {
        "speed": 54,
        "width": 32,
        "decay": 53,
        "intensity": 90,
        "palette": "neon",
    },
    "beat_tunnel": {
        "speed": 52,
        "width": 33,
        "intensity": 90,
        "palette": "neon",
        "beat_sensitivity": 88,
    },
    "beat_wave": {
        "speed": 56,
        "width": 32,
        "intensity": 90,
        "palette": "neon",
        "beat_sensitivity": 88,
    },
    "hard_beat": {"decay": 42, "duty_cycle": 44, "intensity": 91},
    "confetti": {
        "count": 5,
        "spread": 67,
        "fade_time": 58,
        "intensity": 91,
        "palette": "rainbow",
    },
    "comet_storm": {
        "count": 5,
        "speed": 57,
        "tail_length": 12,
        "intensity": 88,
        "palette": "neon",
    },
    "electric_storm": {
        "count": 5,
        "decay": 51,
        "flash_rate": 57,
        "intensity": 90,
        "palette": "rainbow",
    },
    "firework_burst": {
        "count": 4,
        "spread": 63,
        "fade_time": 56,
        "intensity": 90,
        "palette": "neon",
    },
    "lightning_strike": {
        "width": 33,
        "spread": 62,
        "decay": 48,
        "intensity": 90,
        "palette": "neon",
    },
    "pixel_explosion": {
        "count": 5,
        "spread": 64,
        "decay": 48,
        "intensity": 90,
        "palette": "rainbow",
    },
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
    animation = definition.create(settings)
    if hasattr(animation, "_rng"):
        animation._rng.seed(12_345)
    stripe = Stripe(PIXEL_COUNT)
    frames: list[str] = []
    for index, wire_audio in enumerate(AUDIO):
        if name == "beat_ripple" and variant == "python":
            stripe.fill(Rgba(0, 0, 0, 0.0))
        audio = audio_frame(wire_audio, index)
        configured_audio = None if audio is None else animation.effective_audio(audio)
        frame = animation.effective_frame(index)
        if variant == "native":
            rendered = animation._render_native(
                frame,
                stripe.pixels(),
                configured_audio,
                BRIGHTNESS,
            )
            if not rendered:
                raise RuntimeError(f"native renderer is unavailable for {name}")
        elif configured_audio is None:
            animation.tick(frame, animation.output_controller(stripe, BRIGHTNESS))
        else:
            animation.tick_audio(
                frame,
                animation.output_controller(stripe, BRIGHTNESS),
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
