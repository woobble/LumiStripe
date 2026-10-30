from __future__ import annotations

import numpy as np
import pytest
from lumistripe import (
    AudioFrame,
    Aurora,
    BassDrop,
    BeatExplosion,
    BeatRipple,
    BeatWave,
    BouncingBall,
    Bpm,
    CenterBurst,
    ClubFlash,
    ColorBurst,
    ColorWipe,
    Comet,
    CometStorm,
    Confetti,
    DanceFloor,
    DiscoComet,
    DiscoSparkle,
    DropExplosion,
    DropWave,
    DualComet,
    DualLaser,
    ElectricStorm,
    Fire,
    FireworkBurst,
    GlowRush,
    HardBeat,
    Juggle,
    LaserSweep,
    LightningStrike,
    MirrorFlash,
    NeonConfetti,
    NeonStorm,
    PeakMirror,
    PixelExplosion,
    PlasmaRave,
    Police,
    Pulse,
    Rainbow,
    RainbowCycle,
    RainbowStrobe,
    RavePulse,
    RaveScanner,
    RedBlackoutStrobe,
    RedRaveChase,
    RedRaveSweep,
    Rgba,
    Shockwave,
    Sinelon,
    Stripe,
    Strobe,
    StrobeChase,
    TheaterChase,
    Twinkle,
    Wave,
)
from lumistripe.render import kernels


@pytest.mark.skipif(
    not kernels.native_color_burst_available(),
    reason="render extension with ColorBurst kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_color_burst_matches_python(
    audio_mode: bool, length: int, palette: str
) -> None:
    settings = {
        "count": 5,
        "spread": 73,
        "speed": 64,
        "decay": 61,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = ColorBurst(), ColorBurst()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
        AudioFrame(
            rms=0.93,
            bands=(0.8, 0.8, 0.8, 0.8, 0.8, 0.7, 0.8, 0.9),
        ),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            assert configured is not None
            python.tick_audio(frame, controller, configured)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert len(native.bursts) == len(python.bursts)
        for actual, expected in zip(native.bursts, python.bursts, strict=True):
            assert actual.center == pytest.approx(expected.center)
            assert actual.radius == pytest.approx(expected.radius)
            assert actual.speed == pytest.approx(expected.speed)
            assert actual.hue_seed == expected.hue_seed
            assert actual.strength == pytest.approx(expected.strength)
            assert actual.width == pytest.approx(expected.width)


@pytest.mark.skipif(
    not kernels.native_firework_burst_available(),
    reason="render extension with FireworkBurst kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_firework_burst_matches_python(
    audio_mode: bool, length: int, palette: str
) -> None:
    settings = {
        "count": 5,
        "spread": 73,
        "fade_time": 62,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = FireworkBurst(), FireworkBurst()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
        AudioFrame(
            rms=0.63,
            bands=(0.7, 0.8, 0.6, 0.5, 0.4, 0.7, 0.6, 0.5),
            beat=True,
            beat_strength=0.72,
        ),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            assert configured is not None
            python.tick_audio(frame, controller, configured)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native._hue == python._hue
        assert len(native.bursts) == len(python.bursts)
        for actual, expected in zip(native.bursts, python.bursts, strict=True):
            assert actual.phase == pytest.approx(expected.phase)
            assert actual.strength == pytest.approx(expected.strength)
            assert actual.speed == pytest.approx(expected.speed)
            assert actual.hue == expected.hue


@pytest.mark.skipif(
    not kernels.native_firework_burst_available(),
    reason="render extension with FireworkBurst kernel is not built",
)
@pytest.mark.parametrize("frame", [2**31 + 17, 2**40 + 123])
def test_native_firework_burst_matches_python_for_long_running_frames(frame: int) -> None:
    settings = {"count": 5, "spread": 73, "fade_time": 62}
    native, python = FireworkBurst(), FireworkBurst()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((40, 4), dtype=np.uint8)
    stripe = Stripe(40)

    assert native._render_native(frame, destination, None, 0.73)
    python.tick(frame, python.output_controller(stripe, 0.73))

    np.testing.assert_array_equal(destination, stripe.pixels())


@pytest.mark.skipif(
    not kernels.native_beat_explosion_available(),
    reason="render extension with BeatExplosion kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_beat_explosion_matches_python(
    audio_mode: bool, length: int, palette: str
) -> None:
    settings = {
        "speed": 63,
        "width": 47,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = BeatExplosion(), BeatExplosion()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
        AudioFrame(
            rms=0.63,
            bands=(0.7, 0.8, 0.6, 0.5, 0.4, 0.7, 0.6, 0.5),
            beat=True,
            beat_strength=0.72,
        ),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            assert configured is not None
            python.tick_audio(frame, controller, configured)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.radius == pytest.approx(python.radius)
        assert native.burst.value == pytest.approx(python.burst.value)


@pytest.mark.skipif(
    not kernels.native_beat_wave_available(),
    reason="render extension with BeatWave kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_beat_wave_matches_python(
    audio_mode: bool, length: int, palette: str
) -> None:
    settings = {
        "speed": 68,
        "width": 47,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = BeatWave(), BeatWave()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
        AudioFrame(
            rms=0.63,
            bands=(0.7, 0.8, 0.6, 0.5, 0.4, 0.7, 0.6, 0.5),
            beat=True,
            beat_strength=0.72,
        ),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            assert configured is not None
            python.tick_audio(frame, controller, configured)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native._hue == python._hue
        assert len(native.waves) == len(python.waves)
        for actual, expected in zip(native.waves, python.waves, strict=True):
            assert actual.pos == pytest.approx(expected.pos)
            assert actual.strength == pytest.approx(expected.strength)


@pytest.mark.skipif(
    not kernels.native_disco_sparkle_available(),
    reason="render extension with DiscoSparkle kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_disco_sparkle_matches_python(
    audio_mode: bool, length: int, palette: str
) -> None:
    settings = {
        "density": 67,
        "fade_time": 63,
        "variation": 81,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = DiscoSparkle(), DiscoSparkle()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            assert configured is not None
            python.tick_audio(frame, controller, configured)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())
        np.testing.assert_array_equal(native._intensities, python._intensities)


@pytest.mark.skipif(
    not kernels.native_disco_sparkle_available(),
    reason="render extension with DiscoSparkle kernel is not built",
)
def test_native_disco_sparkle_max_fade_keeps_intensities_bounded() -> None:
    settings = {
        "density": 0,
        "fade_time": 100,
        "variation": 50,
        "music_reactivity": 100,
        "sensitivity": 50,
    }
    native, python = DiscoSparkle(), DiscoSparkle()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((1, 4), dtype=np.uint8)
    stripe = Stripe(1)

    for frame in range(24):
        assert native._render_native(frame, destination, None, 1.0)
        python.tick(frame, python.output_controller(stripe, 1.0))
        np.testing.assert_array_equal(destination, stripe.pixels())
        np.testing.assert_array_equal(native._intensities, python._intensities)
        assert np.all(native._intensities <= 1.0)


@pytest.mark.skipif(
    not kernels.native_disco_sparkle_available(),
    reason="render extension with DiscoSparkle kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_disco_sparkle_handles_large_frame_indices(audio_mode: bool) -> None:
    settings = {
        "density": 67,
        "fade_time": 63,
        "variation": 81,
        "music_reactivity": 100,
        "sensitivity": 50,
    }
    native, python = DiscoSparkle(), DiscoSparkle()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((3, 4), dtype=np.uint8)
    stripe = Stripe(3)
    frame = 2**62 + 123
    audio = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6))
        if audio_mode
        else None
    )

    configured = native.effective_audio(audio) if audio is not None else None
    assert native._render_native(frame, destination, configured, 0.73)
    controller = python.output_controller(stripe, 0.73)
    if audio is None:
        python.tick(frame, controller)
    else:
        assert configured is not None
        python.tick_audio(frame, controller, configured)

    np.testing.assert_array_equal(destination, stripe.pixels())
    np.testing.assert_array_equal(native._intensities, python._intensities)


@pytest.mark.skipif(
    not kernels.native_disco_sparkle_available(),
    reason="render extension with DiscoSparkle kernel is not built",
)
@pytest.mark.parametrize(
    ("frame", "decay", "variation", "hue_rate", "pulse"),
    [
        (-1, 0.9, 0.5, 0.6, 5.0),
        (0, -0.1, 0.5, 0.6, 5.0),
        (0, 1.1, 0.5, 0.6, 5.0),
        (0, 0.9, 1.1, 0.6, 5.0),
        (0, 0.9, 0.5, 3.1, 5.0),
        (0, 0.9, 0.5, 0.6, 10.1),
    ],
)
def test_native_disco_sparkle_rejects_unsafe_scalar_ranges(
    frame: int, decay: float, variation: float, hue_rate: float, pulse: float
) -> None:
    module = kernels._native_module
    assert module is not None
    destination = np.zeros((1, 4), dtype=np.uint8)
    intensities = np.zeros(1, dtype=np.float32)

    with pytest.raises(ValueError, match="invalid DiscoSparkle"):
        module.render_disco_sparkle_into(
            destination,
            intensities,
            frame,
            0,
            5,
            0,
            decay,
            variation,
            hue_rate,
            pulse,
            1.0,
            0,
            0,
        )


@pytest.mark.skipif(
    not kernels.native_dance_floor_available(),
    reason="render extension with DanceFloor kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_dance_floor_matches_python(
    audio_mode: bool, length: int, palette: str
) -> None:
    settings = {
        "frequency": 73,
        "intensity": 100,
        "music_reactivity": 100,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = DanceFloor(), DanceFloor()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            assert configured is not None
            python.tick_audio(frame, controller, configured)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())


@pytest.mark.skipif(
    not kernels.native_club_flash_available(),
    reason="render extension with ClubFlash kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 7, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_club_flash_matches_python(
    audio_mode: bool, length: int, palette: str
) -> None:
    settings = {
        "flash_rate": 78,
        "duty_cycle": 42,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = ClubFlash(), ClubFlash()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.34, bands=(0.2, 0.3, 0.4, 0.3, 0.2, 0.3, 0.2, 0.1)),
        AudioFrame(
            rms=0.83,
            bands=(0.82, 0.76, 0.65, 0.7, 0.6, 0.5, 0.6, 0.7),
            beat=True,
            beat_strength=0.92,
        ),
        AudioFrame(rms=0.28, bands=(0.1, 0.2, 0.2, 0.3, 0.4, 0.3, 0.2, 0.1)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            assert configured is not None
            python.tick_audio(frame, controller, configured)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.flash.value == pytest.approx(python.flash.value)
        assert native.hue_seed == python.hue_seed
        assert native.segment_start == python.segment_start
        assert native.segment_width == python.segment_width
        assert native.whole_strip == python.whole_strip


@pytest.mark.skipif(
    not kernels.native_comet_available(),
    reason="render extension with Comet kernel is not built",
)
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
def test_native_comet_matches_python(
    palette: str, audio_mode: bool, length: int
) -> None:
    settings = {
        "speed": 71,
        "tail_length": 34,
        "intensity": 82,
        "direction": "bounce",
        "palette": palette,
        "custom_color": "#2EA7D4",
        "music_reactivity": 76,
    }
    native, python = Comet(), Comet()
    native.set_settings(settings)
    python.set_settings(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            python.tick_audio(frame, controller, audio)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.position == pytest.approx(python.position)
        assert native._motion_direction == python._motion_direction


@pytest.mark.skipif(
    not kernels.native_pulse_available(),
    reason="render extension with Pulse kernel is not built",
)
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("pulse_curve", ["sine", "smoothstep", "sharp"])
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
def test_native_pulse_matches_python(
    palette: str, pulse_curve: str, audio_mode: bool, length: int
) -> None:
    settings = {
        "speed": 63,
        "minimum_intensity": 18,
        "maximum_intensity": 87,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "pulse_curve": pulse_curve,
        "music_reactivity": 76,
    }
    native = Pulse()
    python = Pulse()
    native.set_settings(settings)
    python.set_settings(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        controller = python.output_controller(stripe, 0.73)
        if audio_mode:
            python.tick_audio(frame, controller, audio)
        else:
            python.tick(frame, controller)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native._pulse == pytest.approx(python._pulse)
        assert native._hue == python._hue


@pytest.mark.skipif(
    not kernels.native_solid_render_available()
    or not kernels.native_halves_render_available(),
    reason="render extension with solid/halves kernels is not built",
)
@pytest.mark.parametrize("animation_type", [Bpm, Police, RedBlackoutStrobe, Strobe])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_solid_animations_match_python(
    animation_type: type[Bpm | Police | RedBlackoutStrobe | Strobe],
    brightness: float,
    audio_mode: bool,
) -> None:
    settings = {
        "flash_rate": 71,
        "duty_cycle": 43,
        "intensity": 82,
        "palette": "neon",
        "custom_color": "#2EA7D4",
        "pattern": "double",
        "speed": 65,
        "decay": 42,
    }
    native = animation_type()
    python = animation_type()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((150, 4), dtype=np.uint8)
    stripe = Stripe(150)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6), beat=False),
        AudioFrame(rms=0.71, bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8), beat=True),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2), beat=False),
    )

    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        if isinstance(native, Bpm):
            assert native._flash == pytest.approx(python._flash)
            assert native._hue == python._hue
        if isinstance(native, RedBlackoutStrobe):
            assert native.flash_frames == python.flash_frames
            assert native._last_beat_frame == python._last_beat_frame


@pytest.mark.skipif(
    not kernels.native_red_sweep_available()
    or not kernels.native_red_chase_available(),
    reason="render extension with red motion kernels is not built",
)
@pytest.mark.parametrize("animation_type", [RedRaveSweep, RedRaveChase])
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_red_motion_animations_match_python(
    animation_type: type[RedRaveSweep | RedRaveChase],
    length: int,
    brightness: float,
    audio_mode: bool,
) -> None:
    settings = {
        "speed": 65,
        "width": 38,
        "count": 5,
        "duty_cycle": 57,
    }
    native = animation_type()
    python = animation_type()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )

    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        if isinstance(native, RedRaveSweep):
            assert native.position == pytest.approx(python.position)
            assert native.direction == python.direction
        else:
            assert native.phase == pytest.approx(python.phase)
            assert native.direction == python.direction


@pytest.mark.skipif(
    not kernels.native_hsl_solid_available(),
    reason="render extension with HSL solid kernel is not built",
)
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_rave_pulse_matches_python(brightness: float, audio_mode: bool) -> None:
    settings = {
        "speed": 63,
        "intensity": 78,
        "pulse_curve": "smoothstep",
        "palette": "rainbow",
        "custom_color": "#2EA7D4",
        "music_reactivity": 100,
    }
    native = RavePulse()
    python = RavePulse()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((300, 4), dtype=np.uint8)
    stripe = Stripe(300)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.hue == python.hue
        assert native.flash.value == pytest.approx(python.flash.value)


@pytest.mark.skipif(
    not kernels.native_hsl_solid_available()
    or not kernels.native_solid_render_available(),
    reason="render extension with solid HSL kernels is not built",
)
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_hard_beat_matches_python(brightness: float, audio_mode: bool) -> None:
    settings = {"duty_cycle": 47, "decay": 38, "intensity": 78}
    native = HardBeat()
    python = HardBeat()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((300, 4), dtype=np.uint8)
    stripe = Stripe(300)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.hue == python.hue
        assert native.flash.value == pytest.approx(python.flash.value)


@pytest.mark.skipif(
    not kernels.native_solid_render_available(),
    reason="render extension with solid kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_bass_drop_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    settings = {
        "speed": 64,
        "decay": 42,
        "intensity": 82,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "beat_sensitivity": 100,
    }
    native = BassDrop()
    python = BassDrop()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.charge == pytest.approx(python.charge)
        assert native.flash.value == pytest.approx(python.flash.value)


@pytest.mark.skipif(
    not kernels.native_drop_explosion_available(),
    reason="render extension with DropExplosion kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("brightness", [0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_drop_explosion_matches_python(
    length: int, brightness: float, audio_mode: bool
) -> None:
    settings = {
        "speed": 64,
        "decay": 42,
        "duty_cycle": 57,
        "intensity": 82,
        "music_reactivity": 100,
    }
    native = DropExplosion()
    python = DropExplosion()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.charge == pytest.approx(python.charge)
        assert native.flash.value == pytest.approx(python.flash.value)


@pytest.mark.skipif(
    not kernels.native_fire_available(),
    reason="render extension with Fire kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("brightness", [0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_fire_matches_python(
    length: int, brightness: float, audio_mode: bool
) -> None:
    settings = {"cooling": 47, "sparking": 61, "intensity": 82}
    native = Fire()
    python = Fire()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        np.testing.assert_array_equal(native.heat, python.heat)


@pytest.mark.skipif(
    not kernels.native_color_wipe_available(),
    reason="render extension with ColorWipe kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("direction", ["bounce", "forward", "reverse"])
@pytest.mark.parametrize("brightness", [0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_color_wipe_matches_python(
    length: int, direction: str, brightness: float, audio_mode: bool
) -> None:
    settings = {
        "speed": 64,
        "tail_length": 18,
        "direction": direction,
        "palette": "rainbow",
        "custom_color": "#2EA7D4",
        "music_reactivity": 100,
    }
    native = ColorWipe()
    python = ColorWipe()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
        ),
        AudioFrame(rms=0.3, bands=(0.3, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.head == pytest.approx(python.head)
        assert native._motion_direction == python._motion_direction
        assert native.palette_index == python.palette_index
        assert native.speed == pytest.approx(python.speed)


@pytest.mark.skipif(
    not kernels.native_rainbow_pattern_available(),
    reason="render extension with rainbow pattern kernel is not built",
)
@pytest.mark.parametrize("animation_type", [Rainbow, RainbowCycle])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_rainbow_animations_match_python(
    animation_type: type[Rainbow | RainbowCycle],
    length: int,
    palette: str,
    brightness: float,
    audio_mode: bool,
) -> None:
    settings = {
        "color_spread": 72,
        "intensity": 82,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "music_reactivity": 100,
    }
    native = animation_type()
    python = animation_type()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(
            rms=0.96,
            bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28),
            beat=True,
            beat_strength=0.94,
        ),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        if isinstance(native, Rainbow):
            assert native._beat_flash == pytest.approx(python._beat_flash)


@pytest.mark.skipif(
    not kernels.native_rainbow_pattern_available(),
    reason="render extension with rainbow pattern kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_rainbow_strobe_matches_python(
    length: int, brightness: float, audio_mode: bool
) -> None:
    native = RainbowStrobe()
    python = RainbowStrobe()
    settings = {"flash_rate": 72, "duty_cycle": 55, "intensity": 82, "color_spread": 63}
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())


@pytest.mark.skipif(
    not kernels.native_theater_chase_available(),
    reason="render extension with TheaterChase kernel is not built",
)
@pytest.mark.parametrize("length", [1, 2, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_theater_chase_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = TheaterChase()
    python = TheaterChase()
    settings = {
        "count": 3,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.phase == pytest.approx(python.phase)
        assert native.hue_phase == pytest.approx(python.hue_phase)


@pytest.mark.skipif(
    not kernels.native_laser_sweep_available(),
    reason="render extension with LaserSweep kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_laser_sweep_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = LaserSweep()
    python = LaserSweep()
    settings = {
        "speed": 72,
        "width": 46,
        "direction": "bounce",
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.position == pytest.approx(python.position)
        assert native.direction == pytest.approx(python.direction)


@pytest.mark.skipif(
    not kernels.native_sinelon_available(),
    reason="render extension with Sinelon kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_sinelon_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = Sinelon()
    python = Sinelon()
    settings = {
        "fade_time": 67,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())


@pytest.mark.skipif(
    not kernels.native_peak_mirror_available(),
    reason="render extension with PeakMirror kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_peak_mirror_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = PeakMirror()
    python = PeakMirror()
    settings = {
        "width": 68,
        "decay": 61,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native._center_flash == pytest.approx(python._center_flash)
        assert [peak.value for peak in native.peaks] == pytest.approx(
            [peak.value for peak in python.peaks]
        )


@pytest.mark.skipif(
    not kernels.native_bouncing_ball_available(),
    reason="render extension with BouncingBall kernel is not built",
)
@pytest.mark.parametrize("length", [1, 2, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_bouncing_ball_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = BouncingBall()
    python = BouncingBall()
    settings = {
        "speed": 72,
        "particle_size": 61,
        "direction": "bounce",
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.pos == pytest.approx(python.pos)
        assert native.speed == pytest.approx(python.speed)
        assert native.direction == pytest.approx(python.direction)


@pytest.mark.skipif(
    not kernels.native_wave_available(),
    reason="render extension with Wave kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_wave_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = Wave()
    python = Wave()
    settings = {
        "frequency": 71,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())


@pytest.mark.skipif(
    not kernels.native_juggle_available(),
    reason="render extension with Juggle kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_juggle_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = Juggle()
    python = Juggle()
    settings = {
        "count": 4,
        "speed": 72,
        "particle_size": 61,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.positions == pytest.approx(python.positions)
        assert native.speeds == pytest.approx(python.speeds)


@pytest.mark.skipif(
    not kernels.native_confetti_available(),
    reason="render extension with Confetti kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_confetti_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = Confetti()
    python = Confetti()
    settings = {
        "count": 7,
        "spread": 73,
        "fade_time": 64,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        np.testing.assert_array_equal(native.intensities, python.intensities)


@pytest.mark.skipif(
    not kernels.native_glow_rush_available(),
    reason="render extension with GlowRush kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_glow_rush_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = GlowRush()
    python = GlowRush()
    settings = {
        "speed": 72,
        "width": 63,
        "frequency": 71,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    stripe = Stripe(length)
    destination = np.array(stripe.pixels(), copy=True)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.position == pytest.approx(python.position)
        assert native.hue == python.hue


@pytest.mark.skipif(
    not kernels.native_dual_comet_available(),
    reason="render extension with DualComet kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_dual_comet_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = DualComet()
    python = DualComet()
    settings = {
        "speed": 72,
        "tail_length": 28,
        "separation": 63,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.phase == pytest.approx(python.phase)
        assert native.direction == pytest.approx(python.direction)
        assert native.burst.value == pytest.approx(python.burst.value)


@pytest.mark.skipif(
    not kernels.native_shockwave_available(),
    reason="render extension with Shockwave kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_shockwave_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = Shockwave()
    python = Shockwave()
    settings = {
        "speed": 72,
        "width": 63,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.radius == pytest.approx(python.radius)
        assert native.accent.value == pytest.approx(python.accent.value)


@pytest.mark.skipif(
    not kernels.native_aurora_available(),
    reason="render extension with Aurora kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_aurora_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    native = Aurora()
    python = Aurora()
    settings = {
        "frequency": 71,
        "palette": palette,
        "custom_color": "#2EA7D4",
        "intensity": 82,
    }
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.12, bands=(0.08, 0.17, 0.29, 0.41, 0.53, 0.65, 0.77, 0.9)),
        AudioFrame(rms=0.96, bands=(0.91, 0.82, 0.73, 0.64, 0.55, 0.46, 0.37, 0.28), beat=True, beat_strength=0.94),
        AudioFrame(rms=0.32, bands=(0.31, 0.29, 0.27, 0.25, 0.23, 0.21, 0.19, 0.17)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.glow.value == pytest.approx(python.glow.value)


@pytest.mark.skipif(
    not kernels.native_comet_storm_available(),
    reason="render extension with CometStorm kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_comet_storm_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    settings = {
        "count": 6,
        "speed": 68,
        "tail_length": 17,
        "intensity": 78,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native = CometStorm()
    python = CometStorm()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.45, bands=(0.3, 0.4, 0.6, 0.7, 0.5, 0.4, 0.3, 0.2)),
        AudioFrame(rms=0.78, bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8), beat=True),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.phase == pytest.approx(python.phase)
        assert native.hue_phase == pytest.approx(python.hue_phase)


@pytest.mark.skipif(
    not kernels.native_twinkle_available(),
    reason="render extension with Twinkle kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_twinkle_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    settings = {
        "density": 68,
        "fade_time": 47,
        "particle_size": 52,
        "intensity": 78,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native = Twinkle()
    python = Twinkle()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.45, bands=(0.3, 0.4, 0.6, 0.7, 0.5, 0.4, 0.3, 0.2)),
        AudioFrame(rms=0.78, bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8), beat=True),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        np.testing.assert_array_equal(native.levels, python.levels)
        np.testing.assert_array_equal(native.hues, python.hues)
        assert native._bloom == pytest.approx(python._bloom)


@pytest.mark.skipif(
    not kernels.native_plasma_rave_available(),
    reason="render extension with PlasmaRave kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("brightness", [0.0, 0.37, 1.0])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_plasma_rave_matches_python(
    length: int, palette: str, brightness: float, audio_mode: bool
) -> None:
    settings = {
        "frequency": 73,
        "intensity": 81,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = PlasmaRave(), PlasmaRave()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames, start=7):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, brightness)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, brightness)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())


@pytest.mark.skipif(
    not kernels.native_beat_ripple_available(),
    reason="render extension with BeatRipple kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_beat_ripple_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "speed": 63,
        "width": 46,
        "decay": 57,
        "intensity": 83,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = BeatRipple(), BeatRipple()
    native.configure(settings)
    python.configure(settings)
    destination = np.zeros((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames, start=7):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.73)
        configured = python.effective_audio(audio) if audio_mode else None
        stripe.fill(Rgba(0, 0, 0, 0.0))
        controller = python.output_controller(stripe, 0.73)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.radius == pytest.approx(python.radius)
        assert native.hue == python.hue
        assert native.burst.value == pytest.approx(python.burst.value)


@pytest.mark.skipif(
    not kernels.native_neon_confetti_available(),
    reason="render extension with NeonConfetti kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_neon_confetti_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "count": 7,
        "spread": 73,
        "fade_time": 41,
        "intensity": 84,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = NeonConfetti(), NeonConfetti()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames, start=11):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        np.testing.assert_allclose(native.levels, python.levels, rtol=1e-6, atol=1e-7)
        np.testing.assert_array_equal(native.hues, python.hues)


@pytest.mark.skipif(
    not kernels.native_strobe_chase_available(),
    reason="render extension with StrobeChase kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_strobe_chase_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "count": 6,
        "speed": 67,
        "duty_cycle": 42,
        "intensity": 86,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = StrobeChase(), StrobeChase()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.phase == pytest.approx(python.phase)
        assert native.hue_phase == pytest.approx(python.hue_phase)
        assert native.flash.value == pytest.approx(python.flash.value)


@pytest.mark.skipif(
    not kernels.native_rave_scanner_available(),
    reason="render extension with RaveScanner kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("direction", ["bounce", "forward", "reverse"])
def test_native_rave_scanner_matches_python(
    length: int, palette: str, audio_mode: bool, direction: str
) -> None:
    settings = {
        "direction": direction,
        "speed": 67,
        "width": 46,
        "intensity": 86,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = RaveScanner(), RaveScanner()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.phase == pytest.approx(python.phase)
        assert native.direction == pytest.approx(python.direction)
        assert native.flash.value == pytest.approx(python.flash.value)


@pytest.mark.skipif(
    not kernels.native_center_burst_available(),
    reason="render extension with CenterBurst kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_center_burst_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "speed": 62,
        "width": 48,
        "intensity": 84,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = CenterBurst(), CenterBurst()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.radius == pytest.approx(python.radius)
        assert native.speed == pytest.approx(python.speed)
        assert native.width == pytest.approx(python.width)
        assert native.hue_seed == python.hue_seed
        assert native.flash.value == pytest.approx(python.flash.value)


@pytest.mark.skipif(
    not kernels.native_dual_laser_available(),
    reason="render extension with DualLaser kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_dual_laser_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "speed": 63,
        "width": 42,
        "separation": 68,
        "intensity": 79,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = DualLaser(), DualLaser()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames, start=5):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.beam_a.position == pytest.approx(python.beam_a.position)
        assert native.beam_b.position == pytest.approx(python.beam_b.position)
        assert native.beam_a.direction == pytest.approx(python.beam_a.direction)
        assert native.beam_b.direction == pytest.approx(python.beam_b.direction)
        assert native.beam_a.hue == python.beam_a.hue
        assert native.beam_b.hue == python.beam_b.hue
        assert native.burst.value == pytest.approx(python.burst.value)


@pytest.mark.skipif(
    not kernels.native_mirror_flash_available(),
    reason="render extension with MirrorFlash kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_mirror_flash_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "width": 63,
        "decay": 42,
        "intensity": 79,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = MirrorFlash(), MirrorFlash()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames, start=7):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.flash.value == pytest.approx(python.flash.value)
        assert native.hue_seed == python.hue_seed
        assert native.alternate == python.alternate


@pytest.mark.skipif(
    not kernels.native_drop_wave_available(),
    reason="render extension with DropWave kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_drop_wave_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "speed": 61,
        "width": 47,
        "decay": 52,
        "intensity": 82,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = DropWave(), DropWave()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.91,
            bands=(0.9, 0.8, 0.8, 0.7, 0.6, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.95,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    count = 5 if audio_mode else 80
    for frame in range(count):
        audio = frames[frame % len(frames)]
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.charge == pytest.approx(python.charge)
        assert native.radius == pytest.approx(python.radius)
        assert native.speed == pytest.approx(python.speed)
        assert native.hue_seed == python.hue_seed
        assert native.wave.value == pytest.approx(python.wave.value)


@pytest.mark.skipif(
    not kernels.native_disco_comet_available(),
    reason="render extension with DiscoComet kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("direction", ["bounce", "forward", "reverse"])
def test_native_disco_comet_matches_python(
    length: int, palette: str, audio_mode: bool, direction: str
) -> None:
    settings = {
        "direction": direction,
        "speed": 63,
        "tail_length": 18,
        "intensity": 84,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = DiscoComet(), DiscoComet()
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames, start=19):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.phase == pytest.approx(python.phase)
        assert native.direction == pytest.approx(python.direction)


@pytest.mark.skipif(
    not kernels.native_pixel_explosion_available(),
    reason="render extension with PixelExplosion kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_pixel_explosion_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "count": 6,
        "spread": 71,
        "decay": 53,
        "intensity": 82,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = PixelExplosion(), PixelExplosion()
    native.configure(settings)
    python.configure(settings)
    native._rng.seed(7241)
    python._rng.seed(7241)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame in range(8):
        audio = frames[frame % len(frames)]
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        stripe.fill(Rgba(0, 0, 0, 0.0))
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert len(native.bursts) == len(python.bursts)
        for native_burst, python_burst in zip(native.bursts, python.bursts, strict=True):
            assert native_burst.x == pytest.approx(python_burst.x)
            assert native_burst.radius == pytest.approx(python_burst.radius)
            assert native_burst.hue == python_burst.hue
            assert native_burst.alpha.value == pytest.approx(python_burst.alpha.value)


@pytest.mark.skipif(
    not kernels.native_lightning_strike_available(),
    reason="render extension with LightningStrike kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_native_lightning_strike_matches_python(
    length: int, palette: str, audio_mode: bool
) -> None:
    settings = {
        "spread": 61,
        "width": 44,
        "decay": 48,
        "intensity": 84,
        "music_reactivity": 100,
        "sensitivity": 50,
        "palette": palette,
        "custom_color": "#2EA7D4",
    }
    native, python = LightningStrike(), LightningStrike()
    native.configure(settings)
    python.configure(settings)
    native._rng.seed(427)
    python._rng.seed(427)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(
            rms=0.71,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8),
            beat=True,
            beat_strength=0.9,
        ),
        AudioFrame(rms=0.31, bands=(0.2, 0.3, 0.4, 0.5, 0.6, 0.4, 0.3, 0.2)),
    )
    for frame, audio in enumerate(frames, start=43):
        configured = native.effective_audio(audio) if audio_mode else None
        assert native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.flash.value == pytest.approx(python.flash.value)
        assert native.strike_pos == python.strike_pos
        assert native.strike_len == python.strike_len
        assert native.hue == python.hue


@pytest.mark.skipif(
    not kernels.native_neon_storm_available(),
    reason="render extension with NeonStorm kernel is not built",
)
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_neon_storm_matches_python(length: int, audio_mode: bool, palette: str) -> None:
    native, python = NeonStorm(), NeonStorm()
    settings = {"count": 5, "speed": 63, "decay": 47, "music_reactivity": 100, "sensitivity": 50, "palette": palette, "custom_color": "#2EA7D4"}
    native.configure(settings)
    python.configure(settings)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    frames = (
        AudioFrame(rms=0.42, bands=(0.2, 0.4, 0.7, 0.8, 0.4, 0.3, 0.5, 0.6)),
        AudioFrame(rms=0.71, bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.6, 0.7, 0.8)),
    )
    for frame in range(8):
        audio = frames[frame % len(frames)]
        configured = native.effective_audio(audio) if audio_mode else None
        rendered = native._render_native(frame, destination, configured, 0.72)
        configured = python.effective_audio(audio) if audio_mode else None
        stripe.fill(Rgba(0, 0, 0, 0.0))
        controller = python.output_controller(stripe, 0.72)
        if configured is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, configured)
        if not rendered:
            fallback_stripe = Stripe(length)
            fallback_controller = native.output_controller(fallback_stripe, 0.72)
            if audio_mode:
                native.tick_audio(frame, fallback_controller, native.effective_audio(audio))
            else:
                native.tick(frame, fallback_controller)
            np.testing.assert_array_equal(fallback_stripe.pixels(), stripe.pixels())
        else:
            assert palette == "rainbow"
        if rendered:
            np.testing.assert_array_equal(destination, stripe.pixels())
        np.testing.assert_allclose(native.levels, python.levels, rtol=1e-6, atol=1e-7)
        np.testing.assert_allclose(native.streak_positions, python.streak_positions)


@pytest.mark.skipif(
    not kernels.native_electric_storm_available(),
    reason="render extension with ElectricStorm kernel is not built",
)
@pytest.mark.parametrize("audio_mode", [False, True])
@pytest.mark.parametrize("length", [1, 150, 300, 600])
@pytest.mark.parametrize("palette", ["rainbow", "neon", "fire", "custom"])
def test_native_electric_storm_matches_python(length: int, audio_mode: bool, palette: str) -> None:
    native, python = ElectricStorm(), ElectricStorm()
    settings = {"count": 5, "flash_rate": 68, "decay": 47, "music_reactivity": 100, "sensitivity": 50, "palette": palette, "custom_color": "#2EA7D4"}
    native.configure(settings)
    python.configure(settings)
    native._rng.seed(9173)
    python._rng.seed(9173)
    destination = np.empty((length, 4), dtype=np.uint8)
    stripe = Stripe(length)
    audio = AudioFrame(rms=0.62, bands=(0.7, 0.6, 0.5, 0.4, 0.5, 0.6, 0.7, 0.8), beat=True, beat_strength=0.9)
    for frame in range(24):
        configured = native.effective_audio(audio) if audio_mode else None
        rendered = native._render_native(frame, destination, configured, 0.72)
        stripe.fill(Rgba(0, 0, 0, 0.0))
        controller = python.output_controller(stripe, 0.72)
        if audio_mode:
            python.tick_audio(frame, controller, python.effective_audio(audio))
        else:
            python.tick(frame, controller)
        if not rendered:
            fallback_stripe = Stripe(length)
            fallback_controller = native.output_controller(fallback_stripe, 0.72)
            if audio_mode:
                native.tick_audio(frame, fallback_controller, native.effective_audio(audio))
            else:
                native.tick(frame, fallback_controller)
            np.testing.assert_array_equal(fallback_stripe.pixels(), stripe.pixels())
        else:
            assert palette == "rainbow"
        if rendered:
            np.testing.assert_array_equal(destination, stripe.pixels())
        assert native.flash.value == pytest.approx(python.flash.value)
        np.testing.assert_allclose(
            [(s.pos, s.length, s.speed, s.hue, s.alpha.value) for s in native.streaks],
            [(s.pos, s.length, s.speed, s.hue, s.alpha.value) for s in python.streaks],
            rtol=1e-6,
            atol=1e-12,
        )
