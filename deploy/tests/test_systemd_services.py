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
    assert "Environment=LUMI_PYTHON_VERSION=@LUMI_PYTHON_VERSION@" in web_service
    assert "LUMI_WEB_BACKEND=python" in web_service

    environment_template = _unit("lumistripe-web.env.example")
    assert _section_values(environment_template, "", "LUMI_WEB_BACKEND") == ["rust"]

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
        "@LUMI_PYTHON_VERSION@": "3.12",
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
            f"{tmp_path}/project/rust/target/rust-deploy/current/"
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
