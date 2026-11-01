"""Export Python animation metadata for the Rust renderer's typed catalog."""

from __future__ import annotations

import argparse
import json
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from lumistripe.animation.catalog import party_catalog
from lumistripe.effects.definitions import ACCENT_EFFECTS, RHYTHMIC_EFFECTS

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "rust/crates/lumistripe-engine-animation-definitions/catalog.json"


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    return value


def catalog_payload() -> dict[str, Any]:
    animations = []
    for definition in party_catalog():
        animations.append(
            {
                "name": definition.name,
                "renderer": type(definition.factory()).__name__,
                "frame_ms": definition.frame_ms,
                "frames_per_cycle": definition.frames_per_cycle,
                "automatic": definition.automatic,
                "display_name": definition.display_name,
                "role": _plain(definition.role),
                "metadata": _plain(definition.metadata),
                "dynamic_metadata": _plain(definition.dynamic_metadata),
                "effect_metadata": _plain(definition.effect_metadata),
                "settings": [_plain(setting) for setting in definition.settings],
            }
        )
    effects = [_plain(effect) for effect in (*RHYTHMIC_EFFECTS, *ACCENT_EFFECTS)]
    return {"schema_version": 1, "animations": animations, "effects": effects}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = json.dumps(catalog_payload(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != generated:
            print(f"{OUTPUT.relative_to(ROOT)} is stale; run this script to regenerate it")
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
