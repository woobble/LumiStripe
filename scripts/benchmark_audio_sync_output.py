"""Measure PortAudio calibration callback timing on the selected Pi sink."""

from __future__ import annotations

import argparse
import json
import platform
import time

from lumistripe_web.bluetooth import BluetoothManager
from lumistripe_web.runtime.sync_calibration import AudioSyncCalibrationTone


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sink", required=True, help="PipeWire sink selector to verify")
    parser.add_argument("--duration", type=float, default=20.0)
    args = parser.parse_args()
    if args.duration < 2.0:
        parser.error("--duration must be at least 2 seconds")

    manager = BluetoothManager(enabled=True)
    player: AudioSyncCalibrationTone | None = None
    try:
        before_input_ids = manager.calibration_output_input_ids()
        player = AudioSyncCalibrationTone()
        manager.verify_calibration_output_route(before_input_ids, args.sink)
        player.arm()
        time.sleep(args.duration)
        stats = player.diagnostics()
        print(
            json.dumps(
                {
                    "platform": platform.platform(),
                    "sink": args.sink,
                    "duration_seconds": args.duration,
                    "callback_count": stats.callback_count,
                    "sample_rate": stats.sample_rate,
                    "block_duration_ms": stats.block_duration_ms,
                    "callback_p50_ms": stats.callback_p50_ms,
                    "callback_p95_ms": stats.callback_p95_ms,
                    "callback_p99_ms": stats.callback_p99_ms,
                    "interval_jitter_p95_ms": stats.interval_jitter_p95_ms,
                    "underflow_count": stats.underflow_count,
                    "overflow_count": stats.overflow_count,
                    "callback_error_count": stats.callback_error_count,
                },
                indent=2,
            )
        )
    finally:
        if player is not None:
            player.close()
        manager.stop()


if __name__ == "__main__":
    main()
