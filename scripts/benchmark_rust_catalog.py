"""Measure steady-state Rust rendering across the complete generated catalog."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = (
    REPOSITORY_ROOT
    / "rust"
    / "crates"
    / "lumistripe-engine-animation-definitions"
    / "catalog.json"
)


def _binary_spec(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("use NAME=PATH for each Rust binary")
    return name, Path(path)


def _processor_name() -> str:
    processor = platform.processor()
    if processor:
        return processor
    if platform.system() == "Linux":
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.is_file():
            for line in cpuinfo.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().lower() in {"model name", "hardware"}:
                    return value.strip()
    return platform.machine()


def _measure(
    binary: Path,
    *,
    animation: str,
    role: str,
    pixels: int,
    frames: int,
    warmup: int,
    audio: bool,
) -> dict[str, Any]:
    command = [
        str(binary),
        "--animation",
        animation,
        "--pixels",
        str(pixels),
        "--frames",
        str(frames),
        "--warmup",
        str(warmup),
    ]
    if audio:
        command.append("--audio")
    if role == "effect_layer":
        command.extend(
            [
                "--effects",
                json.dumps([{"name": animation, "settings": {}, "intensity": 0.75}]),
            ]
        )
        command[command.index("--animation") + 1] = "rainbow"
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    measured = json.loads(completed.stdout)
    return {
        "role": role,
        "name": animation,
        "pixels": pixels,
        "frames": frames,
        "warmup_frames": warmup,
        "audio": audio,
        "mean_frame_ns": measured["mean_frame_ns"],
        "p50_frame_ns": measured["p50_frame_ns"],
        "p95_frame_ns": measured["p95_frame_ns"],
        "p99_frame_ns": measured["p99_frame_ns"],
        "allocations_per_frame": measured["allocations_per_frame"],
        "allocated_bytes_per_frame": measured["allocated_bytes_per_frame"],
    }


def _summary(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for result in results:
        build = result["build"]
        summary = summaries.setdefault(
            build,
            {
                "cases": 0,
                "nonzero_allocation_cases": 0,
                "max_allocations_per_frame": 0.0,
                "max_allocated_bytes_per_frame": 0.0,
            },
        )
        summary["cases"] += 1
        allocations = result["allocations_per_frame"]
        allocated_bytes = result["allocated_bytes_per_frame"]
        if allocations != 0 or allocated_bytes != 0:
            summary["nonzero_allocation_cases"] += 1
        summary["max_allocations_per_frame"] = max(
            summary["max_allocations_per_frame"], allocations
        )
        summary["max_allocated_bytes_per_frame"] = max(
            summary["max_allocated_bytes_per_frame"], allocated_bytes
        )
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark every generated Rust animation and effect layer."
    )
    parser.add_argument(
        "--rust-binary",
        action="append",
        type=_binary_spec,
        required=True,
        metavar="NAME=PATH",
        help="benchmark binary built with the requested compiler profile; repeatable",
    )
    parser.add_argument("--pixels", type=int, default=300)
    parser.add_argument("--frames", type=int, default=2_000)
    parser.add_argument("--warmup", type=int, default=250)
    parser.add_argument("--audio", action="store_true")
    parser.add_argument("--fail-on-allocation", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.pixels <= 0 or args.frames <= 0 or args.warmup < 0:
        parser.error("pixels and frames must be positive; warmup must be non-negative")
    binary_names = [name for name, _binary in args.rust_binary]
    if len(set(binary_names)) != len(binary_names):
        parser.error("Rust binary names must be unique")
    for name, binary in args.rust_binary:
        if not binary.is_file():
            parser.error(f"Rust benchmark binary {name!r} does not exist: {binary}")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    animations = [item["metadata"]["name"] for item in catalog["animations"]]
    effects = sorted(
        {
            item["effect_metadata"]["name"]
            for item in catalog["animations"]
            if item["effect_metadata"] is not None
        }
    )
    if len(animations) != 54 or len(effects) != 19:
        parser.error(
            f"generated catalog must contain 54 animations and 19 effects; "
            f"found {len(animations)} and {len(effects)}"
        )

    results = []
    for build, binary in args.rust_binary:
        for animation in animations:
            result = _measure(
                binary,
                animation=animation,
                role="animation",
                pixels=args.pixels,
                frames=args.frames,
                warmup=args.warmup,
                audio=args.audio,
            )
            result["build"] = build
            results.append(result)
        for effect in effects:
            result = _measure(
                binary,
                animation=effect,
                role="effect_layer",
                pixels=args.pixels,
                frames=args.frames,
                warmup=args.warmup,
                audio=args.audio,
            )
            result["build"] = build
            results.append(result)

    report = {
        "machine": {
            "architecture": platform.machine(),
            "processor": _processor_name(),
            "system": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
        },
        "animation_count": len(animations),
        "effect_count": len(effects),
        "results": results,
        "summary": _summary(results),
    }
    if args.output is not None:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    else:
        print(json.dumps(report, indent=2))

    if args.fail_on_allocation and any(
        result["allocations_per_frame"] != 0
        or result["allocated_bytes_per_frame"] != 0
        for result in results
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
