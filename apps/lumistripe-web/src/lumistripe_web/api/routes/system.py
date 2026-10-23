"""Health and dashboard snapshot routes."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...contracts.common import AnimationList, DashboardState
from ...contracts.system import SystemStats, UpdateStatus
from ..dependencies import runtime_from_request

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    runtime = runtime_from_request(request)
    state = runtime.snapshot()
    if runtime.healthy:
        return JSONResponse({"status": "ok"})
    return JSONResponse(
        {"status": "unavailable", "error": state.error or "runtime is not running"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@router.get("/api/state", response_model=DashboardState)
async def state(request: Request) -> DashboardState:
    return runtime_from_request(request).snapshot()


@router.get("/api/animations", response_model=AnimationList)
async def animations(request: Request) -> AnimationList:
    return AnimationList(items=runtime_from_request(request).animations())


@router.get("/api/system/stats", response_model=SystemStats)
async def system_stats(request: Request) -> SystemStats:
    return request.app.state.system_stats_collector.collect()


def _update_status(request: Request) -> UpdateStatus:
    state_file: Path = request.app.state.update_state_file
    try:
        return UpdateStatus.model_validate_json(state_file.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return UpdateStatus(
            supported=False,
            installed_commit=None,
            available_commit=None,
            update_available=False,
            working_tree_dirty=False,
            checked_at=None,
            check_error=None,
            install_state="idle",
            install_started_at=None,
            install_finished_at=None,
            install_error=None,
        )


@router.get("/api/system/update", response_model=UpdateStatus)
async def update_status(request: Request) -> UpdateStatus:
    return _update_status(request)


@router.post(
    "/api/system/update/check",
    response_model=UpdateStatus,
    status_code=status.HTTP_202_ACCEPTED,
)
def check_for_updates(request: Request) -> UpdateStatus | JSONResponse:
    current = _update_status(request)
    if not current.supported:
        return JSONResponse(
            {"detail": "The system updater is not installed."},
            status_code=status.HTTP_409_CONFLICT,
        )
    # The checker exits while an update holds its lock and reconciles stale
    # "running" states after the updater stops.
    project_dir: Path = request.app.state.update_project_dir
    helper = project_dir / "deploy" / "update_state.py"
    if not helper.is_file():
        return JSONResponse(
            {"detail": "The update checker is unavailable."},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        subprocess.Popen(
            [
                sys.executable,
                str(helper),
                "check",
                "--project-dir",
                str(project_dir),
                "--state-file",
                str(request.app.state.update_state_file),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            env={
                "HOME": os.environ.get("HOME", "/"),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            },
        )
    except OSError:
        return JSONResponse(
            {"detail": "Could not start the update check."},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return current


@router.post(
    "/api/system/update/install",
    response_model=UpdateStatus,
    status_code=status.HTTP_202_ACCEPTED,
)
def install_update(request: Request) -> UpdateStatus:
    current = _update_status(request)
    if not current.supported:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The system updater is not installed.",
        )
    if current.install_state == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An update is already being installed.",
        )
    if current.working_tree_dirty:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The project checkout has local changes and cannot be updated.",
        )
    if not current.update_available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is no update ready to install.",
        )

    try:
        subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/local/sbin/lumistripe-update-trigger"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not start the system updater. Check the Pi installation.",
        ) from exc

    return current.model_copy(
        update={"install_state": "running", "install_started_at": datetime.now(UTC)}
    )


__all__ = ["router"]
