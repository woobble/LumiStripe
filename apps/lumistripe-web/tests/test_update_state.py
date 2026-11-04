from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

HELPER_PATH = Path(__file__).resolve().parents[3] / "deploy" / "update_state.py"
SPEC = importlib.util.spec_from_file_location("lumistripe_update_state", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
update_state = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = update_state
SPEC.loader.exec_module(update_state)


def test_write_status_initializes_owner_from_parent_directory(tmp_path: Path) -> None:
    state_dir = tmp_path / "update"
    state_dir.mkdir()
    expected = state_dir.stat()
    state_file = state_dir / "status.json"

    update_state.write_status(state_file, update_state.empty_status())

    actual = state_file.stat()
    assert (actual.st_uid, actual.st_gid) == (expected.st_uid, expected.st_gid)
    assert actual.st_mode & 0o777 == 0o644


def test_write_status_preserves_existing_owner_for_root_updater(tmp_path: Path) -> None:
    if os.geteuid() != 0:
        pytest.skip("preserving a different service owner requires root")
    state_file = tmp_path / "update" / "status.json"
    update_state.write_status(state_file, update_state.empty_status())
    os.chown(state_file, 65534, 65534)

    update_state.write_status(state_file, update_state.empty_status())

    actual = state_file.stat()
    assert (actual.st_uid, actual.st_gid) == (65534, 65534)


def test_write_status_rejects_symlinked_status_path(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("sensitive data", encoding="utf-8")
    state_file = tmp_path / "update" / "status.json"
    state_file.parent.mkdir()
    state_file.symlink_to(target)

    with pytest.raises(OSError, match="regular file"):
        update_state.write_status(state_file, update_state.empty_status())
    assert target.read_text(encoding="utf-8") == "sensitive data"


def test_root_created_lock_uses_status_file_owner(tmp_path: Path) -> None:
    if os.geteuid() != 0:
        pytest.skip("changing lock ownership requires root")
    state_file = tmp_path / "update" / "status.json"
    update_state.write_status(state_file, update_state.empty_status())
    os.chown(state_file, 65534, 65534)

    with update_state._open_update_lock(state_file):
        lock = state_file.with_name("update-check.lock").stat()

    assert (lock.st_uid, lock.st_gid) == (65534, 65534)
    assert lock.st_mode & 0o777 == 0o644


def test_update_check_recovers_stale_installing_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    state_file = tmp_path / "update" / "status.json"
    update_state.write_status(
        state_file,
        {
            **update_state.empty_status(),
            "install_state": "running",
            "install_started_at": "2026-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(update_state, "UPDATE_LOCK_FILE", tmp_path / "missing.lock")
    installed = "a" * 40
    available = "b" * 40

    def online_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "rev-parse" in command:
            return SimpleNamespace(stdout=f"{installed}\n")
        if "ls-remote" in command:
            return SimpleNamespace(stdout=f"{available}\trefs/heads/main\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(update_state.subprocess, "run", online_run)
    assert update_state.check(project_dir, state_file) == 0
    status = update_state.load_status(state_file)
    assert status["install_state"] == "failed"
    assert status["update_available"] is True
    assert "stopped before completion" in status["install_error"]


def test_update_check_skips_while_update_lock_is_held(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    state_file = tmp_path / "update" / "status.json"
    update_state.write_status(
        state_file,
        {
            **update_state.empty_status(),
            "install_state": "running",
            "install_started_at": "2026-01-01T00:00:00+00:00",
        },
    )
    lock_path = tmp_path / "updater.lock"
    lock_path.touch()
    monkeypatch.setattr(update_state, "UPDATE_LOCK_FILE", lock_path)
    with lock_path.open("r+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert update_state.check(project_dir, state_file) == 0
    status = json.loads(state_file.read_text(encoding="utf-8"))
    assert status["install_state"] == "running"
    assert status["checked_at"] is None


def test_update_check_records_remote_version_and_preserves_it_when_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    state_file = tmp_path / "update" / "status.json"
    installed = "a" * 40
    available = "b" * 40

    def online_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "rev-parse" in command:
            return SimpleNamespace(stdout=f"{installed}\n")
        if "ls-remote" in command:
            return SimpleNamespace(stdout=f"{available}\trefs/heads/main\n")
        return SimpleNamespace(stdout=" M local-config\n")

    monkeypatch.setattr(update_state.subprocess, "run", online_run)
    assert update_state.check(project_dir, state_file) == 0
    available_status = update_state.load_status(state_file)
    assert available_status["update_available"] is True
    assert available_status["installed_commit"] == installed
    assert available_status["available_commit"] == available
    assert available_status["working_tree_dirty"] is True
    assert available_status["check_error"] is None

    def offline_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "ls-remote" in command:
            raise subprocess.CalledProcessError(128, command)
        return SimpleNamespace(stdout=f"{installed}\n")

    monkeypatch.setattr(update_state.subprocess, "run", offline_run)
    assert update_state.check(project_dir, state_file) == 0
    offline_status = update_state.load_status(state_file)
    assert offline_status["update_available"] is True
    assert offline_status["available_commit"] == available
    assert offline_status["check_error"]
