from __future__ import annotations

import argparse
import time
from functools import partial
from math import ceil

import numpy as np
from lumistripe.gpio._sm16716 import encode_into, frame_size
from lumistripe.gpio.spi import SPIConfig, SPIStripe, encode_legacy_frame


def _measure_microseconds(callback, iterations: int) -> tuple[float, float, float]:
    timings: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        callback()
        timings.append((time.perf_counter_ns() - started) / 1_000)
    ordered = sorted(timings)

    def percentile(value: float) -> float:
        return ordered[min(max(0, ceil(len(ordered) * value) - 1), len(ordered) - 1)]

    return percentile(0.50), percentile(0.95), percentile(0.99)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark LumiStripe SPI frame encoding")
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument(
        "--device",
        help="optional physical spidev path; sends repeated black frames and clears on exit",
    )
    parser.add_argument(
        "--transfer-iterations",
        type=int,
        default=20,
        help="physical transfer samples per LED count when --device is supplied",
    )
    parser.add_argument("--speed", type=int, default=1_000_000, help="SPI speed in Hz")
    args = parser.parse_args()
    if args.iterations <= 0 or args.speed <= 0 or args.transfer_iterations <= 0:
        parser.error("iterations, transfer iterations, and speed must be positive")

    print(
        "pixels bytes native_p50_us native_p95_us native_p99_us "
        "python_p50_us python_p95_us python_p99_us wire_us native_p50/wire "
        "spi_p50_us spi_p95_us spi_p99_us"
    )
    for pixel_count in (41, 100, 150, 200, 300, 600, 1_000):
        pixels = np.random.default_rng(pixel_count).integers(
            0,
            256,
            size=(pixel_count, 4),
            dtype=np.uint8,
        )
        destination = bytearray(frame_size(pixel_count))
        native_p50, native_p95, native_p99 = _measure_microseconds(
            partial(encode_into, pixels, destination),
            args.iterations,
        )
        python_p50, python_p95, python_p99 = _measure_microseconds(
            partial(encode_legacy_frame, pixels),
            args.iterations,
        )
        wire_us = len(destination) * 8 / args.speed * 1_000_000
        spi_timings: tuple[float, float, float] | None = None
        if args.device is not None:
            stripe = SPIStripe(SPIConfig(args.device, args.speed), pixel_count)
            stripe.clear()
            try:
                stripe.force_flush()
                spi_timings = _measure_microseconds(
                    stripe.force_flush,
                    args.transfer_iterations,
                )
            finally:
                try:
                    stripe.clear()
                    stripe.force_flush()
                finally:
                    stripe.close()
        spi_result = (
            "- - -"
            if spi_timings is None
            else " ".join(f"{value:.2f}" for value in spi_timings)
        )
        print(
            f"{pixel_count:>6} {len(destination):>5} "
            f"{native_p50:>13.2f} {native_p95:>13.2f} {native_p99:>13.2f} "
            f"{python_p50:>13.2f} {python_p95:>13.2f} {python_p99:>13.2f} "
            f"{wire_us:>7.2f} {native_p50 / wire_us:>15.2%} {spi_result}"
        )


if __name__ == "__main__":
    main()
