"""Generate a repeatable four-on-the-floor test track for LumiStripe."""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

STRUCTURE_SECTIONS = (
    ("silence", 2),
    ("groove", 8),
    ("breakdown", 4),
    ("buildup", 8),
    ("drop", 2),
    ("high_energy", 8),
)


def make_track(bpm: float, bars: int, sample_rate: int) -> np.ndarray:
    if not 60.0 <= bpm <= 200.0:
        raise ValueError("BPM must be between 60 and 200")
    if bars < 1:
        raise ValueError("bars must be positive")
    if sample_rate < 8_000:
        raise ValueError("sample rate must be at least 8000 Hz")

    beat_period = 60.0 / bpm
    beat_count = bars * 4
    output = np.zeros(round(beat_count * beat_period * sample_rate), dtype=np.float64)
    rng = np.random.default_rng(20261023)
    kick_duration_s = 0.14
    kick_samples = int(kick_duration_s * sample_rate)
    kick_time = np.arange(kick_samples, dtype=np.float64) / sample_rate
    kick_envelope = np.exp(-kick_time * 21.0)
    kick_phase = 2.0 * np.pi * (
        92.0 * kick_time - 0.5 * (92.0 - 48.0) * kick_time**2 / kick_duration_s
    )
    kick = 0.92 * kick_envelope * np.sin(kick_phase)

    hat_duration_s = 0.028
    hat_samples = int(hat_duration_s * sample_rate)
    hat_time = np.arange(hat_samples, dtype=np.float64) / sample_rate
    hats = rng.normal(0.0, 0.055, hat_samples) * np.exp(-hat_time * 95.0)
    for beat in range(beat_count):
        start = round(beat * beat_period * sample_rate)
        end = min(output.size, start + kick.size)
        output[start:end] += kick[: end - start]
        hat_start = round((beat + 0.5) * beat_period * sample_rate)
        if hat_start < output.size:
            hat_end = min(output.size, hat_start + hats.size)
            output[hat_start:hat_end] += hats[: hat_end - hat_start]

    return np.clip(output, -1.0, 1.0)


def make_structure_track(
    bpm: float, sample_rate: int
) -> tuple[np.ndarray, dict[str, object]]:
    """Generate a deterministic 32-bar structure track and its reference grid."""

    if not 60.0 <= bpm <= 200.0:
        raise ValueError("BPM must be between 60 and 200")
    if sample_rate < 8_000:
        raise ValueError("sample rate must be at least 8000 Hz")

    total_bars = sum(bars for _, bars in STRUCTURE_SECTIONS)
    beat_period = 60.0 / bpm
    bar_duration = beat_period * 4
    total_beats = total_bars * 4
    output = np.zeros(round(total_beats * beat_period * sample_rate), dtype=np.float64)
    rng = np.random.default_rng(20261025)

    kick_duration_s = 0.14
    kick_samples = int(kick_duration_s * sample_rate)
    kick_time = np.arange(kick_samples, dtype=np.float64) / sample_rate
    kick_envelope = np.exp(-kick_time * 21.0)
    kick_phase = 2.0 * np.pi * (
        92.0 * kick_time - 0.5 * (92.0 - 48.0) * kick_time**2 / kick_duration_s
    )
    kick = np.sin(kick_phase) * kick_envelope

    hat_duration_s = 0.028
    hat_samples = int(hat_duration_s * sample_rate)
    hat_time = np.arange(hat_samples, dtype=np.float64) / sample_rate
    hat = rng.normal(0.0, 1.0, hat_samples) * np.exp(-hat_time * 95.0)

    sections: list[dict[str, object]] = []
    beat_events: list[dict[str, float | int]] = []
    drop_events: list[dict[str, float | int]] = []
    start_bar = 1
    for name, section_bars in STRUCTURE_SECTIONS:
        end_bar_exclusive = start_bar + section_bars
        start_s = (start_bar - 1) * bar_duration
        end_s = (end_bar_exclusive - 1) * bar_duration
        sections.append(
            {
                "name": name,
                "start_bar": start_bar,
                "end_bar_exclusive": end_bar_exclusive,
                "start_time_s": start_s,
                "end_time_s": end_s,
                "duration_bars": section_bars,
            }
        )

        for bar in range(start_bar, end_bar_exclusive):
            for beat_in_bar in range(1, 5):
                beat_index = (bar - 1) * 4 + beat_in_bar
                time_s = (beat_index - 1) * beat_period
                start = round(time_s * sample_rate)
                if name == "silence":
                    continue

                if name == "groove":
                    kick_gain, hat_gain = 0.66, 0.07
                elif name == "breakdown":
                    # Keep a subdued pulse so Dynamic activity remains latched,
                    # while staying below the structure tracker's breakdown
                    # energy threshold.
                    kick_gain, hat_gain = (0.45, 0.12) if beat_in_bar in (1, 3) else (0.28, 0.12)
                elif name == "buildup":
                    progress = ((bar - start_bar) * 4 + beat_in_bar - 1) / max(
                        section_bars * 4 - 1, 1
                    )
                    kick_gain = 0.20 + 0.58 * progress
                    hat_gain = 0.035 + 0.11 * progress
                elif name == "drop":
                    kick_gain, hat_gain = 1.0, 0.20
                else:
                    kick_gain, hat_gain = 0.82, 0.14

                if kick_gain > 0.0:
                    end = min(output.size, start + kick.size)
                    output[start:end] += kick[: end - start] * kick_gain
                    beat_events.append(
                        {
                            "time_s": time_s,
                            "beat_index": beat_index,
                            "bar_index": bar,
                            "beat_in_bar": beat_in_bar,
                        }
                    )
                if name != "breakdown" or beat_in_bar in (1, 3):
                    end = min(output.size, start + hat.size)
                    output[start:end] += hat[: end - start] * hat_gain

                # Build-up subdivision density increases gradually. The final
                # section includes an explicit noisy crash at its first beat.
                if name == "buildup" and (bar - start_bar) * 4 + beat_in_bar >= section_bars * 2:
                    subdivision_s = time_s + beat_period / 2
                    subdivision = round(subdivision_s * sample_rate)
                    end = min(output.size, subdivision + hat.size)
                    output[subdivision:end] += hat[: end - subdivision] * hat_gain * 0.7
                if name == "drop" and bar == start_bar and beat_in_bar == 1:
                    crash_duration = 0.22
                    crash_size = min(int(crash_duration * sample_rate), output.size - start)
                    crash_noise = rng.normal(0.0, 0.20, crash_size)
                    crash_noise *= np.exp(-np.arange(crash_size) / (sample_rate * 0.075))
                    output[start : start + crash_size] += crash_noise
                    drop_events.append(
                        {
                            "time_s": time_s,
                            "beat_index": beat_index,
                            "bar_index": bar,
                            "beat_in_bar": beat_in_bar,
                        }
                    )
        start_bar = end_bar_exclusive

    beat_grid = [
        {
            "time_s": (beat_index - 1) * beat_period,
            "beat_index": beat_index,
            "bar_index": (beat_index - 1) // 4 + 1,
            "beat_in_bar": (beat_index - 1) % 4 + 1,
        }
        for beat_index in range(1, total_beats + 1)
    ]
    reference: dict[str, object] = {
        "schema_version": 1,
        "kind": "lumistripe-structure-audio-reference",
        "bpm": bpm,
        "sample_rate": sample_rate,
        "beats_per_bar": 4,
        "bars_per_phrase": 8,
        "total_bars": total_bars,
        "duration_s": output.size / sample_rate,
        "bar_positions": [
            {"bar_index": bar, "time_s": (bar - 1) * bar_duration}
            for bar in range(1, total_bars + 1)
        ],
        "beat_grid": beat_grid,
        "beat_events": beat_events,
        "drop_events": drop_events,
        "sections": sections,
        "estimated_bar_alignment": True,
    }
    return np.clip(output, -1.0, 1.0), reference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="destination WAV path")
    parser.add_argument(
        "--profile",
        choices=("sync", "structure"),
        default="sync",
        help="sync preserves the steady reference; structure writes a 32-bar progression",
    )
    parser.add_argument("--bpm", type=float, default=120.0)
    parser.add_argument("--bars", type=int, default=None)
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument(
        "--timeline",
        type=Path,
        help="reference JSON destination (default: <output>.timeline.json); structure profile only",
    )
    args = parser.parse_args()

    if args.profile == "structure":
        if args.bars is not None and args.bars != 32:
            parser.error("the structure profile uses a fixed 32-bar section layout")
        samples, reference = make_structure_track(args.bpm, args.sample_rate)
        bars_written = sum(bars for _, bars in STRUCTURE_SECTIONS)
        timeline_path = args.timeline or args.output.with_suffix(".timeline.json")
        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_path.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
    else:
        if args.timeline is not None:
            parser.error("--timeline is only available with --profile structure")
        bars_written = 32 if args.bars is None else args.bars
        samples = make_track(args.bpm, bars_written, args.sample_rate)
    pcm = np.round(samples * 32767.0).astype("<i2")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.output), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(args.sample_rate)
        output.writeframes(pcm.tobytes())
    print(
        f"Wrote {args.output}: {args.bpm:g} BPM, {bars_written} bars, "
        f"{samples.size / args.sample_rate:.1f} seconds"
    )


if __name__ == "__main__":
    main()
