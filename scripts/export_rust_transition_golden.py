"""Export Python golden frames for Rust scene transition parity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from lumistripe import AnimationPlayer, Stripe, TransitionEffect  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "rust/crates/lumistripe-engine-compositing/golden_transitions.json"
PIXEL_COUNT = 80
AMOUNTS = (0.25, 0.5, 0.75)
MODES = tuple(effect.value for effect in TransitionEffect)


def source_and_target() -> tuple[np.ndarray, np.ndarray]:
    source = np.array(
        [
            ((index * 31 + 11) % 256, (index * 17 + 23) % 256,
             (index * 9 + 77) % 256, (index * 13 + 100) % 256)
            for index in range(PIXEL_COUNT)
        ],
        dtype=np.uint8,
    )
    target = np.array(
        [
            ((index * 7 + 201) % 256, (index * 29 + 5) % 256,
             (index * 19 + 43) % 256, (index * 11 + 20) % 256)
            for index in range(PIXEL_COUNT)
        ],
        dtype=np.uint8,
    )
    return source, target


def generate() -> dict[str, Any]:
    source, target = source_and_target()
    cases: list[dict[str, object]] = []
    for mode in MODES:
        for amount in AMOUNTS:
            player = AnimationPlayer()
            player.begin_transition(
                duration_ms=1000,
                source=source,
                now_s=0.0,
                effect=TransitionEffect(mode),
            )
            controller = Stripe(PIXEL_COUNT)
            player.apply_transition(
                controller,
                now_s=amount,
                target_pixels=target,
            )
            cases.append(
                {
                    "mode": mode,
                    "amount": amount,
                    "expected_hex": controller.pixels().tobytes().hex(),
                }
            )
    return {
        "schema_version": 1,
        "pixel_count": PIXEL_COUNT,
        "source_hex": source.tobytes().hex(),
        "target_hex": target.tobytes().hex(),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = json.dumps(generate(), indent=2) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text() != generated:
            raise SystemExit(f"{OUTPUT.relative_to(ROOT)} is stale; rerun this script")
        return
    OUTPUT.write_text(generated)


if __name__ == "__main__":
    main()
