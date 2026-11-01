"""Export Python output-correction and power-budget reference frames for Rust."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from lumistripe.power import (
    OutputPowerEstimate,
    apply_power_budget,
    estimate_frame_power,
)
from lumistripe.render.kernels import apply_alpha_scale_into, correct_rgba_into

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "rust/crates/lumistripe-engine-runtime/golden_power_finalization.json"

CASES = (
    {
        "name": "corrected_per_output_and_global_limit",
        "power": {"enabled": True, "budget_watts": 0.05},
        "outputs": [
            {
                "animation": "rainbow",
                "color_correction": [128, 255, 64],
                "voltage_v": 5.0,
                "full_white_current_a": 0.06,
                "power_limit_watts": 0.04,
                "blackout": False,
                "frames": [[255, 128, 64, 255]],
            }
        ],
    },
    {
        "name": "blackout_excluded_from_power_estimate",
        "power": {"enabled": True, "budget_watts": 0.1},
        "outputs": [
            {
                "animation": "rainbow",
                "color_correction": [255, 255, 255],
                "voltage_v": 5.0,
                "full_white_current_a": 0.06,
                "power_limit_watts": None,
                "blackout": False,
                "frames": [[255, 255, 255, 255]],
            },
            {
                "animation": "rainbow",
                "color_correction": [255, 255, 255],
                "voltage_v": 5.0,
                "full_white_current_a": 0.06,
                "power_limit_watts": None,
                "blackout": True,
                "frames": [[255, 255, 255, 255]],
            },
        ],
    },
)


def _render_case(case: dict[str, object]) -> dict[str, object]:
    power = case["power"]
    outputs = case["outputs"]
    assert isinstance(power, dict) and isinstance(outputs, list)
    estimates: list[OutputPowerEstimate] = []
    sources: list[np.ndarray] = []
    corrected: list[np.ndarray] = []
    corrections: list[np.ndarray] = []

    for index, output in enumerate(outputs):
        assert isinstance(output, dict)
        source = np.asarray(output["frames"], dtype=np.uint8)
        correction = np.asarray(output["color_correction"], dtype=np.uint16)
        corrected_frame = np.empty_like(source)
        correct_rgba_into(
            source,
            corrected_frame,
            correction,
            np.empty((len(source), 3), dtype=np.uint16),
        )
        blackout = output["blackout"]
        watts = (
            0.0
            if blackout
            else estimate_frame_power(
                corrected_frame,
                voltage_v=output["voltage_v"],
                full_white_current_a=output["full_white_current_a"],
            )
        )
        estimates.append(
            OutputPowerEstimate(
                output_id=str(index),
                watts=watts,
                limit_watts=output["power_limit_watts"],
            )
        )
        sources.append(source)
        corrected.append(corrected_frame)
        corrections.append(correction)

    result = apply_power_budget(
        estimates,
        enabled=power["enabled"],
        budget_watts=power["budget_watts"],
    )
    rendered: list[list[list[int]]] = []
    for output, source, corrected_frame, correction in zip(
        outputs, sources, corrected, corrections, strict=True
    ):
        scaled = np.empty_like(source)
        apply_alpha_scale_into(
            source,
            corrected_frame,
            scaled,
            result.applied_scale,
            np.empty((len(source),), dtype=np.float64),
        )
        final = np.empty_like(source)
        correct_rgba_into(
            scaled,
            final,
            correction,
            np.empty((len(source), 3), dtype=np.uint16),
        )
        if output["blackout"]:
            final.fill(0)
        rendered.append(final.tolist())

    return {
        "name": case["name"],
        "power": power,
        "outputs": [
            {
                key: output[key]
                for key in (
                    "animation",
                    "color_correction",
                    "voltage_v",
                    "full_white_current_a",
                    "power_limit_watts",
                    "blackout",
                )
            }
            for output in outputs
        ],
        "input_frames": [output["frames"] for output in outputs],
        "estimated_watts": result.estimated_watts,
        "applied_scale": result.applied_scale,
        "output_watts": [estimate.watts for estimate in result.outputs],
        "limiting_output_index": next(
            (
                index
                for index, estimate in enumerate(result.outputs)
                if estimate.output_id == result.limiting_output_id
            ),
            None,
        ),
        "output_frames": rendered,
    }


def golden_payload() -> dict[str, object]:
    return {"schema_version": 1, "cases": [_render_case(case) for case in CASES]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = json.dumps(golden_payload(), indent=2, sort_keys=True) + "\n"
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
