from lumistripe import AnimationPlayer, AudioFrame, GPIOStripe, Stripe


def test_package_exports_are_importable() -> None:
    stripe = Stripe(4)
    player = AnimationPlayer.party()
    frame = AudioFrame()

    assert stripe.length == 4
    assert player.index_of("pulse") is not None
    assert frame.bands == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert GPIOStripe is not None


def test_animation_star_import_exports_reactive_helpers() -> None:
    namespace: dict[str, object] = {}
    exec("from lumistripe.animation import *", namespace, namespace)

    assert "AudioReactive" in namespace
    assert "Decay" in namespace
