from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1]
UPDATE_SCRIPT = DEPLOY / "update.sh"
UPDATE_SUPPORT_SCRIPT = DEPLOY / "update-support.sh"


@pytest.mark.parametrize(
    ("spotify_configured", "failure_step", "return_code"),
    [(False, "", 0), (True, "", 0), (False, "build_rust_backend", 42)],
)
def test_update_rollback_checks_out_previous_revision_and_rebuilds_services(
    tmp_path: Path, spotify_configured: bool, failure_step: str, return_code: int
) -> None:
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    rollback = source[source.index("rollback() {"):source.index("record_update_state() {")]
    project_dir = tmp_path / "project"
    deploy_dir = project_dir / "deploy"
    deploy_dir.mkdir(parents=True)
    (deploy_dir / "update-support.sh").write_text(
        UPDATE_SUPPORT_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    call_log = tmp_path / "calls"
    script = f"""
set -Eeuo pipefail
PREVIOUS_REF=previous-rust-commit
ROLLING_BACK=0
git_user() {{
  printf 'git %s\\n' "$*" >> "$CALL_LOG"
  if [[ "$1" == switch ]]; then [[ "$3" == "$PREVIOUS_REF" ]] || return 1; fi
  return 0
}}
rollback_step() {{
  printf '%s\\n' "$1" >> "$CALL_LOG"
  [[ "$FAIL_ROLLBACK_STEP" != "$1" ]]
}}
build_rust_backend() {{ rollback_step build_rust_backend; }}
build_frontend() {{ rollback_step build_frontend; }}
refresh_deployment_files() {{ rollback_step refresh_deployment_files; }}
systemctl() {{ printf 'systemctl %s\\n' "$*" >> "$CALL_LOG"; }}
wait_for_web_service_health() {{ printf 'health_check\\n' >> "$CALL_LOG"; [[ "$ROLLBACK_HEALTHY" == 1 ]]; }}
spotify_environment_ready() {{ [[ "$SPOTIFY_CONFIGURED" == 1 ]]; }}
{rollback}
if rollback; then exit 0; else exit 42; fi
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PROJECT_DIR": str(project_dir),
            "CALL_LOG": str(call_log),
            "FAIL_ROLLBACK_STEP": failure_step,
            "SPOTIFY_CONFIGURED": str(int(spotify_configured)),
            "ROLLBACK_HEALTHY": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == return_code, result.stderr
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "git switch --detach previous-rust-commit"
    assert calls[1:4] == [
        "build_rust_backend",
        "build_frontend",
        "refresh_deployment_files",
    ]
    assert "systemctl restart lumistripe-web.service" in calls
    assert "health_check" in calls
    assert ("systemctl restart lumistripe-spotify.service" in calls) is spotify_configured
    assert not any("submodule" in call for call in calls)
    if failure_step:
        assert "Rollback failed; manual recovery is required." in result.stderr
    else:
        assert "Rollback failed" not in result.stderr


@pytest.mark.parametrize(
    ("restart_succeeds", "service_active", "healthy", "return_code", "probes"),
    [
        (True, True, True, 0, 1),
        (True, True, False, 42, 60),
        (True, False, True, 42, 0),
        (False, True, True, 42, 0),
    ],
)
def test_rust_rollback_skips_python_and_verifies_restored_web_health(
    tmp_path: Path,
    restart_succeeds: bool,
    service_active: bool,
    healthy: bool,
    return_code: int,
    probes: int,
) -> None:
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    health = source[
        source.index("wait_for_web_service_health() {"):
        source.index("rollback() {")
    ]
    rollback = source[source.index("rollback() {"):source.index("record_update_state() {")]
    build = source[
        source.index("build_rust_backend() {"):
        source.index("prune_rust_backend_releases() {")
    ]
    project_dir = tmp_path / "project"
    deploy_dir = project_dir / "deploy"
    deploy_dir.mkdir(parents=True)
    (deploy_dir / "update-support.sh").write_text(
        UPDATE_SUPPORT_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    builder = deploy_dir / "build-rust-backend.sh"
    builder.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    builder.chmod(0o755)
    release = project_dir / "target/rust-deploy/releases/previous-rust-commit"
    release.mkdir(parents=True)
    cli_binary = release / "lumistripe"
    cli_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli_binary.chmod(0o755)
    current_release = project_dir / "target/rust-deploy/current"
    current_release.symlink_to("releases/previous-rust-commit")
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    call_log = tmp_path / "calls"

    script = f"""
set -Eeuo pipefail
PREVIOUS_REF=previous-rust-commit
ROLLING_BACK=0
PROJECT_DIR="$PROJECT_DIR"
PROJECT_HOME="$PROJECT_HOME"
WEB_BACKEND=rust
as_user() {{
  printf 'as_user %s\\n' "$*" >> "$CALL_LOG"
  case "$1" in
    install) /usr/bin/install "${{@:2}}" ;;
    ln) /usr/bin/ln "${{@:2}}" ;;
    *) return 95 ;;
  esac
}}
as_user_with_env() {{ printf 'rust_build %s\\n' "$*" >> "$CALL_LOG"; }}
git_user() {{ printf 'git %s\\n' "$*" >> "$CALL_LOG"; }}
build_frontend() {{ printf 'build_frontend\\n' >> "$CALL_LOG"; }}
refresh_deployment_files() {{ printf 'refresh_deployment_files\\n' >> "$CALL_LOG"; }}
systemctl() {{
  printf 'systemctl %s\\n' "$*" >> "$CALL_LOG"
  if [[ "$*" == 'restart lumistripe-web.service' ]]; then
    [[ "$RESTART_SUCCEEDS" == 1 ]]
  elif [[ "$*" == 'is-active --quiet lumistripe-web.service' ]]; then
    [[ "$SERVICE_ACTIVE" == 1 ]]
  else
    return 0
  fi
}}
curl() {{ printf 'curl %s\\n' "$*" >> "$CALL_LOG"; [[ "$WEB_HEALTHY" == 1 ]]; }}
sleep() {{ :; }}
spotify_environment_ready() {{ return 1; }}
{build}
{health}
{rollback}
if rollback; then exit 0; else exit 42; fi
"""
    environment = {
        "PATH": str(empty_path),
        "PROJECT_DIR": str(project_dir),
        "PROJECT_HOME": str(tmp_path / "service-home"),
        "CALL_LOG": str(call_log),
        "RESTART_SUCCEEDS": str(int(restart_succeeds)),
        "SERVICE_ACTIVE": str(int(service_active)),
        "WEB_HEALTHY": str(int(healthy)),
    }
    for interpreter in ("python", "python3"):
        with pytest.raises(FileNotFoundError):
            subprocess.run([interpreter], env=environment, check=False)

    result = subprocess.run(
        ["/bin/bash", "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == return_code, result.stderr
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "git switch --detach previous-rust-commit"
    assert any(
        line.startswith("rust_build RUSTUP_HOME=")
        and "build-rust-backend.sh" in line
        and str(project_dir) in line
        for line in calls
    )
    assert "build_frontend" in calls
    assert "refresh_deployment_files" in calls
    assert any(line.startswith("as_user ln -sfn ") for line in calls)
    assert not any("python" in line.lower() or "uv" in line.lower() for line in calls)
    assert not any("submodule" in line.lower() for line in calls)
    assert (tmp_path / "service-home/.local/bin/lumistripe-cli").resolve() == cli_binary
    health_probes = [line for line in calls if line.startswith("curl ")]
    assert len(health_probes) == probes
    assert all("--max-time 0.25" in line for line in health_probes)
    if return_code == 42:
        assert "Rollback failed; manual recovery is required." in result.stderr
