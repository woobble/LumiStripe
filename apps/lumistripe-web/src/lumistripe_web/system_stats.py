"""Best-effort Linux host resource sampling for the dashboard."""

from __future__ import annotations

import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from .contracts.system import SystemStats

_CPU_COUNTER_COUNT = 8
_KIBIBYTE = 1024


class SystemStatsCollector:
    """Collect host metrics without depending on Raspberry Pi-only commands."""

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        thermal_root: Path = Path("/sys/class/thermal"),
        disk_path: Path = Path("/"),
    ) -> None:
        self._proc_root = proc_root
        self._thermal_root = thermal_root
        self._disk_path = disk_path
        self._previous_cpu: tuple[int, int] | None = None
        self._lock = threading.Lock()

    def collect(self) -> SystemStats:
        """Return the newest readable metrics, leaving unavailable values null."""
        with self._lock:
            current_cpu = self._read_cpu_counters()
            cpu_usage = self._cpu_usage(current_cpu)
            if current_cpu is not None:
                self._previous_cpu = current_cpu

        memory_used, memory_total, memory_percent = self._read_memory()
        disk_used, disk_total, disk_percent = self._read_disk()
        return SystemStats(
            cpu_temperature_c=self._read_temperature(),
            cpu_usage_percent=cpu_usage,
            memory_used_bytes=memory_used,
            memory_total_bytes=memory_total,
            memory_usage_percent=memory_percent,
            disk_used_bytes=disk_used,
            disk_total_bytes=disk_total,
            disk_usage_percent=disk_percent,
            uptime_seconds=self._read_uptime(),
            sampled_at=datetime.now(UTC),
        )

    def _read_cpu_counters(self) -> tuple[int, int] | None:
        try:
            cpu_line = next(
                line
                for line in (self._proc_root / "stat")
                .read_text(encoding="ascii")
                .splitlines()
                if line.startswith("cpu ")
            )
            counters = [int(value) for value in cpu_line.split()[1:]]
        except (OSError, StopIteration, ValueError):
            return None
        if len(counters) < 4:
            return None

        # The first eight values are user through steal. Guest counters are
        # already included in user and nice, so exclude them from the total.
        counters = counters[:_CPU_COUNTER_COUNT]
        total = sum(counters)
        idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
        return total, idle

    def _cpu_usage(self, current: tuple[int, int] | None) -> float | None:
        previous = self._previous_cpu
        if current is None or previous is None:
            return None

        total_delta = current[0] - previous[0]
        idle_delta = current[1] - previous[1]
        if total_delta <= 0 or idle_delta < 0:
            return None
        busy_percent = 100.0 * (1.0 - idle_delta / total_delta)
        return round(min(100.0, max(0.0, busy_percent)), 1)

    def _read_memory(self) -> tuple[int | None, int | None, float | None]:
        values: dict[str, int] = {}
        try:
            for line in (self._proc_root / "meminfo").read_text(encoding="ascii").splitlines():
                key, separator, value = line.partition(":")
                if separator and key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(value.strip().split()[0]) * _KIBIBYTE
        except (OSError, IndexError, ValueError):
            return None, None, None

        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if total is None or total <= 0 or available is None:
            return None, None, None
        used = total - min(total, max(0, available))
        return used, total, round(100.0 * used / total, 1)

    def _read_disk(self) -> tuple[int | None, int | None, float | None]:
        try:
            usage = shutil.disk_usage(self._disk_path)
        except OSError:
            return None, None, None
        if usage.total <= 0:
            return None, None, None
        used = usage.total - usage.free
        return used, usage.total, round(100.0 * used / usage.total, 1)

    def _read_uptime(self) -> float | None:
        try:
            value = float((self._proc_root / "uptime").read_text(encoding="ascii").split()[0])
        except (OSError, IndexError, ValueError):
            return None
        return value if value >= 0 else None

    def _read_temperature(self) -> float | None:
        candidates: list[tuple[int, str, float]] = []
        try:
            zones = sorted(self._thermal_root.glob("thermal_zone*"))
        except OSError:
            return None

        for zone in zones:
            try:
                sensor_type = (zone / "type").read_text(encoding="ascii").strip().lower()
                raw_temperature = float((zone / "temp").read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                continue
            temperature = raw_temperature / 1000.0 if abs(raw_temperature) > 200 else raw_temperature
            if not -50.0 <= temperature <= 150.0:
                continue
            if "cpu" in sensor_type:
                priority = 0
            elif "soc" in sensor_type or "package" in sensor_type:
                priority = 1
            else:
                priority = 2
            candidates.append((priority, zone.name, temperature))

        if not candidates:
            return None
        _, _, temperature = min(candidates)
        return round(temperature, 1)


__all__ = ["SystemStatsCollector"]
