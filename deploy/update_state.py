#!/usr/bin/env python3
"""Check the LumiStripe update source and persist updater status."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BRANCH_REF = "refs/heads/main"
UPDATE_REMOTE_URL = "https://github.com/woobble/LumiStripe.git"
CHECK_TIMEOUT_SECONDS = 25
UPDATE_LOCK_FILE = Path("/run/lumistripe-update/update.lock")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017 - system Python may predate datetime.UTC


def empty_status() -> dict[str, Any]:
    return {
        "supported": True,
        "installed_commit": None,
        "available_commit": None,
        "update_available": False,
        "working_tree_dirty": False,
        "checked_at": None,
        "check_error": None,
        "install_state": "idle",
        "install_started_at": None,
        "install_finished_at": None,
        "install_error": None,
    }


def load_status(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_status()
    status = empty_status()
    if isinstance(value, dict):
        status.update(value)
    return status


def write_status(path: Path, status: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".update-status-",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), 0o644)
            json.dump(status, temporary, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def git(project_dir: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_dir), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT_SECONDS,
    )
    return result.stdout.strip()


def update_is_running() -> bool:
    try:
        with UPDATE_LOCK_FILE.open("r", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except OSError:
        return False
    return False


def check(project_dir: Path, status_path: Path) -> int:
    lock_path = status_path.with_name("update-check.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        status = load_status(status_path)
        if status["install_state"] == "running":
            if update_is_running():
                return 0
            status.update(
                install_state="failed",
                install_finished_at=now(),
                install_error="Update stopped before completion. Check the Pi logs before retrying.",
            )
            write_status(status_path, status)

        timestamp = now()
        try:
            installed_commit = git(project_dir, "rev-parse", "HEAD")
            remote_result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(project_dir),
                    "ls-remote",
                    "--exit-code",
                    UPDATE_REMOTE_URL,
                    BRANCH_REF,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=CHECK_TIMEOUT_SECONDS,
            )
            available_commit = remote_result.stdout.split()[0]
            if len(available_commit) != 40 or any(
                character not in "0123456789abcdef" for character in available_commit
            ):
                raise ValueError("remote returned an invalid commit")
            working_tree_dirty = bool(git(project_dir, "status", "--porcelain"))
        except (OSError, ValueError, subprocess.SubprocessError, IndexError):
            status.update(
                supported=True,
                checked_at=timestamp,
                check_error="Could not reach GitHub. The next check will retry.",
            )
            write_status(status_path, status)
            return 0

        status.update(
            supported=True,
            installed_commit=installed_commit,
            available_commit=available_commit if available_commit != installed_commit else None,
            update_available=available_commit != installed_commit,
            working_tree_dirty=working_tree_dirty,
            checked_at=timestamp,
            check_error=None,
        )
        write_status(status_path, status)
    return 0


def record_install_state(command: str, project_dir: Path, status_path: Path) -> int:
    lock_path = status_path.with_name("update-check.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        status = load_status(status_path)
        timestamp = now()
        if command == "install-start":
            status.update(
                supported=True,
                install_state="running",
                install_started_at=timestamp,
                install_finished_at=None,
                install_error=None,
            )
        elif command == "install-succeeded":
            installed_commit = git(project_dir, "rev-parse", "HEAD")
            status.update(
                supported=True,
                installed_commit=installed_commit,
                available_commit=None,
                update_available=False,
                working_tree_dirty=bool(git(project_dir, "status", "--porcelain")),
                checked_at=timestamp,
                check_error=None,
                install_state="success",
                install_finished_at=timestamp,
                install_error=None,
            )
        else:
            status.update(
                supported=True,
                install_state="failed",
                install_finished_at=timestamp,
                install_error="Update failed. Check the Pi logs before retrying.",
            )
        write_status(status_path, status)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("initialize", "check", "install-start", "install-succeeded", "install-failed"),
    )
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "initialize":
        write_status(args.state_file, empty_status())
        return 0
    if args.command == "check":
        return check(args.project_dir, args.state_file)
    try:
        return record_install_state(args.command, args.project_dir, args.state_file)
    except (OSError, subprocess.SubprocessError):
        print("Could not write LumiStripe update status.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
