from __future__ import annotations

from types import ModuleType

import numpy as np
import pytest
from lumistripe import (
    Animation,
    AnimationPlayer,
    AudioFrame,
    AudioSnapshot,
    BeatTunnel,
    Effect,
    MusicFeatures,
    Rgb,
    Rgba,
    Stripe,
)
from lumistripe.controller import Controller
from lumistripe.effects.layers import (
    ActiveEffect,
    EffectCategory,
    EffectDefinition,
    EffectScheduler,
    EffectSchedulerConfig,
    LayeredRenderer,
)
from lumistripe.render import kernels

_PALETTES = ("rainbow", "neon", "fire", "custom")


def _configured_tunnel(palette: str, intensity: int) -> BeatTunnel:
    animation = BeatTunnel()
    animation.configure(
        {
            "speed": 83,
            "width": 24,
            "intensity": intensity,
            "palette": palette,
            "custom_color": "#2EA7D4",
        }
    )
    return animation


def _audio_sequence() -> tuple[AudioFrame, ...]:
    return (
        AudioFrame(
            rms=0.62,
            bands=(0.85, 0.7, 0.52, 0.61, 0.44, 0.25, 0.18, 0.3),
            beat=False,
            beat_strength=0.0,
            fresh=True,
        ),
        AudioFrame(
            rms=0.92,
            bands=(0.91, 0.89, 0.71, 0.76, 0.68, 0.81, 0.76, 0.88),
            beat=True,
            beat_strength=0.97,
            fresh=True,
        ),
        AudioFrame(
            rms=0.4,
            bands=(0.38, 0.31, 0.29, 0.36, 0.22, 0.68, 0.72, 0.57),
            beat=False,
            beat_strength=0.0,
            fresh=True,
        ),
        AudioFrame(
            rms=0.77,
            bands=(0.8, 0.74, 0.66, 0.7, 0.62, 0.42, 0.47, 0.5),
            beat=True,
            beat_strength=0.76,
            fresh=True,
        ),
        AudioFrame(
            rms=0.19,
            bands=(0.2, 0.17, 0.13, 0.22, 0.19, 0.31, 0.28, 0.25),
            beat=False,
            beat_strength=0.0,
            fresh=True,
        ),
    )


@pytest.mark.skipif(
    not kernels.native_tunnel_rings_available(),
    reason="render extension with BeatTunnel kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("palette", _PALETTES)
@pytest.mark.parametrize("intensity", [35, 100])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True], ids=["static", "audio"])
def test_native_beat_tunnel_matches_python_pixels_and_state(
    length: int,
    palette: str,
    intensity: int,
    brightness: float,
    audio_mode: bool,
) -> None:
    native = _configured_tunnel(palette, intensity)
    python = _configured_tunnel(palette, intensity)
    destination = np.empty((length, 4), dtype=np.uint8)
    python_stripe = Stripe(length)
    frames = _audio_sequence() if audio_mode else (None,) * 5

    for index, audio in enumerate(frames):
        configured_audio = None if audio is None else native.effective_audio(audio)
        assert native._render_native(index, destination, configured_audio, brightness)

        configured_audio = None if audio is None else python.effective_audio(audio)
        controller = python.output_controller(python_stripe, brightness)
        if configured_audio is None:
            python.tick(index, controller)
        else:
            python.tick_audio(index, controller, configured_audio)

        np.testing.assert_array_equal(destination, python_stripe.pixels())
        assert native.radius == pytest.approx(python.radius)
        assert native.direction == pytest.approx(python.direction)
        assert native.pulse.value == pytest.approx(python.pulse.value)


def test_native_hook_declines_without_changing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lumistripe.effects.beat_tunnel.native_tunnel_rings_available",
        lambda: False,
    )
    animation = _configured_tunnel("rainbow", 100)
    destination = np.full((16, 4), 123, dtype=np.uint8)
    previous = destination.copy()

    assert not animation._render_native(2, destination, _audio_sequence()[0], 0.7)

    np.testing.assert_array_equal(destination, previous)
    assert animation.radius == 0.0
    assert animation.direction == 1.0
    assert animation.pulse.value == 0.0


class _SolidBase(Animation):
    @property
    def name(self) -> str:
        return "solid_base"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgb(40, 80, 140))


class _SolidEffect(Effect):
    @property
    def name(self) -> str:
        return "solid_effect"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgba(30, 210, 90, 0.65))


def _render_layered_frames(native_module: object) -> tuple[np.ndarray, ...]:
    kernels._native_module = native_module  # type: ignore[assignment]
    player = AnimationPlayer(transition_ms=0)
    player.add(_SolidBase(), 50, 10)
    player.set_brightness(0.67)
    snapshot = AudioSnapshot.from_parts(
        _audio_sequence()[0], MusicFeatures(musical_impact=0.7)
    )
    scheduler = EffectScheduler(EffectSchedulerConfig(max_overlay_strength=0.75))
    renderer = LayeredRenderer(scheduler)
    stripe = Stripe(600)
    renderer.render(player, stripe, snapshot, now_s=1.0, flush=False)

    tunnel = _configured_tunnel("neon", 72)
    scheduler.active.extend(
        (
            ActiveEffect(
                EffectDefinition("beat_tunnel", EffectCategory.RHYTHMIC, 10.0, 0.5),
                tunnel,
                1.0,
                snapshot.frame,
            ),
            ActiveEffect(
                EffectDefinition("solid_effect", EffectCategory.ACCENT, 10.0, 0.4),
                _SolidEffect(),
                1.0,
                snapshot.frame,
            ),
        )
    )

    rendered = []
    for index, audio in enumerate(_audio_sequence()):
        frame_snapshot = AudioSnapshot.from_parts(
            audio, MusicFeatures(musical_impact=0.7)
        )
        renderer.render(
            player,
            stripe,
            frame_snapshot,
            now_s=1.02 + index * 0.02,
            flush=False,
        )
        rendered.append(stripe.pixels().copy())
    return tuple(rendered)


@pytest.mark.skipif(
    not kernels.native_tunnel_rings_available(),
    reason="render extension with BeatTunnel kernel is not built",
)
def test_layered_renderer_native_tunnel_preserves_budgeted_composited_frames() -> None:
    native = kernels._native_module
    assert native is not None
    python_effect_module = ModuleType("lumistripe.render._native_without_tunnel")
    for name in (
        "correct_rgba_into",
        "apply_alpha_scale_into",
        "composite_screen_rgba_into",
    ):
        setattr(python_effect_module, name, getattr(native, name))

    expected = _render_layered_frames(python_effect_module)
    actual = _render_layered_frames(native)

    assert len(actual) == len(expected)
    for native_frame, python_frame in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(native_frame, python_frame)


def test_layered_renderer_uses_python_tunnel_when_extension_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kernels, "_native_module", None)

    frames = _render_layered_frames(None)

    assert len(frames) == len(_audio_sequence())
    for frame in frames:
        assert frame.shape == (600, 4)
        assert np.all(frame[:, 3] == 255)
