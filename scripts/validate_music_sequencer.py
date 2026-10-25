"""Validate generated music structure and sequencer playback offline."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import wave
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from lumistripe.animation import AnimationPlayer
from lumistripe.animation.catalog import animation_catalog, dynamic_sequence_eligible
from lumistripe.audio import FFT_HOP_SIZE, AudioSnapshot, AudioState
from lumistripe.effects.layers import EffectSchedulerConfig
from lumistripe.playback import AnimationSequence, AnimationSequenceStep
from lumistripe.playback.config import (
    DynamicSelectorConfig,
    PlaybackConfig,
    PlaybackMode,
    SwitchQuantization,
)
from lumistripe.playback.engine import PlaybackEngine
from lumistripe.stripe import Stripe

PHASE_TOLERANCE_S = 2.0
BEAT_MEDIAN_TOLERANCE_MS = 75.0
TRACE_FIELDS = (
    "scenario",
    "time_s",
    "phase",
    "phase_confidence",
    "buildup_progress",
    "audio_energy",
    "music_active",
    "drop_event",
    "bpm",
    "beat_clock_confidence",
    "beat_index",
    "beat_in_bar",
    "bar_index",
    "bar_in_phrase",
    "phrase_index",
    "active_animation",
    "pending_animation",
    "pending_switch_in_s",
    "sequence_step_index",
    "sequence_animation",
    "sequence_remaining_bars",
    "sequence_waiting_for_clock",
    "sequence_next_transition_in_s",
    "late_ticks_dropped",
    "late_ticks_coalesced",
    "beat_lateness_p95_ms",
    "drop_accent_status",
    "drop_accent_reason",
)


@dataclass(frozen=True, slots=True)
class AudioObservation:
    time_s: float
    snapshot: AudioSnapshot


def read_audio_observations(path: Path) -> tuple[list[AudioObservation], int]:
    """Decode mono PCM WAV into snapshots using the installed native DSP."""

    with wave.open(str(path), "rb") as audio_file:
        channels = audio_file.getnchannels()
        width = audio_file.getsampwidth()
        sample_rate = audio_file.getframerate()
        if channels != 1 or width != 2:
            raise ValueError("validation audio must be mono, 16-bit PCM WAV")
        pcm = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype="<i2")

    try:
        audio_state = AudioState(sample_rate=float(sample_rate))
    except RuntimeError as error:
        raise RuntimeError(
            "the validation runner requires LumiStripe's native audio extension; "
            "install/build the audio profile first"
        ) from error

    samples = pcm.astype(np.float32) / 32768.0
    observations: list[AudioObservation] = []
    previous_sequence = 0
    for start in range(0, samples.size, FFT_HOP_SIZE):
        block = samples[start : start + FFT_HOP_SIZE]
        if block.size == 0:
            continue
        time_s = min((start + block.size) / sample_rate, samples.size / sample_rate)
        audio_state.feed_samples(block, captured_at=time_s)
        frame = audio_state.frame()
        fresh = frame.sequence > previous_sequence
        previous_sequence = max(previous_sequence, frame.sequence)
        frame = replace(frame, fresh=fresh)
        snapshot = AudioSnapshot.from_parts(
            frame,
            audio_state.music_features(),
            beat_events=audio_state.take_beat_events(),
            beat_event_stream=True,
        )
        observations.append(AudioObservation(time_s, snapshot))
    return observations, sample_rate


def match_timestamps(
    expected: list[float], observed: list[float], tolerance_s: float
) -> dict[str, Any]:
    """Pair timestamps one-to-one and return timing errors and unmatched items."""

    unused = set(range(len(observed)))
    matched: list[dict[str, float | int]] = []
    missed: list[int] = []
    for expected_index, expected_at in enumerate(expected):
        candidate = min(
            unused,
            key=lambda index: abs(observed[index] - expected_at),
            default=None,
        )
        if candidate is None or abs(observed[candidate] - expected_at) > tolerance_s:
            missed.append(expected_index)
            continue
        unused.remove(candidate)
        matched.append(
            {
                "expected_index": expected_index,
                "expected_time_s": expected_at,
                "observed_time_s": observed[candidate],
                "error_ms": (observed[candidate] - expected_at) * 1000.0,
            }
        )
    errors = [abs(float(item["error_ms"])) for item in matched]
    return {
        "matched": matched,
        "missed_expected_indices": missed,
        "unmatched_observed_indices": sorted(unused),
        "median_absolute_error_ms": statistics.median(errors) if errors else None,
        "p95_absolute_error_ms": (
            float(np.percentile(errors, 95)) if errors else None
        ),
    }


def _phase_name(state: object) -> str:
    return str(getattr(getattr(state, "phase", "idle"), "value", "idle"))


def score_structure(
    reference: dict[str, Any], trace: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compare phase and drop edges with labeled reference boundaries."""

    expected_phase = {
        "silence": "idle",
        "groove": "groove",
        "breakdown": "breakdown",
        "buildup": "buildup",
        "drop": "drop",
        "high_energy": "groove",
    }
    expected = [
        section
        for section in reference["sections"]
        if section["name"] in expected_phase
    ]
    boundaries: list[dict[str, Any]] = []
    cursor = 0
    for section in expected:
        boundary = float(section["start_time_s"])
        wanted = expected_phase[str(section["name"])]
        found_index = next(
            (
                index
                for index in range(cursor, len(trace))
                if float(trace[index]["time_s"]) >= boundary - PHASE_TOLERANCE_S
                and (
                    bool(trace[index]["drop_event"])
                    if wanted == "drop"
                    else trace[index]["phase"] == wanted
                    and (index == 0 or trace[index - 1]["phase"] != wanted)
                )
            ),
            None,
        )
        if found_index is None:
            boundaries.append(
                {
                    "section": section["name"],
                    "expected_time_s": boundary,
                    "detected_time_s": None,
                    "error_s": None,
                    "status": "missed",
                }
            )
            continue
        detected = float(trace[found_index]["time_s"])
        error = detected - boundary
        boundaries.append(
            {
                "section": section["name"],
                "expected_time_s": boundary,
                "detected_time_s": detected,
                "error_s": error,
                "status": "pass" if abs(error) <= PHASE_TOLERANCE_S else "late-or-early",
            }
        )
        cursor = found_index + 1

    drop_times = [float(row["time_s"]) for row in trace if row["drop_event"]]
    drop_reference = [float(item["time_s"]) for item in reference["drop_events"]]
    drop_match = match_timestamps(drop_reference, drop_times, PHASE_TOLERANCE_S)
    false_drop_indices = drop_match["unmatched_observed_indices"]
    boundary_times = [float(section["start_time_s"]) for section in reference["sections"]]
    false_phase_rows: list[dict[str, Any]] = []
    for row in trace:
        time_s = float(row["time_s"])
        if any(abs(time_s - boundary) <= PHASE_TOLERANCE_S for boundary in boundary_times):
            continue
        expected_name = next(
            (
                str(section["name"])
                for section in reference["sections"]
                if float(section["start_time_s"]) <= time_s < float(section["end_time_s"])
            ),
            None,
        )
        if expected_name is None:
            continue
        expected_phase_name = expected_phase.get(expected_name)
        if expected_name == "drop":
            elapsed_in_drop = time_s - next(
                float(section["start_time_s"])
                for section in reference["sections"]
                if section["name"] == "drop"
            )
            if elapsed_in_drop >= 1.0:
                expected_phase_name = "groove"
        if expected_phase_name is not None and row["phase"] != expected_phase_name:
            false_phase_rows.append(
                {
                    "time_s": time_s,
                    "expected_phase": expected_phase_name,
                    "observed_phase": row["phase"],
                }
            )
    missed_boundary = any(item["status"] != "pass" for item in boundaries)
    return {
        "status": "pass"
        if not missed_boundary and not false_drop_indices and not false_phase_rows
        else "fail",
        "boundary_tolerance_s": PHASE_TOLERANCE_S,
        "boundaries": boundaries,
        "drop_event_match": drop_match,
        "false_drop_times_s": [drop_times[index] for index in false_drop_indices],
        "false_phase_sample_count": len(false_phase_rows),
        "false_phase_samples": false_phase_rows[:20],
        "phase_transitions": [
            {
                "time_s": float(row["time_s"]),
                "phase": row["phase"],
                "confidence": row["phase_confidence"],
            }
            for index, row in enumerate(trace)
            if index == 0 or row["phase"] != trace[index - 1]["phase"]
        ],
    }


def _scenarios(player: AnimationPlayer) -> list[tuple[str, SwitchQuantization, AnimationSequence | None]]:
    definitions = animation_catalog(player)
    eligible = [definition for definition in definitions if dynamic_sequence_eligible(definition)]
    if len(eligible) < 2:
        raise RuntimeError("the party catalog needs at least two sequence-eligible animations")
    repeating = AnimationSequence(
        "validation-repeat",
        "Validation repeating sequence",
        (
            AnimationSequenceStep(eligible[0].name, 2),
            AnimationSequenceStep(eligible[1].name, 2),
        ),
        True,
    )
    once = AnimationSequence(
        "validation-once",
        "Validation one-shot sequence",
        repeating.steps,
        False,
    )
    return [
        (f"automatic-{mode.value}", mode, None)
        for mode in SwitchQuantization
    ] + [
        ("custom-repeating-minus-85ms", SwitchQuantization.OFF, repeating),
        ("custom-one-shot-minus-85ms", SwitchQuantization.OFF, once),
    ]


def run_scenario(
    name: str,
    quantization: SwitchQuantization,
    sequence: AnimationSequence | None,
    observations: list[AudioObservation],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    player = AnimationPlayer.party()
    config = PlaybackConfig(
        mode=PlaybackMode.DYNAMIC,
        switch_quantization=quantization,
        animation_sequence=sequence,
        dynamic=DynamicSelectorConfig(seed=73),
        effects=EffectSchedulerConfig(seed=73),
        transition_duration_s=0.3,
    )
    engine = PlaybackEngine(player, config)
    if sequence is not None:
        engine.set_sync_offset(-0.085)
    stripe = Stripe(60)
    trace: list[dict[str, Any]] = []
    animation_changes: list[dict[str, Any]] = []
    previous_animation = player.name_at(player.current_index())
    previous_beat_index = 0
    estimated_beats: dict[int, float] = {}

    for observation in observations:
        before_quantized = engine.quantized_switch_diagnostics(now_s=observation.time_s)
        before_sequence = engine.sequence_playback_state
        engine.step(
            stripe,
            snapshot=observation.snapshot,
            now_s=observation.time_s,
        )
        current_animation = player.name_at(player.current_index())
        clock = engine.beat_clock_state
        if clock.beat_index > previous_beat_index and clock.last_beat_at is not None:
            period = 60.0 / clock.bpm if clock.bpm > 0.0 else 0.0
            for beat_index in range(previous_beat_index + 1, clock.beat_index + 1):
                estimated_beats[beat_index] = clock.last_beat_at - (
                    clock.beat_index - beat_index
                ) * period
        previous_beat_index = max(previous_beat_index, clock.beat_index)

        sequence_state = engine.sequence_playback_state
        if current_animation != previous_animation:
            animation_changes.append(
                {
                    "time_s": observation.time_s,
                    "from": previous_animation,
                    "to": current_animation,
                    "scheduled_time_s": (
                        observation.time_s + before_quantized.next_switch_in_s
                        if before_quantized.next_switch_in_s is not None
                        else before_sequence.next_transition_at_s
                    ),
                }
            )
            previous_animation = current_animation
        scheduler = engine.effect_diagnostics(now_s=observation.time_s)
        timeline = engine.musical_timeline_state
        trace.append(
            {
                "scenario": name,
                "time_s": observation.time_s,
                "phase": _phase_name(engine.music_structure_state),
                "phase_confidence": engine.music_structure_state.confidence,
                "buildup_progress": engine.music_structure_state.buildup_progress,
                "audio_energy": observation.snapshot.features.energy,
                "music_active": engine.music_active,
                "drop_event": engine.music_structure_state.drop_event,
                "bpm": clock.bpm,
                "beat_clock_confidence": clock.confidence,
                "beat_index": timeline.beat_index,
                "beat_in_bar": timeline.beat_in_bar,
                "bar_index": timeline.bar_index,
                "bar_in_phrase": timeline.bar_in_phrase,
                "phrase_index": timeline.phrase_index,
                "active_animation": current_animation,
                "pending_animation": before_quantized.pending_animation,
                "pending_switch_in_s": before_quantized.next_switch_in_s,
                "sequence_step_index": sequence_state.current_step_index,
                "sequence_animation": sequence_state.animation_name,
                "sequence_remaining_bars": sequence_state.remaining_bars,
                "sequence_waiting_for_clock": sequence_state.waiting_for_clock,
                "sequence_next_transition_in_s": (
                    None
                    if sequence_state.next_transition_at_s is None
                    else max(0.0, sequence_state.next_transition_at_s - observation.time_s)
                ),
                "late_ticks_dropped": scheduler.late_ticks_dropped,
                "late_ticks_coalesced": scheduler.late_ticks_coalesced,
                "beat_lateness_p95_ms": scheduler.beat_lateness_p95_ms,
                "drop_accent_status": scheduler.drop_accent.status.value,
                "drop_accent_reason": scheduler.drop_accent.reason,
            }
        )
    return trace, {
        "scenario": name,
        "quantization": quantization.value,
        "sequence_id": None if sequence is None else sequence.id,
        "animation_changes": animation_changes,
        "estimated_beat_times_s": [estimated_beats[index] for index in sorted(estimated_beats)],
        "scheduler": {
            "late_ticks_dropped": trace[-1]["late_ticks_dropped"] if trace else 0,
            "late_ticks_coalesced": trace[-1]["late_ticks_coalesced"] if trace else 0,
            "beat_lateness_p95_ms": trace[-1]["beat_lateness_p95_ms"] if trace else 0.0,
            "drop_accent_status": trace[-1]["drop_accent_status"] if trace else "idle",
            "drop_accent_reason": trace[-1]["drop_accent_reason"] if trace else None,
        },
    }


def validate(
    audio_path: Path,
    timeline_path: Path,
    report_path: Path,
    trace_path: Path,
) -> dict[str, Any]:
    reference = json.loads(timeline_path.read_text(encoding="utf-8"))
    if reference.get("schema_version") != 1 or reference.get("kind") != "lumistripe-structure-audio-reference":
        raise ValueError("unsupported structure-audio reference timeline")
    observations, sample_rate = read_audio_observations(audio_path)
    if sample_rate != int(reference["sample_rate"]):
        raise ValueError("WAV sample rate does not match the reference timeline")
    sequence_frame_tolerance_s = FFT_HOP_SIZE / sample_rate
    first_reference_beat = int(reference["beat_events"][0]["beat_index"])
    player = AnimationPlayer.party()
    summaries: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    for scenario_name, mode, sequence in _scenarios(player):
        trace, summary = run_scenario(scenario_name, mode, sequence, observations)
        structure_result = score_structure(reference, trace)
        summary["structure"] = structure_result
        observed_beats = summary.pop("estimated_beat_times_s")
        expected_beats = [
            float(event["time_s"])
            for event in reference["beat_grid"]
            if int(event["beat_index"]) >= first_reference_beat
        ]
        beat_result = match_timestamps(
            expected_beats,
            observed_beats,
            tolerance_s=BEAT_MEDIAN_TOLERANCE_MS / 1000.0,
        )
        beat_median = beat_result["median_absolute_error_ms"]
        beat_result["status"] = (
            "unverified"
            if beat_median is None
            else "pass"
            if beat_median <= BEAT_MEDIAN_TOLERANCE_MS
            else "fail"
        )
        beat_result["median_tolerance_ms"] = BEAT_MEDIAN_TOLERANCE_MS
        summary["beat_clock"] = beat_result
        switch_errors = [
            abs(float(item["time_s"]) - float(item["scheduled_time_s"]))
            for item in summary["animation_changes"]
            if item["scheduled_time_s"] is not None
        ]
        summary["automatic_switch_timing"] = {
            "status": (
                "not-applicable"
                if sequence is not None
                else "unverified"
                if not summary["animation_changes"]
                or (mode is not SwitchQuantization.OFF and not switch_errors)
                else "pass"
                if mode is SwitchQuantization.OFF
                or max(switch_errors) <= sequence_frame_tolerance_s
                else "fail"
            ),
            "frame_tolerance_s": sequence_frame_tolerance_s,
            "scheduled_transition_errors_s": switch_errors,
            "transition_count": len(summary["animation_changes"]),
        }
        if sequence is not None:
            changed = summary["animation_changes"]
            scheduled_errors = [
                abs(float(item["time_s"]) - float(item["scheduled_time_s"]))
                for item in changed
                if item["scheduled_time_s"] is not None
            ]
            summary["sequence_timing"] = {
                "status": (
                    "unverified"
                    if not scheduled_errors
                    else "pass"
                    if max(scheduled_errors) <= sequence_frame_tolerance_s
                    else "fail"
                ),
                "frame_tolerance_s": sequence_frame_tolerance_s,
                "scheduled_transition_errors_s": scheduled_errors,
                "transition_count": len(changed),
            }
        else:
            summary["sequence_timing"] = {"status": "not-applicable"}
        summaries.append(summary)
        trace_rows.extend(trace)

    report: dict[str, Any] = {
        "schema_version": 1,
        "audio_file": str(audio_path),
        "timeline_file": str(timeline_path),
        "sample_rate": sample_rate,
        "duration_s": observations[-1].time_s if observations else 0.0,
        "tolerances": {
            "phase_and_drop_boundary_s": PHASE_TOLERANCE_S,
            "beat_median_absolute_error_ms": BEAT_MEDIAN_TOLERANCE_MS,
            "sequence_transition_frame_s": sequence_frame_tolerance_s,
        },
        "estimated_bar_alignment_note": (
            "Beat/bar/phrase positions are relative to the first confident observed beat; "
            "this report does not validate actual musical downbeat alignment."
        ),
        "hardware_sync_status": "unverified",
        "scenarios": summaries,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", newline="", encoding="utf-8") as trace_file:
        writer = csv.DictWriter(trace_file, fieldnames=TRACE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trace_rows)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="structured reference WAV")
    parser.add_argument("timeline", type=Path, help="timeline JSON written by the generator")
    parser.add_argument("--report", type=Path, default=Path("music-validation-report.json"))
    parser.add_argument("--trace", type=Path, default=Path("music-validation-trace.csv"))
    args = parser.parse_args()
    report = validate(args.audio, args.timeline, args.report, args.trace)
    print(f"Wrote report: {args.report}")
    print(f"Wrote trace: {args.trace}")
    for scenario in report["scenarios"]:
        print(
            f"{scenario['scenario']}: structure={scenario['structure']['status']}, "
            f"beat={scenario['beat_clock']['status']}, "
            f"switch={scenario['automatic_switch_timing']['status']}, "
            f"sequence={scenario['sequence_timing']['status']}"
        )


if __name__ == "__main__":
    main()
