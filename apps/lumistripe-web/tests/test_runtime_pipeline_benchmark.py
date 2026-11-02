from __future__ import annotations

import argparse
import sys

import pytest

from scripts.benchmark_runtime_pipeline import (
    _counter_delta,
    _parse_args,
    _topology,
    parse_csv_ints,
    percentile,
    summarize,
)


def test_percentile_uses_nearest_rank() -> None:
    values = [8.0, 1.0, 3.0, 2.0, 4.0, 5.0, 6.0, 7.0]

    assert percentile(values, 0.50) == 4.0
    assert percentile(values, 0.95) == 8.0
    assert summarize(values) == {"p50": 4.0, "p95": 8.0, "p99": 8.0}


def test_percentile_returns_zero_for_no_samples() -> None:
    assert percentile([], 0.95) == 0.0


def test_counter_delta_handles_missing_and_reset_counters() -> None:
    assert _counter_delta(12, 8) == 4
    assert _counter_delta(3, 5) == 0
    assert _counter_delta(0, None) == 0
    assert _counter_delta(None, 0) is None


def test_parse_csv_ints_rejects_nonpositive_values() -> None:
    assert parse_csv_ints("150, 300", argument="--led-counts") == (150, 300)
    with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
        parse_csv_ints("150,0", argument="--led-counts")


def test_benchmark_topology_covers_two_zoned_outputs_and_power_limit() -> None:
    topology = _topology(
        150,
        "continuous",
        4,
        True,
        hardware=False,
        spi_devices=("/dev/spidev0.0", "/dev/spidev0.1"),
        spi_speed_hz=1_000_000,
        power_budget_watts=15.0,
    )

    assert topology.layout == "continuous"
    assert [output.pixels for output in topology.outputs] == [75, 75]
    assert [output.zone_count for output in topology.outputs] == [4, 4]
    assert topology.power_limiting_enabled
    assert topology.effective_power_budget_watts == 15.0


def test_benchmark_topology_supports_one_output() -> None:
    topology = _topology(
        300,
        "independent",
        1,
        False,
        hardware=False,
        spi_devices=("/dev/spidev0.0", "/dev/spidev0.1"),
        spi_speed_hz=1_000_000,
        power_budget_watts=15.0,
        output_count=1,
    )

    assert [output.pixels for output in topology.outputs] == [300]


def test_benchmark_cli_accepts_python_and_rust_backends(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    binary = tmp_path / "lumistripe-engine"
    binary.touch()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_runtime_pipeline.py",
            "--engine-backends",
            "python,rust",
            "--engine-binary",
            str(binary),
            "--output-counts",
            "1,2",
        ],
    )

    args = _parse_args()

    assert args.engine_backends == ("python", "rust")
    assert args.output_counts == (1, 2)
