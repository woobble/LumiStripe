from __future__ import annotations

import grp
import os
import pwd
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1]
ROOT = DEPLOY.parent


def _current_user_group() -> tuple[str, str]:
    return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name


def _bash(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script, "bash", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def _soloist_fixture(
    tmp_path: Path,
    *,
    old: str | None = None,
    candidate: str | None = None,
    download_mode: str = "ok",
    refresh: bool = False,
    offline: bool = False,
    fail_rename: bool = False,
    old_mode: int = 0o755,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    project = tmp_path / "project"
    helper = project / "target/rust-deploy/current/lumistripe-deploy-helper"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
command_name="$1"
shift
case "$command_name" in
  soloist-check)
    while (($#)); do
      if [[ "$1" == --binary ]]; then binary="$2"; shift 2; else shift; fi
    done
    [[ -f "$binary" && -x "$binary" ]] && grep -Fq '# SOLOIST_FIXTURE_VALID' "$binary"
    ;;
  soloist-download)
    while (($#)); do
      case "$1" in
        --archive) archive="$2"; shift 2 ;;
        --destination) destination="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    printf '%s\\n' "$SOLOIST_DOWNLOAD_MODE" >> "$SOLOIST_DOWNLOAD_LOG"
    printf 'archive fixture\\n' > "$archive"
    case "$SOLOIST_DOWNLOAD_MODE" in
      fail|bad_archive) exit 1 ;;
      *) cat "$SOLOIST_CANDIDATE_FILE" > "$destination"; chmod 755 "$destination" ;;
    esac
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    helper.chmod(0o755)

    home = tmp_path / "service-home"
    installed = home / ".local/bin/soloist"
    installed.parent.mkdir(parents=True)
    if old is not None:
        installed.write_text(old, encoding="utf-8")
        installed.chmod(old_mode)
    candidate_file = tmp_path / "candidate-soloist"
    candidate_file.write_text(candidate or _soloist_binary("1.2.3", 101), encoding="utf-8")
    candidate_file.chmod(0o755)

    download_log = tmp_path / "downloads.log"
    environment = os.environ.copy()
    service_user, service_group = _current_user_group()
    environment.update(
        {
            "PROJECT_DIR": str(project),
            "SERVICE_USER": service_user,
            "SERVICE_GROUP": service_group,
            "SOLOIST_CANDIDATE_FILE": str(candidate_file),
            "SOLOIST_DOWNLOAD_LOG": str(download_log),
            "SOLOIST_DOWNLOAD_MODE": download_mode,
            "LUMI_SOLOIST_REFRESH": "1" if refresh else "0",
            "LUMI_SOLOIST_OFFLINE": "1" if offline else "0",
            "SOLOIST_FAIL_RENAME": "1" if fail_rename else "0",
        }
    )
    script = r"""
set +e
as_user() {
  if [[ "$1" == mv && "${SOLOIST_FAIL_RENAME:-0}" == 1 ]]; then return 1; fi
  "$@"
}
source "$1"
install_spotify_soloist "$2" "$3" "$4"
status=$?
printf 'install_status=%s\n' "$status"
"""
    result = _bash(
        script,
        environment,
        str(DEPLOY / "soloist.sh"),
        str(home),
        service_user,
        service_group,
    )
    return result, installed, download_log


def _soloist_binary(version: str, build: int) -> str:
    return (
        "#!/bin/sh\n"
        "# SOLOIST_FIXTURE_VALID\n"
        f"if [ \"${{1:-}}\" = --version ]; then printf 'Soloist {version} build {build}\\n'; fi\n"
    )


def test_soloist_reuses_valid_binary_without_downloading(tmp_path: Path) -> None:
    old = _soloist_binary("1.2.3", 100)
    result, installed, download_log = _soloist_fixture(tmp_path, old=old)

    assert result.returncode == 0, result.stderr
    assert "install_status=10" in result.stdout
    assert "Reusing the valid Spotify Soloist executable" in result.stdout
    assert installed.read_text(encoding="utf-8") == old
    assert not download_log.exists()


def test_offline_soloist_reuses_existing_binary_even_when_refresh_is_requested(
    tmp_path: Path,
) -> None:
    old = _soloist_binary("1.2.3", 100)
    result, installed, download_log = _soloist_fixture(
        tmp_path, old=old, refresh=True, offline=True
    )

    assert result.returncode == 0, result.stderr
    assert "install_status=10" in result.stdout
    assert "Offline installation is reusing" in result.stdout
    assert installed.read_text(encoding="utf-8") == old
    assert not download_log.exists()


def test_soloist_installs_newer_candidate_and_reuses_identical_candidate(
    tmp_path: Path,
) -> None:
    old = _soloist_binary("1.2.3", 100)
    newer = _soloist_binary("1.2.4", 101)
    result, installed, download_log = _soloist_fixture(
        tmp_path, old=old, candidate=newer, refresh=True
    )

    assert result.returncode == 0, result.stderr
    assert "install_status=0" in result.stdout
    assert "atomically" in result.stdout
    assert installed.read_text(encoding="utf-8") == newer
    assert download_log.read_text(encoding="utf-8").splitlines() == ["ok"]

    identical_dir = tmp_path / "identical"
    identical_dir.mkdir()
    result, installed, _ = _soloist_fixture(
        identical_dir, old=old, candidate=old, refresh=True
    )
    assert result.returncode == 0, result.stderr
    assert "install_status=10" in result.stdout
    assert "byte-identical" in result.stdout
    assert installed.read_text(encoding="utf-8") == old


def test_soloist_build_timestamp_prevents_downgrade(tmp_path: Path) -> None:
    old = _soloist_binary("2.0.0", 100)
    older = _soloist_binary("9.9.9", 99)
    result, installed, _ = _soloist_fixture(
        tmp_path, old=old, candidate=older, refresh=True
    )

    assert result.returncode == 0, result.stderr
    assert "install_status=10" in result.stdout
    assert "not newer" in result.stdout
    assert installed.read_text(encoding="utf-8") == old


@pytest.mark.parametrize("mode", ["fail", "bad_archive"])
def test_soloist_download_or_archive_failure_preserves_existing_binary(
    tmp_path: Path, mode: str
) -> None:
    old = _soloist_binary("1.2.3", 100)
    result, installed, _ = _soloist_fixture(
        tmp_path, old=old, download_mode=mode, refresh=True
    )

    assert result.returncode == 0, result.stderr
    assert "install_status=1" in result.stdout
    assert installed.read_text(encoding="utf-8") == old


def test_soloist_rejects_invalid_candidate_before_installing(tmp_path: Path) -> None:
    invalid = "#!/bin/sh\nprintf 'not a Soloist fixture\\n'\n"
    result, installed, _ = _soloist_fixture(
        tmp_path, candidate=invalid, refresh=True
    )

    assert result.returncode == 0, result.stderr
    assert "install_status=1" in result.stdout
    assert "failed validation" in result.stderr
    assert not installed.exists()


def test_soloist_repairs_metadata_for_identical_binary(tmp_path: Path) -> None:
    old = _soloist_binary("1.2.3", 100)
    result, installed, _ = _soloist_fixture(
        tmp_path, old=old, candidate=old, refresh=True, old_mode=0o700
    )

    assert result.returncode == 0, result.stderr
    assert "install_status=10" in result.stdout
    assert installed.read_text(encoding="utf-8") == old
    assert installed.stat().st_mode & 0o777 == 0o755


def test_failed_atomic_rename_keeps_old_soloist_and_cleans_staging_file(
    tmp_path: Path,
) -> None:
    old = _soloist_binary("1.2.3", 100)
    result, installed, _ = _soloist_fixture(
        tmp_path,
        old=old,
        candidate=_soloist_binary("1.2.4", 101),
        refresh=True,
        fail_rename=True,
    )

    assert result.returncode == 0, result.stderr
    assert "install_status=1" in result.stdout
    assert installed.read_text(encoding="utf-8") == old
    assert not list(installed.parent.glob(".soloist.*"))


def _apt_environment(tmp_path: Path, packages: dict[str, str]) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_file = tmp_path / "dpkg-state"
    state_file.write_text(
        "".join(f"{package}={version}\n" for package, version in packages.items()),
        encoding="utf-8",
    )
    apt_log = tmp_path / "apt.log"
    commands = {
        "dpkg-query": """#!/usr/bin/env bash
for package in "$@"; do :; done
version="$(awk -F= -v package="$package" '$1 == package { print substr($0, index($0, "=") + 1); exit }' "$DPKG_STATE_FILE")"
[[ -n "$version" ]] || exit 1
printf 'install ok installed %s\\n' "$version"
""",
        "dpkg": """#!/usr/bin/env bash
[[ "$1" == --compare-versions && "$3" == ge ]] || exit 2
highest="$(printf '%s\\n%s\\n' "$2" "$4" | sort -V | tail -n 1)"
[[ "$highest" == "$2" ]]
""",
        "apt-get": """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$APT_LOG"
""",
    }
    for name, body in commands.items():
        command = bin_dir / name
        command.write_text(body, encoding="utf-8")
        command.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
            "DPKG_STATE_FILE": str(state_file),
            "APT_LOG": str(apt_log),
        }
    )
    return environment


def test_apt_helper_skips_update_when_required_packages_are_satisfied(
    tmp_path: Path,
) -> None:
    environment = _apt_environment(tmp_path, {"foo": "1.5", "bar": "2.0"})
    result = _bash(
        r'set -e; source "$1"; install_missing_apt_packages test "foo|1.4" bar',
        environment,
        str(DEPLOY / "package-provisioning.sh"),
    )

    assert result.returncode == 0, result.stderr
    assert "skipping APT" in result.stdout
    assert not (tmp_path / "apt.log").exists()


def test_apt_helper_refreshes_package_lists_only_once_for_missing_packages(
    tmp_path: Path,
) -> None:
    environment = _apt_environment(tmp_path, {"old": "1.2"})
    result = _bash(
        r"""
set -e
source "$1"
install_missing_apt_packages first missing-one "old|1.4"
install_missing_apt_packages second missing-two
""",
        environment,
        str(DEPLOY / "package-provisioning.sh"),
    )

    assert result.returncode == 0, result.stderr
    commands = (tmp_path / "apt.log").read_text(encoding="utf-8").splitlines()
    assert commands.count("update") == 1
    assert any("missing-one old" in command for command in commands)
    assert any("missing-two" in command for command in commands)


def test_apt_helper_fails_offline_when_a_required_package_is_missing(
    tmp_path: Path,
) -> None:
    environment = _apt_environment(tmp_path, {})
    environment["OFFLINE_COMMIT"] = "a" * 40
    result = _bash(
        'source "$1"; install_missing_apt_packages offline missing-package',
        environment,
        str(DEPLOY / "package-provisioning.sh"),
    )

    assert result.returncode == 1
    assert "Offline installation requires" in result.stderr
    assert not (tmp_path / "apt.log").exists()


def test_managed_file_install_reports_only_real_changes(tmp_path: Path) -> None:
    source = tmp_path / "source.conf"
    destination = tmp_path / "managed.conf"
    source.write_text("managed configuration\n", encoding="utf-8")
    service_user, service_group = _current_user_group()
    script = r"""
set -e
as_user() { "$@"; }
source "$1"
install_file_if_changed "$2" "$3" "$4" "$5" 644
printf 'first=%s\n' "$FILE_CHANGED"
install_file_if_changed "$2" "$3" "$4" "$5" 644
printf 'second=%s\n' "$FILE_CHANGED"
chmod 600 "$3"
install_file_if_changed "$2" "$3" "$4" "$5" 644
printf 'metadata=%s\n' "$FILE_CHANGED"
"""
    result = _bash(
        script,
        os.environ.copy(),
        str(DEPLOY / "update-support.sh"),
        str(source),
        str(destination),
        service_user,
        service_group,
    )

    assert result.returncode == 0, result.stderr
    assert "first=1" in result.stdout
    assert "second=0" in result.stdout
    assert "metadata=1" in result.stdout
    assert destination.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize(
    ("active", "required", "should_restart"),
    [("1", "0", False), ("1", "1", True), ("0", "0", True)],
)
def test_spotify_restart_requires_change_or_inactive_service(
    tmp_path: Path, active: str, required: str, should_restart: bool
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$SYSTEMCTL_LOG"
case "$1" in
  is-active) [[ "$SPOTIFY_ACTIVE" == 1 ]] ;;
  *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    log = tmp_path / "systemctl.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
            "SYSTEMCTL_LOG": str(log),
            "SPOTIFY_ACTIVE": active,
        }
    )
    result = _bash(
        'set -e; source "$1"; restart_spotify_service_if_needed "$2"',
        environment,
        str(DEPLOY / "update-support.sh"),
        required,
    )

    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="utf-8").splitlines()
    assert ("restart lumistripe-spotify.service" in commands) is should_restart


def test_raspberry_pi_validation_is_manual_main_only() -> None:
    pi_workflow = (ROOT / ".github/workflows/pi-validation.yml").read_text(
        encoding="utf-8"
    )
    ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "on:\n  workflow_dispatch:\n" in pi_workflow
    assert "github.ref == 'refs/heads/main'" in pi_workflow
    assert "ref: main" in pi_workflow
    assert "rpi5" in pi_workflow
    assert "rpi5" not in ci_workflow


def test_phase_timing_reports_named_monotonic_spans() -> None:
    result = _bash(
        'set -e; source "$1"; lumi_phase_start setup; lumi_phase_end setup; lumi_phase_report',
        os.environ.copy(),
        str(DEPLOY / "timing.sh"),
    )

    assert result.returncode == 0, result.stderr
    assert "Installation/update timing (monotonic elapsed)" in result.stdout
    assert "setup" in result.stdout
    for script_name in ("install.sh", "update.sh"):
        script = (DEPLOY / script_name).read_text(encoding="utf-8")
        assert 'lumi_phase_start "Source build"' in script
        assert 'lumi_phase_end "Source build"' in script


