from __future__ import annotations

import numpy as np
import pytest
from lumistripe.animation.catalog import AnimationDefinition, party_catalog
from lumistripe.audio import AudioFrame
from lumistripe.color import Rgba
from lumistripe.render import NATIVE_AVAILABLE
from lumistripe.stripe import Stripe


def _catalog_cases() -> list[tuple[AnimationDefinition, str | None]]:
    cases = []
    for definition in party_catalog():
        palette_setting = next(
            (setting for setting in definition.settings if setting.id == "palette"),
            None,
        )
        if palette_setting is None:
            cases.append((definition, None))
        else:
            cases.extend((definition, palette) for palette in palette_setting.choices)
    return cases


@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="render extension is not built")
@pytest.mark.parametrize(
    ("definition", "palette"),
    _catalog_cases(),
    ids=lambda item: item.name if isinstance(item, AnimationDefinition) else str(item),
)
@pytest.mark.parametrize("length", [1, 30, 60, 150, 300, 600, 1200])
@pytest.mark.parametrize("audio_mode", [False, True])
def test_party_catalog_native_pixels_match_python(
    definition: AnimationDefinition, palette: str | None, length: int, audio_mode: bool
) -> None:
    settings = definition.normalize_settings(
        {"palette": palette} if palette is not None else None,
        allow_partial=True,
    )
    native = definition.create(settings)
    python = definition.create(settings)
    for animation in (native, python):
        rng = getattr(animation, "_rng", None)
        if rng is not None and hasattr(rng, "seed"):
            rng.seed(0x4C554D49)

    frame_offset = 37 if audio_mode else 24
    native_pixels = Stripe(length).pixels()
    assert native.native_render_enabled
    python_buffer = Stripe(length)
    controller = python.output_controller(python_buffer, 0.73)
    for step in range(12):
        frame = frame_offset + step
        audio = AudioFrame(
            rms=0.42 + (step % 4) * 0.13,
            bands=(0.82, 0.77, 0.68, 0.56, 0.48, 0.61, 0.76, 0.88),
            beat=step % 4 == 0,
            beat_strength=0.93 if step % 4 == 0 else 0.0,
        )
        native_audio = native.effective_audio(audio) if audio_mode else None
        used_native = native._render_native(frame, native_pixels, native_audio, 0.73)
        if not used_native and palette is not None:
            pytest.skip(
                f"{definition.name} falls back to Python for palette {palette!r}"
            )
        assert used_native, f"{definition.name} did not use its native renderer"

        python_audio = python.effective_audio(audio) if audio_mode else None
        if definition.effect_metadata is not None:
            python_buffer.fill(Rgba(0, 0, 0, 0.0))
        if python_audio is None:
            python.tick(frame, controller)
        else:
            python.tick_audio(frame, controller, python_audio)

        np.testing.assert_array_equal(native_pixels, python_buffer.pixels())
