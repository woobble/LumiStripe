from lumistripe import (
    AnimationPlayer,
    AudioFrame,
    AudioSource,
    CompositeController,
    CyclingConfig,
    DynamicSelector,
    GPIOStripe,
    PlaybackEngine,
    PlaybackMode,
    ReversedController,
    SPIConfig,
    SPIStripe,
    Stripe,
)


def test_package_exports_are_importable() -> None:
    stripe = Stripe(4)
    player = AnimationPlayer.party()
    frame = AudioFrame()

    assert stripe.length == 4
    assert player.index_of("pulse") is not None
    assert frame.bands == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert GPIOStripe is not None
    assert SPIConfig().device == "/dev/spidev0.0"
    assert SPIStripe is not None
    assert CompositeController is not None
    assert ReversedController is not None
    assert PlaybackEngine is not None
    assert PlaybackMode.STATIC.value == "static"
    assert AudioSource.DEMO.value == "demo"
    assert CyclingConfig().interval_s == 30.0
    assert DynamicSelector is not None


def test_animation_star_import_exports_reactive_helpers() -> None:
    namespace: dict[str, object] = {}
    exec("from lumistripe.animation import *", namespace, namespace)  # noqa: S102

    assert "AudioReactive" in namespace
    assert "Decay" in namespace


def test_effects_package_exports_effects_and_layering_api() -> None:
    namespace: dict[str, object] = {}
    exec("from lumistripe.effects import *", namespace, namespace)  # noqa: S102

    assert "BeatWave" in namespace
    assert "Effect" in namespace
    assert "EffectScheduler" in namespace
    assert "EffectSchedulerDiagnostics" in namespace
