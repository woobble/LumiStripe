from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

UPDATE_SCRIPT = Path(__file__).parents[1] / "update.sh"


def _rollback_fixture(tmp_path: Path, extra: str = "") -> tuple[str, Path, Path]:
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    health = source[
        source.index("wait_for_web_service_health() {"):
        source.index("rollback() {")
    ]
    rollback = source[source.index("rollback() {"):source.index("record_update_state() {")]
    project_dir = tmp_path / "project"
    deploy_dir = project_dir / "deploy"
    deploy_dir.mkdir(parents=True)
    (deploy_dir / "update-support.sh").write_text(
        """
activate_rust_release() { printf 'activate %s\\n' "$1" >> "$CALL_LOG"; [[ "$FAIL_ROLLBACK_STEP" != activate_release ]]; }
install_rust_cli_command() { printf 'install-cli %s\\n' "$1" >> "$CALL_LOG"; [[ "$FAIL_ROLLBACK_STEP" != install_cli ]]; }
restart_spotify_service_if_needed() {
  local restart_required="$1"
  systemctl enable lumistripe-spotify.service
  if [[ "$restart_required" == 1 ]] || ! systemctl is-active --quiet lumistripe-spotify.service; then
    systemctl restart lumistripe-spotify.service
  fi
}
""",
        encoding="utf-8",
    )
    script = f"""
set -Eeuo pipefail
SCRIPT_DIR="$DEPLOY_DIR"
PREVIOUS_REF=previous-rust-commit
ROLLING_BACK=0
UPDATE_SWITCHED=1
RELEASE_ACTIVATED=1
PROJECT_DIR="$PROJECT_DIR"
PROJECT_HOME="$PROJECT_HOME"
SOLOIST_BINARY_CHANGED="${{SOLOIST_CHANGED:-0}}"
SPOTIFY_SERVICE_FILE_CHANGED=0
SPOTIFY_ENV_CHANGED=0
WIREPLUMBER_CONFIG_CHANGED=0
git_user() {{
  printf 'git %s\\n' "$*" >> "$CALL_LOG"
  if [[ "$1" == switch ]]; then [[ "$3" == "$PREVIOUS_REF" ]] || return 1; fi
  return 0
}}
refresh_deployment_files() {{
  printf 'refresh\\n' >> "$CALL_LOG"
  [[ "$FAIL_ROLLBACK_STEP" != refresh ]]
}}
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
spotify_environment_ready() {{ [[ "$SPOTIFY_CONFIGURED" == 1 ]]; }}
{extra}
{health}
{rollback}
if rollback; then exit 0; else exit 42; fi
"""
    return script, project_dir, deploy_dir


def _run_rollback(
    tmp_path: Path,
    script: str,
    *,
    fail_step: str = "",
    restart_succeeds: bool = True,
    service_active: bool = True,
    healthy: bool = True,
    spotify_configured: bool = False,
    soloist_changed: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    call_log = tmp_path / "calls"
    project_dir = tmp_path / "project"
    deploy_dir = project_dir / "deploy"
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir(exist_ok=True)
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        env={
            **os.environ,
            "PATH": str(empty_path),
            "PROJECT_DIR": str(project_dir),
            "PROJECT_HOME": str(tmp_path / "service-home"),
            "DEPLOY_DIR": str(deploy_dir),
            "CALL_LOG": str(call_log),
            "FAIL_ROLLBACK_STEP": fail_step,
            "RESTART_SUCCEEDS": str(int(restart_succeeds)),
            "SERVICE_ACTIVE": str(int(service_active)),
            "WEB_HEALTHY": str(int(healthy)),
            "SPOTIFY_CONFIGURED": str(int(spotify_configured)),
            "SOLOIST_CHANGED": str(int(soloist_changed)),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    calls = call_log.read_text(encoding="utf-8").splitlines()
    return result, calls


@pytest.mark.parametrize(
    ("spotify_configured", "soloist_changed", "failure_step", "return_code"),
    [
        (False, False, "", 0),
        (True, False, "", 0),
        (True, True, "", 0),
        (False, False, "activate_release", 42),
    ],
)
def test_rollback_restores_the_previous_checkout_and_release_without_rebuilding(
    tmp_path: Path,
    spotify_configured: bool,
    soloist_changed: bool,
    failure_step: str,
    return_code: int,
) -> None:
    script, _, _ = _rollback_fixture(tmp_path)

    result, calls = _run_rollback(
        tmp_path,
        script,
        fail_step=failure_step,
        spotify_configured=spotify_configured,
        soloist_changed=soloist_changed,
    )

    assert result.returncode == return_code, result.stderr
    assert calls[:5] == [
        "git switch --detach previous-rust-commit",
        "activate previous-rust-commit",
        "install-cli " + str(tmp_path / "service-home"),
        "refresh",
        "systemctl restart lumistripe-web.service",
    ]
    assert "systemctl is-active --quiet lumistripe-web.service" in calls
    assert "curl --disable --fail --silent --show-error --max-time 0.25 http://127.0.0.1:8000/api/health" in calls
    assert ("systemctl restart lumistripe-spotify.service" in calls) is (
        spotify_configured and soloist_changed
    )
    assert not any("build" in call or "bun" in call.lower() or "cargo" in call.lower() for call in calls)
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
def test_rollback_verifies_health_and_uses_only_the_validated_previous_release(
    tmp_path: Path,
    restart_succeeds: bool,
    service_active: bool,
    healthy: bool,
    return_code: int,
    probes: int,
) -> None:
    script, _, _ = _rollback_fixture(tmp_path)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    for interpreter in ("python", "python3"):
        with pytest.raises(FileNotFoundError):
            subprocess.run([interpreter], env={"PATH": str(empty_path)}, check=False)

    result, calls = _run_rollback(
        tmp_path,
        script,
        restart_succeeds=restart_succeeds,
        service_active=service_active,
        healthy=healthy,
    )

    assert result.returncode == return_code, result.stderr
    assert calls[1:4] == [
        "activate previous-rust-commit",
        "install-cli " + str(tmp_path / "service-home"),
        "refresh",
    ]
    assert not any("build" in call or "bun" in call.lower() or "cargo" in call.lower() for call in calls)
    assert not any("python" in call.lower() or "uv" in call.lower() for call in calls)
    health_probes = [line for line in calls if line.startswith("curl ")]
    assert len(health_probes) == probes
    assert all("--max-time 0.25" in line for line in health_probes)
    if return_code == 42:
        assert "Rollback failed; manual recovery is required." in result.stderr
