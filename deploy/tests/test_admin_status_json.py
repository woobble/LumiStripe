from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ADMIN = Path(__file__).parents[1] / "lumistripe-admin"
COMMIT = "d" * 40


def _status_values(
    status_file: Path,
    *,
    service_user: str | None = None,
    path: Path | None = None,
) -> str:
    user = service_user or os.environ.get("USER") or subprocess.check_output(["id", "-un"], text=True).strip()
    command = (
        'source "$1"; '
        'if [[ -n "${4:-}" ]]; then PATH="$4"; export PATH; fi; '
        'ADMIN_SERVICE_USER="$2"; admin_update_status_values "$3"'
    )
    environment = os.environ.copy()
    result = subprocess.run(
        ["/bin/bash", "-c", command, "bash", str(ADMIN), user, str(status_file), str(path or "")],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return result.stdout.rstrip("\n")


def _python_free_path(tmp_path: Path) -> Path:
    path = tmp_path / "python-free-bin"
    path.mkdir()
    for command in ("bash", "chmod", "dd", "dirname", "id", "jq", "mktemp", "rm", "stat", "wc"):
        resolved = shutil.which(command)
        assert resolved is not None, f"required status utility is unavailable: {command}"
        (path / command).symlink_to(resolved)
    assert shutil.which("python", path=str(path)) is None
    assert shutil.which("python3", path=str(path)) is None
    return path


def _write_status(path: Path, status: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(status), encoding="utf-8")
    path.chmod(0o600)


def test_status_values_extract_only_valid_bounded_fields(tmp_path: Path) -> None:
    status_file = tmp_path / "update" / "status.json"
    _write_status(
        status_file,
        {
            "available_commit": COMMIT,
            "update_available": True,
            "checked_at": "2026-11-07T12:00:00Z",
            "install_state": "success",
            "install_finished_at": "2026-11-07T12:01:00Z",
        },
    )

    assert _status_values(status_file) == (
        f"available:{COMMIT}\tsuccess\t2026-11-07T12:00:00Z\t2026-11-07T12:01:00Z"
    )


def test_status_parser_runs_without_python_on_path(tmp_path: Path) -> None:
    status_file = tmp_path / "update" / "status.json"
    _write_status(
        status_file,
        {
            "available_commit": COMMIT,
            "update_available": True,
            "checked_at": "2026-11-07T12:00:00Z",
            "install_state": "success",
        },
    )

    assert _status_values(status_file, path=_python_free_path(tmp_path)) == (
        f"available:{COMMIT}\tsuccess\t2026-11-07T12:00:00Z\t-"
    )


def test_missing_malformed_symlink_and_oversized_state_fail_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "update"
    state_dir.mkdir(mode=0o700)
    missing = state_dir / "missing.json"
    malformed = state_dir / "malformed.json"
    malformed.write_text("{invalid", encoding="utf-8")
    malformed.chmod(0o600)
    target = state_dir / "target.json"
    _write_status(target, {"update_available": False, "checked_at": "2026-11-07T12:00:00Z"})
    linked = state_dir / "linked.json"
    linked.symlink_to(target)
    oversized = state_dir / "oversized.json"
    oversized.write_text("{" + '"padding":"' + ("x" * 1_048_600) + '"}', encoding="utf-8")
    oversized.chmod(0o600)
    unknown = "unknown\tunknown\t-\t-"

    assert _status_values(missing) == unknown
    assert _status_values(malformed) == unknown
    assert _status_values(linked) == unknown
    assert _status_values(oversized) == unknown


def test_status_schema_and_permissions_are_strict(tmp_path: Path) -> None:
    status_file = tmp_path / "update" / "status.json"
    _write_status(status_file, {"update_available": "false", "install_state": ["success"]})
    assert _status_values(status_file) == "unknown\tunknown\t-\t-"

    status_file.chmod(0o666)
    assert _status_values(status_file) == "unknown\tunknown\t-\t-"

    status_file.chmod(0o600)
    status_file.parent.chmod(0o777)
    assert _status_values(status_file) == "unknown\tunknown\t-\t-"


def test_admin_status_parser_has_no_python_or_shell_evaluation() -> None:
    source = ADMIN.read_text(encoding="utf-8")
    parser = source.split("admin_update_status_values() {", 1)[1].split("\nadmin_service_state()", 1)[0]
    assert "python" not in parser.lower()
    assert "jq -sr -e" in parser
    assert "iflag=nofollow" in parser
    assert "eval " not in parser
