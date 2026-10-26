from __future__ import annotations

from lumistripe.animation.base import Animation, AnimationPlayer
from lumistripe.animation.red_rave import RedBlackoutStrobe
from lumistripe.audio import AudioFrame
from lumistripe.buffers import PixelBuffer
from lumistripe.color import Rgba
from lumistripe.controller import Controller
from lumistripe.render import native_render_dispatch_enabled
from lumistripe.stripe import Stripe


class _NativeProbe(Animation):
    native_render_enabled = True

    def __init__(self) -> None:
        self.native_calls = 0
        self.python_calls = 0

    @property
    def name(self) -> str:
        return "native_probe"

    def tick(self, frame: int, controller: Controller) -> None:
        self.python_calls += 1
        controller.fill(Rgba(12, 34, 56, 1.0))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        self.native_calls += 1
        destination[:] = (65, 43, 21, 255)
        return True


class _LargeStripNativeProbe(_NativeProbe):
    native_render_min_pixels = 600


def test_native_dispatch_is_gated_by_measured_strip_size() -> None:
    small = _NativeProbe()
    small_player = AnimationPlayer(audio_enabled=False)
    small_player.add(small, frame_ms=15, frames_per_cycle=100)
    small_player.step(Stripe(149))
    assert small.native_calls == 0
    assert small.python_calls == 1

    threshold = _NativeProbe()
    threshold_player = AnimationPlayer(audio_enabled=False)
    threshold_player.add(threshold, frame_ms=15, frames_per_cycle=100)
    threshold_player.step(Stripe(150))
    assert threshold.native_calls == 1
    assert threshold.python_calls == 0

    assert native_render_dispatch_enabled(149) is False
    assert native_render_dispatch_enabled(150) is True
    assert RedBlackoutStrobe.native_render_min_pixels == 600

    larger = _LargeStripNativeProbe()
    larger_player = AnimationPlayer(audio_enabled=False)
    larger_player.add(larger, frame_ms=15, frames_per_cycle=100)
    larger_player.step(Stripe(599))
    assert larger.native_calls == 0
    larger_player.step(Stripe(600))
    assert larger.native_calls == 1
