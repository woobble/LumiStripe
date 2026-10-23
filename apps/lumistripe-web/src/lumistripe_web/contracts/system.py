"""System and deployment status contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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


__all__ = ["UpdateStatus"]
