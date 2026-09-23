import numpy as np
import pytest
from lumistripe.animation.base import Animation
from lumistripe.animation.catalog import AnimationDefinition, validate_animation_catalog
from lumistripe.audio.capture import BoundedAudioBatchBuffer
from lumistripe.audio.dsp import NativeFrameResult, NativeStatsResult
from lumistripe.playback import (
    MusicGateState,
    PlaybackMode,
    build_render_plan,
    decide_playback,
)


def test_bounded_audio_buffer_drops_oldest_batch() -> None:
    buffer = BoundedAudioBatchBuffer(max_batches=2)
    first = np.array([1.0], dtype=np.float32)
    second = np.array([2.0], dtype=np.float32)
    third = np.array([3.0], dtype=np.float32)

    assert buffer.push(first) is True
    assert buffer.push(second) is True
    assert buffer.push(third) is True
    assert buffer.dropped_count == 1

    received = buffer.pop(timeout=0.0)
    assert received is not None
    np.testing.assert_array_equal(received, second)
    buffer.task_done()
    received = buffer.pop(timeout=0.0)
    assert received is not None
    np.testing.assert_array_equal(received, third)
    buffer.task_done()
    assert buffer.wait_until_idle(timeout=0.0) is True


def test_native_results_accept_named_fields() -> None:
    frame = NativeFrameResult.from_native(
        {
            "rms": 0.4,
            "bands": [0.1, 0.2, 0.3],
            "beat": True,
            "beat_strength": 0.8,
            "sequence": 7,
        }
    )
    stats = NativeStatsResult.from_native(
        {
            "feed_count": 2,
            "samples_seen": 2_048,
            "fft_count": 1,
            "sample_abs_sum": 12.0,
            "normalization_gain": 1.2,
            "input_rms": 0.3,
            "program_loudness": 0.4,
            "musical_impact": 0.5,
        }
    )

    assert frame.bands == (0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert frame.beat is True
    assert stats.samples_seen == 2_048
    assert stats.musical_impact == pytest.approx(0.5)


def test_catalog_rejects_missing_metadata() -> None:
    class Example(Animation):
        name = "example"

        def tick(self, frame, controller):
            del frame, controller

    definition = AnimationDefinition(
        name="example",
        factory=Example,
        frame_ms=10,
        frames_per_cycle=10,
        automatic=True,
        display_name="Example",
    )

    with pytest.raises(ValueError, match="metadata is missing"):
        validate_animation_catalog((definition,))


def test_playback_decision_and_render_plan_are_pure_values() -> None:
    decision = decide_playback(
        PlaybackMode.DYNAMIC,
        animation_name="pulse",
        music_active=False,
        gate_state=MusicGateState.IDLE,
    )
    plan = build_render_plan(decision)

    assert decision.clear_output is True
    assert decision.reason == "music-gate-closed"
    assert plan.audio_frame is None
    assert plan.transition_ms == 0
