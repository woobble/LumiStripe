from __future__ import annotations

import argparse
import time
from functools import partial

import numpy as np
from lumistripe.gpio._sm16716 import encode_into, frame_size
from lumistripe.gpio.spi import encode_legacy_frame


def _average_microseconds(callback, iterations: int) -> float:
    started = time.perf_counter_ns()
    for _ in range(iterations):
        callback()
    return (time.perf_counter_ns() - started) / iterations / 1_000


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark LumiStripe SPI frame encoding")
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--speed", type=int, default=1_000_000, help="SPI speed in Hz")
    args = parser.parse_args()
    if args.iterations <= 0 or args.speed <= 0:
        parser.error("iterations and speed must be positive")

    print("pixels bytes native_us python_us wire_us native/wire")
    for pixel_count in (41, 100, 200, 1_000):
        pixels = np.random.default_rng(pixel_count).integers(
            0,
            256,
            size=(pixel_count, 4),
            dtype=np.uint8,
        )
        destination = bytearray(frame_size(pixel_count))
        native_us = _average_microseconds(
            partial(encode_into, pixels, destination),
            args.iterations,
        )
        python_us = _average_microseconds(
            partial(encode_legacy_frame, pixels),
            args.iterations,
        )
        wire_us = len(destination) * 8 / args.speed * 1_000_000
        print(
            f"{pixel_count:>6} {len(destination):>5} {native_us:>9.2f} "
            f"{python_us:>9.2f} {wire_us:>7.2f} {native_us / wire_us:>10.2%}"
        )


if __name__ == "__main__":
    main()
