from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from lumistripe_web.app import create_app
from lumistripe_web.runtime import RuntimeSettings
from lumistripe_web.system_stats import SystemStatsCollector


def _write_linux_stats(root: Path, *, cpu_line: str = "cpu 100 0 50 800 0 0 0 0") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "stat").write_text(f"{cpu_line}\ncpu0 0 0 0 0\n", encoding="ascii")
    (root / "meminfo").write_text(
        "MemTotal:       1024 kB\nMemAvailable:    256 kB\n",
        encoding="ascii",
    )
    (root / "uptime").write_text("1234.5 500.0\n", encoding="ascii")


def test_system_stats_collector_reads_linux_metrics_and_cpu_deltas(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _write_linux_stats(proc_root)
    thermal_root = tmp_path / "sys" / "class" / "thermal"
    soc_zone = thermal_root / "thermal_zone0"
    cpu_zone = thermal_root / "thermal_zone1"
    soc_zone.mkdir(parents=True)
    cpu_zone.mkdir()
    (soc_zone / "type").write_text("soc-thermal\n", encoding="ascii")
    (soc_zone / "temp").write_text("42000\n", encoding="ascii")
    (cpu_zone / "type").write_text("cpu-thermal\n", encoding="ascii")
    (cpu_zone / "temp").write_text("53500\n", encoding="ascii")

    collector = SystemStatsCollector(
        proc_root=proc_root,
        thermal_root=thermal_root,
        disk_path=tmp_path,
    )
    first = collector.collect()

    assert first.cpu_temperature_c == 53.5
    assert first.cpu_usage_percent is None
    assert first.memory_used_bytes == 768 * 1024
    assert first.memory_total_bytes == 1024 * 1024
    assert first.memory_usage_percent == 75.0
    assert first.disk_used_bytes is not None
    assert first.disk_total_bytes is not None
    assert first.disk_usage_percent is not None
    assert first.uptime_seconds == 1234.5
    assert first.sampled_at.tzinfo is not None

    _write_linux_stats(proc_root, cpu_line="cpu 200 0 100 900 0 0 0 0")
    second = collector.collect()
    assert second.cpu_usage_percent == 60.0


def test_system_stats_collector_returns_null_for_unavailable_sources(tmp_path: Path) -> None:
    collector = SystemStatsCollector(
        proc_root=tmp_path / "missing-proc",
        thermal_root=tmp_path / "missing-thermal",
        disk_path=tmp_path / "missing-disk",
    )

    stats = collector.collect()

    assert stats.cpu_temperature_c is None
    assert stats.cpu_usage_percent is None
    assert stats.memory_used_bytes is None
    assert stats.memory_total_bytes is None
    assert stats.memory_usage_percent is None
    assert stats.disk_used_bytes is None
    assert stats.disk_total_bytes is None
    assert stats.disk_usage_percent is None
    assert stats.uptime_seconds is None


def test_system_stats_endpoint_requires_pairing_and_returns_collected_values(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _write_linux_stats(proc_root)
    collector = SystemStatsCollector(
        proc_root=proc_root,
        thermal_root=tmp_path / "thermal",
        disk_path=tmp_path,
    )
    app = create_app(
        RuntimeSettings(pixels=8),
        pairing_code="4321",
        system_stats_collector=collector,
    )

    with TestClient(app) as client:
        assert client.get("/api/system/stats").status_code == 401
        assert client.post("/api/auth/pair", json={"code": "4321"}).status_code == 200

        response = client.get("/api/system/stats")

    assert response.status_code == 200
    assert response.json()["cpu_temperature_c"] is None
    assert response.json()["cpu_usage_percent"] is None
    assert response.json()["memory_usage_percent"] == 75.0
    assert response.json()["uptime_seconds"] == 1234.5
