from __future__ import annotations

import pytest
from lumistripe.audio import AudioFrame, AudioSnapshot, MusicFeatures
from lumistripe.playback.config import SwitchQuantization

from scripts.generate_audio_sync_track import make_structure_track, make_track
from scripts.validate_music_sequencer import (
    AudioObservation,
    match_timestamps,
    run_scenario,
    score_structure,
)


def test_legacy_sync_track_stays_deterministic_with_the_same_length() -> None:
    first = make_track(128.0, 32, 8_000)
    second = make_track(128.0, 32, 8_000)

    assert first.shape == (32 * 4 * 60 * 8_000 // 128,)
    assert (first == second).all()


def test_structure_profile_writes_expected_sections_and_audio_energy() -> None:
    samples, reference = make_structure_track(120.0, 8_000)
    sections = reference["sections"]
    assert [section["name"] for section in sections] == [
        "silence",
        "groove",
        "breakdown",
        "buildup",
        "drop",
        "high_energy",
    ]
    assert [section["duration_bars"] for section in sections] == [2, 8, 4, 8, 2, 8]
    assert len(reference["beat_grid"]) == 128
    assert len(reference["bar_positions"]) == 32
    assert len(reference["drop_events"]) == 1
    assert not samples[: 4 * 8_000].any()

    groove = samples[4 * 8_000 : 20 * 8_000]
    breakdown = samples[20 * 8_000 : 28 * 8_000]
    buildup = samples[28 * 8_000 : 44 * 8_000]
    drop = samples[44 * 8_000 : 48 * 8_000]
    buildup_first = buildup[: buildup.size // 4]
    buildup_last = buildup[-buildup.size // 4 :]
    assert breakdown.std() < groove.std()
    assert buildup_last.std() > buildup_first.std()
    assert drop.std() > breakdown.std()


def test_match_timestamps_reports_missed_and_unmatched_events() -> None:
    result = match_timestamps([1.0, 2.0], [0.99, 2.03, 3.0], tolerance_s=0.05)

    assert result["missed_expected_indices"] == []
    assert result["unmatched_observed_indices"] == [2]
    assert result["median_absolute_error_ms"] == pytest.approx(20.0)


def test_structure_score_matches_phase_and_drop_boundaries() -> None:
    reference = {
        "sections": [
            {"name": "silence", "start_time_s": 0.0, "end_time_s": 4.0},
            {"name": "groove", "start_time_s": 4.0, "end_time_s": 20.0},
            {"name": "breakdown", "start_time_s": 20.0, "end_time_s": 28.0},
            {"name": "buildup", "start_time_s": 28.0, "end_time_s": 44.0},
            {"name": "drop", "start_time_s": 44.0, "end_time_s": 48.0},
            {"name": "high_energy", "start_time_s": 48.0, "end_time_s": 64.0},
        ],
        "drop_events": [{"time_s": 44.0}],
    }
    trace = [
        {"time_s": 0.0, "phase": "idle", "phase_confidence": 0.8, "drop_event": False},
        {"time_s": 4.1, "phase": "groove", "phase_confidence": 0.8, "drop_event": False},
        {"time_s": 20.4, "phase": "breakdown", "phase_confidence": 0.8, "drop_event": False},
        {"time_s": 28.5, "phase": "buildup", "phase_confidence": 0.8, "drop_event": False},
        {"time_s": 44.2, "phase": "drop", "phase_confidence": 0.9, "drop_event": True},
        {"time_s": 48.4, "phase": "groove", "phase_confidence": 0.8, "drop_event": False},
    ]

    result = score_structure(reference, trace)

    assert result["status"] == "pass"
    assert all(boundary["status"] == "pass" for boundary in result["boundaries"])
    assert result["drop_event_match"]["matched"][0]["error_ms"] == pytest.approx(200.0)


def test_structure_score_flags_missed_drop_and_false_drop() -> None:
    reference = {
        "sections": [{"name": "drop", "start_time_s": 10.0, "end_time_s": 14.0}],
        "drop_events": [{"time_s": 10.0}],
    }
    trace = [
        {"time_s": 5.0, "phase": "groove", "phase_confidence": 0.8, "drop_event": True},
        {"time_s": 10.0, "phase": "groove", "phase_confidence": 0.8, "drop_event": False},
    ]

    result = score_structure(reference, trace)

    assert result["status"] == "fail"
    assert result["boundaries"][0]["status"] == "missed"
    assert result["false_drop_times_s"] == [5.0]


def test_structure_score_reports_false_phase_samples() -> None:
    reference = {
        "sections": [
            {"name": "groove", "start_time_s": 0.0, "end_time_s": 20.0},
        ],
        "drop_events": [],
    }
    trace = [
        {"time_s": 3.0, "phase": "groove", "phase_confidence": 0.8, "drop_event": False},
        {"time_s": 5.0, "phase": "breakdown", "phase_confidence": 0.8, "drop_event": False},
    ]

    result = score_structure(reference, trace)

    assert result["false_phase_sample_count"] == 1
    assert result["false_phase_samples"][0]["time_s"] == 5.0


def test_playback_runner_uses_in_memory_stripe_and_emits_diagnostics() -> None:
    features = MusicFeatures(
        energy=0.5,
        volume=0.5,
        energy_level=0.5,
        bass=0.4,
        bass_energy=0.4,
        onset_strength=0.3,
        beat=True,
        beat_strength=0.3,
        beat_confidence=0.8,
    )
    observations = [
        AudioObservation(
            index * 0.1,
            AudioSnapshot.from_parts(
                AudioFrame(fresh=True, sequence=index + 1), features
            ),
        )
        for index in range(4)
    ]

    trace, summary = run_scenario(
        "runner-test", SwitchQuantization.OFF, None, observations
    )

    assert len(trace) == len(observations)
    assert trace[-1]["active_animation"]
    assert "late_ticks_dropped" in trace[-1]
    assert summary["scheduler"]["late_ticks_coalesced"] >= 0
