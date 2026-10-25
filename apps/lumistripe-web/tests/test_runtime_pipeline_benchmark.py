from __future__ import annotations

import argparse

import pytest

from scripts.benchmark_runtime_pipeline import (
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
