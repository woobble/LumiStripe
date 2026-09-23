import pytest
from lumistripe.animation.base import Animation
from lumistripe.animation.catalog import (
    AnimationDefinition,
    animation_catalog,
    validate_animation_catalog,
)
from lumistripe.selector import animation_metadata


def test_party_catalog_is_unique_and_timed() -> None:
    catalog = animation_catalog()

    assert len(catalog) >= 40
    assert len({definition.name for definition in catalog}) == len(catalog)
    assert all(definition.metadata.name == definition.name for definition in catalog)


def test_catalog_rejects_duplicate_names() -> None:
    class Example(Animation):
        name = "example"

        def tick(self, frame, controller):
            del frame, controller

    definition = AnimationDefinition(
        name="duplicate",
        factory=Example,
        frame_ms=10,
        frames_per_cycle=10,
        automatic=True,
        metadata=animation_metadata("duplicate"),
    )
    with pytest.raises(ValueError, match="unique"):
        validate_animation_catalog((definition, definition))
