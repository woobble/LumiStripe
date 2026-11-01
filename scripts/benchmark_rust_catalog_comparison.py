"""Compare Python, native-C, and Rust renderer profiles across the catalog."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

from benchmark_rust_catalog import CATALOG_PATH

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RENDERER_BENCHMARK = REPOSITORY_ROOT / "scripts" / "benchmark_rust_renderers.py"


def _binary_spec(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("use NAME=PATH for each Rust binary")
    return name, Path(path)


def _summary(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result["build"], []).append(result)

    summary: dict[str, dict[str, Any]] = {}
    for build, cases in grouped.items():
        available = [case for case in cases if case.get("available", True)]
        latencies = [case["p95_frame_ns"] for case in available if "p95_frame_ns" in case]
        profile_summary: dict[str, Any] = {
            "cases": len(cases),
            "available_cases": len(available),
            "unavailable_cases": len(cases) - len(available),
            "median_p95_frame_ns": (
                statistics.median(latencies) if latencies else None
            ),
            "max_p95_frame_ns": max(latencies) if latencies else None,
        }
        allocation_cases = [case for case in available if "allocations_per_frame" in case]
        if allocation_cases:
            profile_summary["nonzero_allocation_cases"] = sum(
                case["allocations_per_frame"] != 0
                or case["allocated_bytes_per_frame"] != 0
                for case in allocation_cases
            )
            profile_summary["max_allocations_per_frame"] = max(
                case["allocations_per_frame"] for case in allocation_cases
            )
            profile_summary["max_allocated_bytes_per_frame"] = max(
                case["allocated_bytes_per_frame"] for case in allocation_cases
            )
        summary[build] = profile_summary
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rust-binary",
        action="append",
        type=_binary_spec,
        default=[],
        metavar="NAME=PATH",
        help="Rust profile binary; repeat for LLVM, scalar, or target-native builds",
    )
    parser.add_argument("--python", action="store_true", help="include the Python renderer")
    parser.add_argument("--native-c", action="store_true", help="include available C kernels")
    parser.add_argument("--animation", action="append", help="limit to named animations")
    parser.add_argument("--pixels", type=int, default=300)
    parser.add_argument("--outputs", type=int, default=1)
    parser.add_argument("--frames", type=int, default=2_000)
    parser.add_argument("--warmup", type=int, default=250)
    parser.add_argument("--audio", action="store_true")
    parser.add_argument("--trace-allocations", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not (args.python or args.native_c or args.rust_binary):
        parser.error("select --python, --native-c, and/or at least one --rust-binary")
    if args.pixels <= 0 or args.outputs <= 0 or args.frames <= 0 or args.warmup < 0:
        parser.error("pixels, outputs, and frames must be positive; warmup must be non-negative")
    binary_names = [name for name, _binary in args.rust_binary]
    if len(set(binary_names)) != len(binary_names):
        parser.error("Rust binary names must be unique")
    for name, binary in args.rust_binary:
        if not binary.is_file():
            parser.error(f"Rust benchmark binary {name!r} does not exist: {binary}")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    available_animations = [item["metadata"]["name"] for item in catalog["animations"]]
    if len(available_animations) != 54:
        parser.error(f"generated catalog must contain 54 animations; found {len(available_animations)}")
    animations = args.animation or available_animations
    unknown = sorted(set(animations) - set(available_animations))
    if unknown:
        parser.error(f"unknown animation names: {', '.join(unknown)}")
    if len(set(animations)) != len(animations):
        parser.error("animation names must be unique")

    results: list[dict[str, Any]] = []
    machine: dict[str, Any] | None = None
    native_c_neon_enabled: bool | None = None
    for animation in animations:
        command = [
            sys.executable,
            str(RENDERER_BENCHMARK),
            "--animation",
            animation,
            "--pixels",
            str(args.pixels),
            "--outputs",
            str(args.outputs),
            "--frames",
            str(args.frames),
            "--warmup",
            str(args.warmup),
        ]
        if args.audio:
            command.append("--audio")
        if args.python:
            command.append("--python")
        if args.native_c:
            command.append("--native-c")
        if args.trace_allocations:
            command.append("--trace-allocations")
        for build, binary in args.rust_binary:
            command.extend(["--rust-binary", f"{build}={binary}"])

        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        comparison = json.loads(completed.stdout)
        machine = machine or comparison["machine"]
        if args.native_c:
            native_c_neon_enabled = comparison["native_c_neon_enabled"]
        for result in comparison["results"]:
            result["role"] = "animation"
            result["name"] = animation
            result.setdefault("build", result.get("renderer", "unknown"))
            results.append(result)
        print(f"Benchmarked {animation}", flush=True)

    report = {
        "machine": machine,
        "native_c_neon_enabled": native_c_neon_enabled,
        "animation_count": len(animations),
        "pixels_per_output": args.pixels,
        "outputs": args.outputs,
        "frames": args.frames,
        "warmup_frames": args.warmup,
        "audio": args.audio,
        "results": results,
        "summary": _summary(results),
    }
    generated = json.dumps(report, indent=2) + "\n"
    if args.output is None:
        print(generated, end="")
    else:
        args.output.write_text(generated, encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))


if __name__ == "__main__":
    main()
