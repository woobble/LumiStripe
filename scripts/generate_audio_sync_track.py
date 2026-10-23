"""Generate a repeatable four-on-the-floor test track for LumiStripe."""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="destination WAV path")
    parser.add_argument("--bpm", type=float, default=120.0)
    parser.add_argument("--bars", type=int, default=32)
    parser.add_argument("--sample-rate", type=int, default=48_000)
    args = parser.parse_args()

    samples = make_track(args.bpm, args.bars, args.sample_rate)
    pcm = np.round(samples * 32767.0).astype("<i2")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.output), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(args.sample_rate)
        output.writeframes(pcm.tobytes())
    print(
        f"Wrote {args.output}: {args.bpm:g} BPM, {args.bars} bars, "
        f"{samples.size / args.sample_rate:.1f} seconds"
    )


if __name__ == "__main__":
    main()
