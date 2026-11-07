from __future__ import annotations

import grp
import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1]


def _section_values(text: str, section: str, key: str) -> list[str]:
    current_section = ""
    values = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
        elif current_section == section and "=" in line:
            current_key, value = line.split("=", 1)
            if current_key == key:
                values.append(value)
    return values


def _unit(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def _nonempty_lines(name: str) -> list[str]:
    return [line for line in _unit(name).splitlines() if line.strip()]


def test_systemd_services_keep_rust_helpers_and_privilege_boundaries() -> None:
    web_service = _unit("lumistripe-web.service")
    assert _section_values(web_service, "Service", "User") == [
        "@LUMI_SERVICE_USER@"
    ]
    assert _section_values(web_service, "Service", "Group") == [
        "@LUMI_SERVICE_GROUP@"
    ]
    assert _section_values(web_service, "Service", "ExecStart") == [
        '/bin/bash "@LUMI_PROJECT_DIR@/deploy/lumistripe-web-launcher.sh"'
    ]
    assert "Environment=PYTHONUNBUFFERED" not in web_service
    assert "LUMI_PYTHON_VERSION" not in web_service
    assert "LUMI_WEB_BACKEND=python" not in web_service

    environment_template = _unit("lumistripe-web.env.example")
    assert _section_values(environment_template, "", "LUMI_WEB_BACKEND") == []

    update_check = _unit("lumistripe-update-check.service")
    assert _section_values(update_check, "Service", "User") == [
        "@LUMI_SERVICE_USER@"
    ]
    check_command = _section_values(update_check, "Service", "ExecStart")
    assert len(check_command) == 1
    assert "lumistripe-deploy-helper\" update-state check" in check_command[0]
    for directive in (
        "NoNewPrivileges",
        "PrivateTmp",
        "ProtectSystem",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectControlGroups",
        "ProtectHostname",
        "RestrictSUIDSGID",
    ):
        assert _section_values(update_check, "Service", directive)

    update_service = _unit("lumistripe-update.service")
    assert _section_values(update_service, "Service", "User") == ["root"]
    assert _section_values(update_service, "Service", "ExecStart") == [
        (
            "/bin/bash /usr/local/libexec/lumistripe/deploy/update.sh "
            "refs/remotes/lumistripe-upstream/main"
        )
    ]

    spotify_service = _unit("lumistripe-spotify.service")
    assert _section_values(spotify_service, "Service", "User") == [
        "@LUMI_SERVICE_USER@"
    ]
    preflight = _section_values(spotify_service, "Service", "ExecStartPre")
    assert len(preflight) == 1
    assert "/usr/bin/env -u LUMI_SPOTIFY_API_KEY" in preflight[0]
    assert "lumistripe-deploy-helper\" soloist-check --binary" in preflight[0]
    assert "NoNewPrivileges=yes" in spotify_service

    timer = _unit("lumistripe-update-check.timer")
    assert _section_values(timer, "Timer", "Unit") == [
        "lumistripe-update-check.service"
    ]
    assert _section_values(timer, "Timer", "OnUnitActiveSec") == ["6h"]


def test_sudoers_grants_only_the_fixed_helper_and_service_commands() -> None:
    assert _nonempty_lines("lumistripe-network.sudoers") == [
        "@LUMI_SERVICE_USER@ ALL=(root) NOPASSWD: /usr/local/sbin/lumistripe-network"
    ]
    assert _nonempty_lines("lumistripe-spotify-service.sudoers") == [
        (
            "@LUMI_SERVICE_USER@ ALL=(root) NOPASSWD: "
            "/usr/bin/systemctl restart --no-block lumistripe-spotify.service"
        )
    ]
    assert _nonempty_lines("lumistripe-update.sudoers") == [
        (
            "@LUMI_SERVICE_USER@ ALL=(root) NOPASSWD: "
            "/usr/local/sbin/lumistripe-update-trigger"
        )
    ]
    trigger = (DEPLOY / "lumistripe-update-trigger").read_text(encoding="utf-8")
    assert 'if (($# != 0)); then' in trigger
    assert "exec /usr/bin/systemctl start --no-block lumistripe-update.service" in trigger


@pytest.mark.parametrize(
    ("fail_configuration", "expected_state", "expected_diagnostic"),
    [
        (False, "success", None),
        (
            True,
            "failed;rollback=complete",
            "Updater step failed: Configuration: render web service unit (exit status 1).",
        ),
    ],
)
def test_dashboard_update_trigger_runs_the_fixed_service_and_records_outcome(
    tmp_path: Path,
    fail_configuration: bool,
    expected_state: str,
    expected_diagnostic: str | None,
) -> None:
    update_source = (DEPLOY / "update.sh").read_text(encoding="utf-8")
    step_helper = update_source[
        update_source.index("lumi_run_update_step() {"):
        update_source.index("\ninstall_wireplumber_config() {")
    ]
    health = update_source[
        update_source.index("wait_for_web_service_health() {"):
        update_source.index("\nrollback() {")
    ]
    rollback = update_source[
        update_source.index("rollback() {"):
        update_source.index("\nrecord_update_state() {")
    ]
    service_runner = tmp_path / "update-service.sh"
    outcome_file = tmp_path / "install-state"
    call_log = tmp_path / "update-calls.log"
    operation = "false" if fail_configuration else "true"
    support_dir = tmp_path / "deploy"
    support_dir.mkdir()
    (support_dir / "update-support.sh").write_text(
        """
install_rust_cli_command() { printf 'install-cli %s\\n' "$1" >> "$CALL_LOG"; }
activate_rust_release() { printf 'activate %s\\n' "$1" >> "$CALL_LOG"; }
restart_spotify_service_if_needed() { :; }
""",
        encoding="utf-8",
    )
    runner = f"""#!/bin/bash
set -u
SCRIPT_DIR={str(support_dir)!r}
PREVIOUS_REF=previous-commit
ROLLING_BACK=0
UPDATE_SWITCHED=1
RELEASE_ACTIVATED=1
PROJECT_DIR={str(tmp_path / "project")!r}
PROJECT_HOME={str(tmp_path / "service-home")!r}
SOLOIST_BINARY_CHANGED=0
SPOTIFY_SERVICE_FILE_CHANGED=0
SPOTIFY_ENV_CHANGED=0
WIREPLUMBER_CONFIG_CHANGED=0
git_user() {{ printf 'git %s\\n' "$*" >> "$CALL_LOG"; }}
refresh_deployment_files() {{ printf 'refresh\\n' >> "$CALL_LOG"; }}
install_rust_cli_command() {{ printf 'install-cli %s\\n' "$1" >> "$CALL_LOG"; }}
activate_rust_release() {{ printf 'activate %s\\n' "$1" >> "$CALL_LOG"; }}
systemctl() {{ printf 'systemctl %s\\n' "$*" >> "$CALL_LOG"; }}
curl() {{ printf 'curl\\n' >> "$CALL_LOG"; return 0; }}
sleep() {{ :; }}
spotify_environment_ready() {{ return 1; }}
{step_helper}
{health}
{rollback}
if lumi_run_update_step "Configuration: render web service unit" {operation}; then
  printf 'success\\n' > {str(outcome_file)!r}
else
  if rollback; then
    printf 'failed;rollback=complete\\n' > {str(outcome_file)!r}
  else
    printf 'failed;rollback=incomplete\\n' > {str(outcome_file)!r}
  fi
fi
"""
    service_runner.write_text(runner, encoding="utf-8")
    service_runner.chmod(0o755)

    systemctl_log = tmp_path / "systemctl.log"
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        f"""#!/bin/sh
set -eu
printf '%s\\n' "$*" >> {str(systemctl_log)!r}
[ "$*" = 'start --no-block lumistripe-update.service' ] || exit 2
{str(service_runner)!r} >> {str(tmp_path / "service.log")!r} 2>&1 || true
""",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)

    trigger = tmp_path / "lumistripe-update-trigger"
    trigger.write_text(
        _unit("lumistripe-update-trigger").replace(
            "/usr/bin/systemctl", str(systemctl)
        ),
        encoding="utf-8",
    )
    trigger.chmod(0o755)

    result = subprocess.run(
        [str(trigger)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "CALL_LOG": str(call_log)},
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert systemctl_log.read_text(encoding="utf-8").splitlines() == [
        "start --no-block lumistripe-update.service"
    ]
    assert outcome_file.read_text(encoding="utf-8").strip() == expected_state
    service_output = (tmp_path / "service.log").read_text(encoding="utf-8")
    if expected_diagnostic is None:
        assert service_output == ""
    else:
        assert expected_diagnostic in service_output
        assert call_log.read_text(encoding="utf-8").splitlines() == [
            "git switch --detach previous-commit",
            "activate previous-commit",
            f"install-cli {tmp_path / 'service-home'}",
            "refresh",
            "systemctl restart lumistripe-web.service",
            "systemctl is-active --quiet lumistripe-web.service",
            "curl",
        ]


def test_systemd_analyze_accepts_rendered_service_units(tmp_path: Path) -> None:
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd-analyze is unavailable")

    service_user = pwd.getpwuid(os.getuid())
    if service_user.pw_uid == 0:
        service_user = next(
            (
                account
                for account in pwd.getpwall()
                if account.pw_uid >= 1000 and account.pw_uid < 60000
            ),
            None,
        )
        if service_user is None:
            pytest.skip("no non-root account is available for unit verification")
    service_group = grp.getgrgid(service_user.pw_gid).gr_name
    replacements = {
        "@LUMI_SERVICE_USER@": service_user.pw_name,
        "@LUMI_SERVICE_GROUP@": service_group,
        "@LUMI_SERVICE_HOME@": str(tmp_path),
        "@LUMI_HOME@": str(tmp_path),
        "@LUMI_PROJECT_DIR@": str(tmp_path / "project"),
        "@LUMI_SERVICE_UID@": str(service_user.pw_uid),
    }
    unit_names = (
        "lumistripe-web.service",
        "lumistripe-spotify.service",
        "lumistripe-update-check.service",
        "lumistripe-update.service",
        "lumistripe-update-check.timer",
    )
    unit_paths = []
    for name in unit_names:
        text = _unit(name)
        for placeholder, replacement in replacements.items():
            text = text.replace(placeholder, replacement)
        # The helper is installed only on a LumiStripe host. Keep the real
        # command covered by the assertions above and substitute an installed
        # executable here so systemd-analyze can validate unit syntax offline.
        text = text.replace(
            f"{tmp_path}/project/target/rust-deploy/current/"
            "lumistripe-deploy-helper",
            "/bin/true",
        )
        rendered_path = tmp_path / name
        rendered_path.write_text(text, encoding="utf-8")
        unit_paths.append(str(rendered_path))

    result = subprocess.run(
        [analyzer, "verify", "--man=no", *unit_paths],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
