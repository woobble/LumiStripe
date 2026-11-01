"""Compare Python, native-C, and engine-owned Rust animation frame rendering."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import time
import tracemalloc
from pathlib import Path
from typing import Any

from lumistripe.animation.catalog import party_catalog  # type: ignore[import-untyped]
from lumistripe.audio import AudioFrame  # type: ignore[import-untyped]
from lumistripe.render import native_render_neon_enabled  # type: ignore[import-untyped]
from lumistripe.stripe import Stripe  # type: ignore[import-untyped]


def _percentile(samples_ns: list[int], quantile: float) -> int:
    ordered = sorted(samples_ns)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * quantile + 0.999999) - 1))
    return ordered[index]


def _metrics(samples_ns: list[int]) -> dict[str, float | int]:
    return {
        "mean_frame_ns": round(statistics.fmean(samples_ns), 1),
        "p50_frame_ns": _percentile(samples_ns, 0.50),
        "p95_frame_ns": _percentile(samples_ns, 0.95),
        "p99_frame_ns": _percentile(samples_ns, 0.99),
    }


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


def _audio(frame: int) -> AudioFrame:
    beat = frame % 8 == 0
    return AudioFrame(
        rms=0.44,
        bands=(0.82, 0.64, 0.48, 0.31, 0.57, 0.26, 0.41, 0.19),
        beat=beat,
        beat_strength=0.91 if beat else 0.12,
        sequence=frame + 1,
        timestamp=frame * 0.02,
        fresh=True,
    )


def _run_python_renderer(
    name: str,
    settings: dict[str, object],
    pixels: int,
    outputs: int,
    frames: int,
    warmup: int,
    audio_enabled: bool,
    *,
    native: bool,
    trace_allocations: bool,
) -> dict[str, Any]:
    definition = next(item for item in party_catalog() if item.name == name)
    animations = []
    stripes = []
    destinations = []
    for output_index in range(outputs):
        animation = definition.create(settings)
        if hasattr(animation, "_rng"):
            animation._rng.seed(12_345 + output_index)
        stripe = Stripe(pixels)
        animations.append(animation)
        stripes.append(stripe)
        destinations.append(stripe.pixels())
    brightness = 0.73
    samples_ns: list[int] = []
    traced_current_delta = None
    traced_peak_bytes = None

    def render(frame: int) -> None:
        audio_frame = _audio(frame) if audio_enabled else None
        for animation, stripe, destination in zip(
            animations, stripes, destinations, strict=True
        ):
            audio = (
                animation.effective_audio(audio_frame)
                if audio_frame is not None
                else None
            )
            effective_frame = animation.effective_frame(frame)
            if native:
                if not animation._render_native(
                    effective_frame, destination, audio, brightness
                ):
                    raise RuntimeError("native renderer is unavailable")
                continue
            controller = animation.output_controller(stripe, brightness)
            if audio is None:
                animation.tick(effective_frame, controller)
            else:
                animation.tick_audio(effective_frame, controller, audio)

    for frame in range(warmup):
        render(frame)
    for frame in range(warmup, warmup + frames):
        started = time.perf_counter_ns()
        render(frame)
        samples_ns.append(time.perf_counter_ns() - started)
    if trace_allocations:
        tracemalloc.start()
        try:
            before_current, _ = tracemalloc.get_traced_memory()
            for frame in range(warmup + frames, warmup + 2 * frames):
                render(frame)
            current, peak = tracemalloc.get_traced_memory()
            traced_current_delta = current - before_current
            traced_peak_bytes = max(0, peak - before_current)
        finally:
            tracemalloc.stop()
    return {
        "renderer": "native-c" if native else "python",
        "animation": name,
        "outputs": outputs,
        "pixels": pixels,
        "total_pixels": outputs * pixels,
        "frames": frames,
        "warmup_frames": warmup,
        "audio": audio_enabled,
        "allocation_sample_frames": frames if trace_allocations else None,
        **_metrics(samples_ns),
        "tracemalloc_retained_bytes_delta": traced_current_delta,
        "tracemalloc_peak_bytes": traced_peak_bytes,
    }


def _run_rust_renderer(
    binary: Path,
    name: str,
    settings: dict[str, object],
    pixels: int,
    outputs: int,
    frames: int,
    warmup: int,
    audio_enabled: bool,
    effects: list[dict[str, object]],
    base_intensity: float,
    max_overlay_strength: float,
) -> dict[str, Any]:
    command = [
        str(binary),
        "--animation",
        name,
        "--settings",
        json.dumps(settings, separators=(",", ":")),
        "--pixels",
        str(pixels),
        "--outputs",
        str(outputs),
        "--frames",
        str(frames),
        "--warmup",
        str(warmup),
        "--effects",
        json.dumps(effects, separators=(",", ":")),
        "--base-intensity",
        str(base_intensity),
        "--max-overlay-strength",
        str(max_overlay_strength),
    ]
    if audio_enabled:
        command.append("--audio")
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    measured = json.loads(result.stdout)
    measured["binary"] = str(binary)
    return measured


def _binary_spec(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("Rust binaries must use NAME=PATH format")
    return name, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--animation", default="rainbow")
    parser.add_argument("--settings", default="{}", help="JSON settings object")
    parser.add_argument("--pixels", type=int, default=80)
    parser.add_argument(
        "--outputs",
        type=int,
        default=1,
        help="independent output count for multi-strip timing",
    )
    parser.add_argument("--frames", type=int, default=2_000)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--audio", action="store_true")
    parser.add_argument(
        "--effects",
        default="[]",
        help="Rust-only JSON array of effect objects with name, settings, and intensity",
    )
    parser.add_argument("--base-intensity", type=float, default=1.0)
    parser.add_argument("--max-overlay-strength", type=float, default=0.65)
    parser.add_argument("--python", action="store_true", help="measure the Python fallback")
    parser.add_argument("--native-c", action="store_true", help="measure the configured C kernel")
    parser.add_argument(
        "--rust-binary",
        action="append",
        type=_binary_spec,
        default=[],
        metavar="NAME=PATH",
        help="benchmark a Rust example binary; repeat for scalar, LLVM, or NEON builds",
    )
    parser.add_argument("--trace-allocations", action="store_true")
    args = parser.parse_args()
    if args.pixels <= 0 or args.outputs <= 0 or args.frames <= 0 or args.warmup < 0:
        parser.error("pixels, outputs, and frames must be positive; warmup must be non-negative")
    try:
        settings = json.loads(args.settings)
    except json.JSONDecodeError as exc:
        parser.error(f"--settings must be valid JSON: {exc}")
    if not isinstance(settings, dict):
        parser.error("--settings must be a JSON object")
    try:
        effects = json.loads(args.effects)
    except json.JSONDecodeError as exc:
        parser.error(f"--effects must be valid JSON: {exc}")
    if not isinstance(effects, list) or any(not isinstance(effect, dict) for effect in effects):
        parser.error("--effects must be a JSON array of effect objects")
    if effects and (args.python or args.native_c):
        parser.error("--effects compares the Rust layer path; omit --python and --native-c")
    if not (args.python or args.native_c or args.rust_binary):
        parser.error("select --python, --native-c, and/or at least one --rust-binary")

    results = []
    for native, enabled in ((False, args.python), (True, args.native_c)):
        if not enabled:
            continue
        try:
            results.append(
                _run_python_renderer(
                    args.animation,
                    settings,
                    args.pixels,
                    args.outputs,
                    args.frames,
                    args.warmup,
                    args.audio,
                    native=native,
                    trace_allocations=args.trace_allocations,
                )
            )
        except (RuntimeError, ImportError) as exc:
            results.append(
                {"renderer": "native-c" if native else "python", "available": False, "reason": str(exc)}
            )
    for build_name, binary in args.rust_binary:
        if not binary.is_file():
            parser.error(f"Rust benchmark binary does not exist: {binary}")
        result = _run_rust_renderer(
            binary,
            args.animation,
            settings,
            args.pixels,
            args.outputs,
            args.frames,
            args.warmup,
            args.audio,
            effects,
            args.base_intensity,
            args.max_overlay_strength,
        )
        result["build"] = build_name
        results.append(result)

    print(
        json.dumps(
            {
                "machine": {
                    "architecture": platform.machine(),
                    "processor": _processor_name(),
                    "system": platform.system(),
                    "release": platform.release(),
                    "python": platform.python_version(),
                },
                "native_c_neon_enabled": (
                    native_render_neon_enabled() if args.native_c else None
                ),
                "animation": args.animation,
                "settings": settings,
                "effects": effects,
                "outputs": args.outputs,
                "pixels_per_output": args.pixels,
                "total_pixels": args.outputs * args.pixels,
                "base_intensity": args.base_intensity,
                "max_overlay_strength": args.max_overlay_strength,
                "results": results,
                "notes": [
                    "Rust binaries are measured in-process; Python and native-C timings include their direct renderer calls.",
                    "When effects are specified, only the Rust layer path is measured because Python and C options render the base animation only.",
                    "Build separate Rust binaries with default release flags and with -C no-vectorize-loops -C no-vectorize-slp for the LLVM comparison.",
                    "On AArch64, build the render extension on the target with LUMISTRIPE_NATIVE_OPTIMIZATION=1 to measure its native-CPU compiler profile.",
                    "Tracemalloc reports retained and peak live Python bytes during a separate allocation pass; it does not count total allocations or C allocations.",
                    "Rust reports total allocator calls and bytes during its timing pass.",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
