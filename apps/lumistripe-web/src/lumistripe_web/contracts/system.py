"""System and deployment status contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SystemStats(BaseModel):
    """Best-effort host resource metrics for the dashboard."""

    model_config = ConfigDict(frozen=True)

    cpu_temperature_c: float | None
    cpu_usage_percent: float | None = Field(ge=0.0, le=100.0)
    memory_used_bytes: int | None = Field(ge=0)
    memory_total_bytes: int | None = Field(ge=0)
    memory_usage_percent: float | None = Field(ge=0.0, le=100.0)
    disk_used_bytes: int | None = Field(ge=0)
    disk_total_bytes: int | None = Field(ge=0)
    disk_usage_percent: float | None = Field(ge=0.0, le=100.0)
    uptime_seconds: float | None = Field(ge=0.0)
    sampled_at: datetime


class UpdateStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    supported: bool
    installed_commit: str | None
    available_commit: str | None
    update_available: bool
    working_tree_dirty: bool
    checked_at: datetime | None
    check_error: str | None
    install_state: Literal["idle", "running", "success", "failed"]
    install_started_at: datetime | None
    install_finished_at: datetime | None
    install_error: str | None


__all__ = ["SystemStats", "UpdateStatus"]
