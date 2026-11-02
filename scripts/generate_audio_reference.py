"""Generate fixed PCM and reference traces from the Python/native-C audio path.

Run with the native audio extension installed:

    LUMISTRIPE_BUILD_EXTENSIONS=audio uv run python scripts/generate_audio_reference.py

The resulting PCM and JSON files are consumed by the Rust differential tests.
Regenerate them only when intentionally changing the Python/C reference
behavior, and review the fixture diff alongside that change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from lumistripe.audio import AudioConfig, AudioState
from lumistripe.audio.config import MicrophoneConditioning
from lumistripe.audio.dsp import _config_to_dict
from lumistripe.audio.structure import MusicStructureTracker
from numpy.typing import NDArray

SAMPLE_RATE = 44_100
FEED_SIZE = 512
OUTPUT = Path("rust/test-fixtures/audio-reference-v1")


def _waveforms() -> list[tuple[str, NDArray[np.float32], AudioConfig]]:
    def time_axis(seconds: float) -> NDArray[np.float64]:
        return np.arange(round(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE

    silence = np.zeros(round(2.0 * SAMPLE_RATE), dtype=np.float32)
    t = time_axis(2.0)
    bass_tone = (0.25 * np.sin(2.0 * np.pi * 80.0 * t)).astype(np.float32)
    treble_tone = (0.25 * np.sin(2.0 * np.pi * 8_000.0 * t)).astype(np.float32)

    sweep_t = time_axis(4.0)
    sweep_envelope = 0.01 + 0.54 * sweep_t / sweep_t[-1]
    amplitude_sweep = (
        sweep_envelope * np.sin(2.0 * np.pi * 55.0 * sweep_t)
        + 0.06 * np.sin(2.0 * np.pi * 1_600.0 * sweep_t)
    ).astype(np.float32)

    impulse = np.zeros(round(3.0 * SAMPLE_RATE), dtype=np.float32)
    for position in range(round(0.2 * SAMPLE_RATE), impulse.size, round(0.37 * SAMPLE_RATE)):
        impulse[position] = 0.95
        if position + 1 < impulse.size:
            impulse[position + 1] = -0.35

    noise_rng = np.random.default_rng(413)
    seeded_noise = (noise_rng.standard_normal(round(2.0 * SAMPLE_RATE)) * 0.14).astype(
        np.float32
    )

    clipped_t = time_axis(2.0)
    clipped_tone = np.clip(
        1.4 * np.sin(2.0 * np.pi * 440.0 * clipped_t), -1.0, 1.0
    ).astype(np.float32)

    beat_count = round(12.0 * SAMPLE_RATE)
    bass_beats = np.zeros(beat_count, dtype=np.float32)
    beat_period = SAMPLE_RATE // 2
    kick_length = round(0.14 * SAMPLE_RATE)
    kick_t = np.arange(kick_length, dtype=np.float64) / SAMPLE_RATE
    kick = (0.9 * np.exp(-kick_t / 0.045) * np.sin(2.0 * np.pi * 52.0 * kick_t)).astype(
        np.float32
    )
    hat = (0.025 * np.sin(2.0 * np.pi * 5_200.0 * t)).astype(np.float32)
    bass_beats[: hat.size] += hat
    for position in range(0, beat_count, beat_period):
        end = min(position + kick.size, beat_count)
        bass_beats[position:end] += kick[: end - position]
    bass_beats = np.clip(bass_beats, -1.0, 1.0)

    mic_t = time_axis(3.0)
    microphone = (
        0.16 * np.sin(2.0 * np.pi * 12.0 * mic_t)
        + 0.11 * np.sin(2.0 * np.pi * 94.0 * mic_t)
    ).astype(np.float32)
    mic_config = replace(
        AudioConfig(),
        microphone=MicrophoneConditioning(
            enabled=True,
            input_noise_floor=0.003,
            high_pass_hz=30.0,
        ),
    )

    return [
        ("silence_default", silence, AudioConfig()),
        ("bass_sine_raw", bass_tone, AudioConfig.raw()),
        ("treble_sine_raw", treble_tone, AudioConfig.raw()),
        ("amplitude_sweep_default", amplitude_sweep, AudioConfig()),
        ("impulses_default", impulse, AudioConfig()),
        ("seeded_noise_default", seeded_noise, AudioConfig()),
        ("clipped_tone_default", clipped_tone, AudioConfig()),
        ("bass_beats_default", bass_beats, AudioConfig()),
        ("microphone_gate_highpass", microphone, mic_config),
    ]


def _json_value(value: object) -> object:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    raise TypeError(f"cannot serialize reference value {type(value).__name__}")


def main() -> None:
    from lumistripe.audio import dsp

    if dsp._native_audio is None:
        raise SystemExit("the native _audio extension is required to generate references")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    fixtures: list[dict[str, object]] = []
    for name, samples, config in _waveforms():
        pcm = np.asarray(samples, dtype="<f4", order="C")
        pcm_bytes = pcm.tobytes()
        pcm_path = OUTPUT / f"{name}.f32le"
        pcm_path.write_bytes(pcm_bytes)

        state = AudioState(config, sample_rate=float(SAMPLE_RATE))
        native = dsp._native_audio.AudioProcessor(
            _config_to_dict(config), float(SAMPLE_RATE)
        )
        frames: list[dict[str, object]] = []
        structures: list[dict[str, object]] = []
        events: list[dict[str, object]] = []
        structure_tracker = MusicStructureTracker()
        previous_phase = "idle"
        previous_sequence = 0
        for offset in range(0, pcm.size, FEED_SIZE):
            block = pcm[offset : offset + FEED_SIZE]
            if block.size == 0:
                continue
            captured_at = (offset + block.size) / SAMPLE_RATE
            state.feed_samples(block, captured_at=captured_at)
            native.feed_samples(block)
            frame = state.frame()
            if frame.sequence > previous_sequence:
                previous_sequence = frame.sequence
                features = state.music_features()
                structure = structure_tracker.update(features, frame.timestamp)
                frames.append(
                    {
                        "frame": _json_value(asdict(frame)),
                        "features": _json_value(asdict(features)),
                    }
                )
                structures.append(
                    {
                        "phase": structure.phase.value,
                        "confidence": structure.confidence,
                        "buildup_progress": structure.buildup_progress,
                        "drop_event": structure.drop_event,
                        "phase_changed": structure.phase.value != previous_phase,
                    }
                )
                previous_phase = structure.phase.value
            for event in native.drain_beat_events():
                sample_position = int(event["sample_position"])
                events.append(
                    {
                        "sequence": int(event["sequence"]),
                        "sample_position": sample_position,
                        "timestamp": captured_at
                        + (sample_position - (offset + block.size)) / SAMPLE_RATE,
                        "strength": float(event["strength"]),
                    }
                )

        fixtures.append(
            {
                "name": name,
                "pcm_file": pcm_path.name,
                "pcm_sha256": hashlib.sha256(pcm_bytes).hexdigest(),
                "sample_rate": SAMPLE_RATE,
                "sample_count": int(pcm.size),
                "feed_size": FEED_SIZE,
                "config": _config_to_dict(config),
                "frames": frames,
                "structures": structures,
                "beat_events": events,
                "final_stats": _json_value(asdict(state.stats())),
            }
        )

    document = {
        "schema_version": 1,
        "reference": "Python AudioState backed by lumistripe.audio._audio/KissFFT",
        "tolerance": {
            "absolute": 0.00002,
            "relative": 0.0002,
            "beat_timestamps_seconds": 0.0117,
        },
        "fixtures": fixtures,
    }
    (OUTPUT / "reference.json").write_text(
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(fixtures)} reference cases to {OUTPUT}")


if __name__ == "__main__":
    main()
