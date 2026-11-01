"""Compare Python SPI output with compact Rust IPC and mock SPI encoding."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from math import ceil
from pathlib import Path

import numpy as np
from lumistripe.gpio.spi import SPIConfig, SPIStripe
from lumistripe_web.runtime.rust_engine import RustEngineProcess, RustOutputSpec


class _MockSpiDevice:
    """Discard SPI payloads while preserving the Python encoder call path."""

    mode = 0
    max_speed_hz = 0
    bits_per_word = 0
    lsbfirst = False
    no_cs = False

    def __init__(self) -> None:
        self.transfers = 0
        self.bytes_written = 0

    def open_path(self, _path: str) -> None:
        pass

    def writebytes2(self, values: np.ndarray) -> None:
        self.transfers += 1
        self.bytes_written += values.nbytes

    def close(self) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, help="Path to lumistripe-engine")
    parser.add_argument("--pixels", type=_positive_int, default=80)
    parser.add_argument("--outputs", type=_positive_int, choices=(1, 2), default=2)
    parser.add_argument("--frames", type=_positive_int, default=500)
    parser.add_argument("--warmup", type=int, default=30)
    parser.add_argument(
        "--require-c-encoder",
        action="store_true",
        help="fail unless the optional native SM16716 encoder is installed",
    )
    args = parser.parse_args()
    if args.warmup < 0:
        parser.error("--warmup must be zero or greater")

    output_lengths = (args.pixels,) * args.outputs
    python_outputs = tuple(_make_python_output(length) for length in output_lengths)
    c_encoder_available = all(
        output._encoder._native_encode_into is not None for output in python_outputs
    )
    if args.require_c_encoder and not c_encoder_available:
        for output in python_outputs:
            output.close()
        parser.error(
            "the native SM16716 encoder is unavailable; build lumistripe-core with the spi profile"
        )
    rust_specs = tuple(
        RustOutputSpec(f"strip-{index}", length, False, "mock_spi")
        for index, length in enumerate(output_lengths)
    )
    frames = tuple(_make_frame(length, 0) for length in output_lengths)

    try:
        python_samples = _benchmark_python(
            python_outputs, frames, args.warmup, args.frames
        )
    finally:
        for output in python_outputs:
            output.close()
    rust_samples, rust_stats = _benchmark_rust(
        args.binary, rust_specs, frames, args.warmup, args.frames
    )
    python_metrics = _metrics(python_samples)
    rust_metrics = _metrics(rust_samples)
    print(
        json.dumps(
            {
                "outputs": args.outputs,
                "pixels_per_output": args.pixels,
                "frames_submitted": args.frames,
                "payload_bytes_per_frame": sum(output_lengths) * 4,
                "python_spi_mock_write": python_metrics,
                "python_native_c_encoder": c_encoder_available,
                "rust_binary_ipc_ack": rust_metrics,
                "rust_mock_spi_encoder": True,
                "rust_frames_transmitted": rust_stats["frames_rendered"],
                "rust_frames_dropped": rust_stats["dropped_frames"],
                "rust_frames_failed": rust_stats["failed_frames"],
                "rust_output_worker_latency": {
                    "frame_latency_ms": rust_stats["frame_latency_ms"],
                    "frame_latency_p50_ms": rust_stats["frame_latency_p50_ms"],
                    "frame_latency_p95_ms": rust_stats["frame_latency_p95_ms"],
                    "frame_latency_p99_ms": rust_stats["frame_latency_p99_ms"],
                    "queue_latency_ms": rust_stats["queue_latency_ms"],
                    "transmission_ms": rust_stats["transmission_ms"],
                },
                "scope": "Python encoder with mock spidev and Rust IPC/output worker with mock SPI encoding; physical SPI/GPIO timing is not measured",
            },
            indent=2,
        )
    )


def _benchmark_python(
    outputs: tuple[SPIStripe, ...],
    frames: tuple[np.ndarray, ...],
    warmup: int,
    count: int,
) -> list[float]:
    samples_ms: list[float] = []
    for sequence in range(warmup + count):
        current = tuple(_mutate_frame(frame, sequence) for frame in frames)
        started_ns = time.perf_counter_ns()
        for output, frame in zip(outputs, current, strict=True):
            output.set_pixels(frame)
            output.force_flush()
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        if sequence >= warmup:
            samples_ms.append(elapsed_ms)
    return samples_ms


def _make_python_output(pixel_count: int) -> SPIStripe:
    mock_device = _MockSpiDevice()
    return SPIStripe(
        SPIConfig(
            device="mock://benchmark",
            max_transfer_bytes=(50 + pixel_count * 26 + 7) // 8,
        ),
        pixel_count,
        _device=mock_device,
    )


def _benchmark_rust(
    binary: Path | None,
    specs: tuple[RustOutputSpec, ...],
    frames: tuple[np.ndarray, ...],
    warmup: int,
    count: int,
) -> tuple[list[float], dict[str, int | float | None]]:
    process = RustEngineProcess(binary_path=binary)
    connection = None
    try:
        process.configure(specs)
        connection = process.open_frame_stream()
        samples_ms: list[float] = []
        latest_sequence = 0
        for sequence in range(1, warmup + count + 1):
            current = tuple(_mutate_frame(frame, sequence) for frame in frames)
            started_ns = time.perf_counter_ns()
            process.send_frame_set(connection, sequence, current)
            elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            if sequence > warmup:
                samples_ms.append(elapsed_ms)
            latest_sequence = sequence

        deadline = time.monotonic() + 5.0
        health = process.health()
        while health.last_sequence != latest_sequence and time.monotonic() < deadline:
            time.sleep(0.01)
            health = process.health()
        if health.last_sequence != latest_sequence:
            raise RuntimeError(
                "Rust output worker did not complete the final benchmark frame"
            )
        return samples_ms, {
            "frames_rendered": health.frames_rendered,
            "dropped_frames": health.dropped_frames,
            "failed_frames": health.failed_frames,
            "frame_latency_ms": health.frame_latency_ms,
            "frame_latency_p50_ms": health.frame_latency_p50_ms,
            "frame_latency_p95_ms": health.frame_latency_p95_ms,
            "frame_latency_p99_ms": health.frame_latency_p99_ms,
            "queue_latency_ms": health.queue_latency_ms,
            "transmission_ms": health.transmission_ms,
        }
    finally:
        if connection is not None:
            connection.close()
        process.close()


def _make_frame(pixel_count: int, sequence: int) -> np.ndarray:
    indexes = np.arange(pixel_count, dtype=np.uint16)
    frame = np.empty((pixel_count, 4), dtype=np.uint8)
    frame[:, 0] = ((indexes + sequence) % 256).astype(np.uint8)
    frame[:, 1] = ((indexes * 3 + sequence) % 256).astype(np.uint8)
    frame[:, 2] = ((indexes * 7 + sequence) % 256).astype(np.uint8)
    frame[:, 3] = 255
    return frame


def _mutate_frame(frame: np.ndarray, sequence: int) -> np.ndarray:
    updated = frame.copy()
    updated[:, 0] = ((updated[:, 0].astype(np.uint16) + sequence) % 256).astype(
        np.uint8
    )
    return updated


def _metrics(samples_ms: list[float]) -> dict[str, float]:
    ordered = sorted(samples_ms)
    p95_index = min(len(ordered) - 1, max(0, ceil(0.95 * len(ordered)) - 1))
    elapsed_seconds = sum(ordered) / 1000
    return {
        "median_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[p95_index], 4),
        "operations_per_second": round(len(ordered) / elapsed_seconds, 2)
        if elapsed_seconds
        else 0.0,
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


if __name__ == "__main__":
    main()
