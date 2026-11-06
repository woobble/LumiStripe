from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1]
ADMIN = DEPLOY / "lumistripe-admin"
ADMIN_TEXT = ADMIN.read_text(encoding="utf-8")
UPDATE_SUPPORT = (DEPLOY / "update-support.sh").read_text(encoding="utf-8")
INSTALLER = (DEPLOY / "install.sh").read_text(encoding="utf-8")
UNINSTALLER = (DEPLOY / "uninstall.sh").read_text(encoding="utf-8")


def _parse(*arguments: str) -> subprocess.CompletedProcess[str]:
    command = (
        'source "$1"; shift; '
        'if admin_parse_arguments "$@"; then parse_status=0; '
        'else parse_status=$?; fi; '
        'printf "parse_status=%s\\ncommand=%s\\nuser=%s\\nproject=%s\\n" '
        '"$parse_status" "$ADMIN_COMMAND" "$ADMIN_USER_OPTION" "$ADMIN_PROJECT_OPTION"; '
        'printf "mode=%s\\noffline=%s\\nrefresh=%s\\nyes=%s\\nsource=%s\\n" '
        '"$ADMIN_MODE" "$ADMIN_OFFLINE" "$ADMIN_REFRESH_SOLOIST" '
        '"$ADMIN_ASSUME_YES" "$ADMIN_SOURCE_OPTION"; '
        'printf "spotify_backend=%s\\n" "$ADMIN_SPOTIFY_BACKEND"'
    )
    return subprocess.run(
        ["bash", "-c", command, "bash", str(ADMIN), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _help(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ADMIN), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _source_call(
    script: str, *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", 'source "$1"; shift; ' + script, "bash", str(ADMIN), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        env=env,
    )


def _support_call(
    script: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; shift; ' + script,
            "bash",
            str(DEPLOY / "update-support.sh"),
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _mock_child(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'CHILD_ARGS=%s\\n' \"$*\"\n"
        "printf 'CHILD_PROJECT=%s\\n' \"${LUMI_PROJECT_DIR:-}\"\n"
        "printf 'CHILD_USER=%s\\n' \"${LUMI_SERVICE_USER:-}\"\n"
        "printf 'CHILD_MODE=%s\\n' \"${LUMI_DEPLOY_MODE:-}\"\n"
        "printf 'CHILD_SPOTIFY_BACKEND=%s\\n' \"${LUMI_SPOTIFY_BACKEND:-}\"\n"
        "printf 'CHILD_OFFLINE=%s\\n' \"${LUMI_OFFLINE_COMMIT:-}\"\n"
        "printf 'CHILD_REFRESH=%s\\n' \"${LUMI_SOLOIST_REFRESH:-}\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_command_help_documents_the_supported_admin_interface() -> None:
    main_help = _help("--help")
    assert main_help.returncode == 0
    for command in ("install", "update", "status", "repair", "uninstall", "help"):
        assert command in main_help.stdout

    update_help = _help("update", "--help")
    assert update_help.returncode == 0
    assert "--mode binary|auto|source" in update_help.stdout
    assert "--offline COMMIT" in update_help.stdout

    install_help = _help("install", "--help")
    assert "--spotify-backend librespot|soloist|disabled" in install_help.stdout

    repair_help = _help("repair", "--help")
    assert repair_help.returncode == 0
    assert "--source PATH" in repair_help.stdout
    assert "--spotify-backend librespot|soloist|disabled" in repair_help.stdout


def test_parser_defaults_and_forwards_supported_values() -> None:
    default = _parse("update")
    assert "parse_status=0" in default.stdout
    assert "mode=binary" in default.stdout

    custom = _parse(
        "update",
        "--user",
        "pi",
        "--project-dir",
        "/opt/lumistripe",
        "--mode",
        "source",
        "--refresh-soloist",
    )
    assert "parse_status=0" in custom.stdout
    assert "user=pi" in custom.stdout
    assert "project=/opt/lumistripe" in custom.stdout
    assert "mode=source" in custom.stdout
    assert "refresh=1" in custom.stdout

    selected_backend = _parse("install", "--spotify-backend", "librespot")
    assert "parse_status=0" in selected_backend.stdout
    assert "spotify_backend=librespot" in selected_backend.stdout

    offline = _parse(
        "update",
        "--mode=binary",
        "--offline",
        "0123456789abcdef0123456789abcdef01234567",
    )
    assert "parse_status=0" in offline.stdout
    assert "offline=0123456789abcdef0123456789abcdef01234567" in offline.stdout

    automatic = _parse("update", "--mode", "auto")
    assert "parse_status=0" in automatic.stdout
    assert "mode=auto" in automatic.stdout

    uninstall = _parse("uninstall", "--yes")
    assert "parse_status=0" in uninstall.stdout
    assert "yes=1" in uninstall.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ("unknown",),
        ("update", "--unknown"),
        ("update", "--mode", "invalid"),
        ("update", "--offline", "shortsha"),
        ("update", "--offline", "0123456789abcdef0123456789abcdef01234567", "--mode", "source"),
        ("update", "--user", "not a user"),
        ("update", "--api-key", "secret"),
        ("install", "--spotify-backend", "unknown"),
        ("update", "--spotify-backend", "librespot"),
        ("status", "--mode", "binary"),
        ("repair",),
        ("install", "--yes"),
        ("uninstall", "--offline", "0123456789abcdef0123456789abcdef01234567"),
        ("update", "--user", "pi", "--user", "pi"),
    ],
)
def test_parser_rejects_unknown_invalid_and_conflicting_options(
    arguments: tuple[str, ...],
) -> None:
    result = _parse(*arguments)
    assert "parse_status=2" in result.stdout
    assert "Error:" in result.stderr


def test_spotify_backend_selection_preserves_legacy_default_and_validates_overrides(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "lumistripe-web.env"
    env_file.write_text("LUMI_SPOTIFY_BACKEND=librespot\n", encoding="utf-8")
    configured = _support_call('resolve_spotify_backend "" "$1"', str(env_file))
    assert configured.returncode == 0
    assert configured.stdout.strip() == "librespot"

    explicit = _support_call(
        'resolve_spotify_backend "$1" "$2"', "disabled", str(env_file)
    )
    assert explicit.returncode == 0
    assert explicit.stdout.strip() == "disabled"

    env_file.write_text("LUMI_PAIRING_CODE=1234\n", encoding="utf-8")
    legacy = _support_call('resolve_spotify_backend "" "$1"', str(env_file))
    assert legacy.returncode == 0
    assert legacy.stdout.strip() == "soloist"

    env_file.write_text("LUMI_SPOTIFY_BACKEND=unknown\n", encoding="utf-8")
    invalid = _support_call('resolve_spotify_backend "" "$1"', str(env_file))
    assert invalid.returncode != 0
    assert "expected librespot, soloist, or disabled" in invalid.stderr


def test_spotify_backend_setting_rewrites_atomically_and_preserves_other_values(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "lumistripe-web.env"
    env_file.write_text(
        "LUMI_PAIRING_CODE=1234\n"
        "LUMI_SPOTIFY_BACKEND=soloist\n"
        "LUMI_SPOTIFY_BACKEND=soloist\n",
        encoding="utf-8",
    )
    owner = subprocess.check_output(["id", "-un"], text=True).strip()
    group = subprocess.check_output(["id", "-gn"], text=True).strip()
    result = _support_call(
        'write_spotify_backend_setting "$1" disabled "$2" "$3"; '
        'printf "changed=%s\\n" "$WEB_ENV_CHANGED"',
        str(env_file),
        owner,
        group,
    )
    assert result.returncode == 0, result.stderr
    assert "changed=1" in result.stdout
    assert env_file.read_text(encoding="utf-8") == (
        "LUMI_PAIRING_CODE=1234\nLUMI_SPOTIFY_BACKEND=disabled\n"
    )
    assert env_file.stat().st_mode & 0o777 == 0o600

    unchanged = _support_call(
        'write_spotify_backend_setting "$1" disabled "$2" "$3"; '
        'printf "changed=%s\\n" "$WEB_ENV_CHANGED"',
        str(env_file),
        owner,
        group,
    )
    assert unchanged.returncode == 0, unchanged.stderr
    assert "changed=0" in unchanged.stdout


def test_native_deployment_skips_soloist_setup_and_keeps_runtime_audio_library() -> None:
    assert 'LUMI_SPOTIFY_BACKEND=soloist' in (DEPLOY / "lumistripe-web.env.example").read_text(
        encoding="utf-8"
    )
    assert 'if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]]' in INSTALLER
    assert '[[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]] || return 1' in INSTALLER
    assert '[[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]] || return 1' in (
        DEPLOY / "update.sh"
    ).read_text(encoding="utf-8")
    assert "libatomic1 libpulse0" in (DEPLOY / "update.sh").read_text(
        encoding="utf-8"
    )
    web_unit = (DEPLOY / "lumistripe-web.service").read_text(encoding="utf-8")
    assert "Wants=network-online.target bluetooth.service" in web_unit
    assert "lumistripe-spotify.service" not in web_unit


def test_project_discovery_migrates_from_trusted_systemd_configuration() -> None:
    assert "admin_read_install_state || return 1" in ADMIN_TEXT
    assert "admin_read_unit_configuration || return 1" in ADMIN_TEXT
    assert (
        'candidate="${ADMIN_PROJECT_OPTION:-${ADMIN_STATE_PROJECT_DIR:-${ADMIN_UNIT_PROJECT_DIR:-}}}"'
        in ADMIN_TEXT
    )
    assert "Environment=LUMI_PROJECT_DIR=" in ADMIN_TEXT
    assert "WorkingDirectory=" in ADMIN_TEXT
    assert "source \"$ADMIN_STATE_FILE\"" not in ADMIN_TEXT


def test_repeat_install_and_repair_preserve_saved_mode_unless_overridden() -> None:
    command = (
        'ADMIN_COMMAND=install; ADMIN_MODE=binary; '
        'ADMIN_STATE_DEPLOYMENT_MODE=source; ADMIN_SEEN_OPTIONS=(); '
        'admin_apply_saved_install_mode; printf "MODE=%s\\n" "$ADMIN_MODE"; '
        'ADMIN_COMMAND=repair; ADMIN_MODE=binary; ADMIN_SEEN_OPTIONS[mode]=1; '
        'admin_apply_saved_install_mode; printf "EXPLICIT=%s\\n" "$ADMIN_MODE"'
    )
    result = _source_call(command)
    assert result.returncode == 0, result.stderr
    assert "MODE=source" in result.stdout
    assert "EXPLICIT=binary" in result.stdout


def test_systemd_directory_validation_requires_root_owned_non_writable_dirs() -> None:
    function = ADMIN_TEXT.split("admin_safe_system_directory() {", 1)[1].split("\n}", 1)[0]
    assert '"$owner" == 0' in function
    assert "(8#$mode & 0022) == 0" in function


@pytest.mark.parametrize(
    ("unit_configuration", "expected_project"),
    [
        (
            "[Service]\nUser=pi\nWorkingDirectory=/srv/fallback\n"
            "Environment=LUMI_PROJECT_DIR=/opt/lumistripe\n",
            "/opt/lumistripe",
        ),
        ("[Service]\nUser=pi\nWorkingDirectory=/srv/lumistripe\n", "/srv/lumistripe"),
    ],
)
def test_legacy_systemd_project_discovery_reads_trusted_unit_values(
    tmp_path: Path, unit_configuration: str, expected_project: str
) -> None:
    unit = tmp_path / "lumistripe-web.service"
    unit.write_text(unit_configuration, encoding="utf-8")
    command = (
        'admin_safe_system_file() { return 0; }; '
        'admin_safe_system_directory() { return 0; }; '
        'admin_read_unit_configuration "$1"; '
        'printf "USER=%s\\nPROJECT=%s\\n" '
        '"$ADMIN_UNIT_SERVICE_USER" "$ADMIN_UNIT_PROJECT_DIR"'
    )
    result = _source_call(command, str(unit))
    assert result.returncode == 0, result.stderr
    assert "USER=pi" in result.stdout
    assert f"PROJECT={expected_project}" in result.stdout


def test_install_state_uses_atomic_root_owned_data_updates() -> None:
    assert "project_dir=%s\\n" in UPDATE_SUPPORT
    assert "service_user=%s\\n" in UPDATE_SUPPORT
    assert "deployment_mode=%s\\n" in UPDATE_SUPPORT
    assert "if [[ \"$requested_nginx_default_removed\" == 1 ]]; then" in UPDATE_SUPPORT
    assert "nginx_default_removed=1" in UPDATE_SUPPORT
    assert 'chmod 600 -- "$temp_file"' in UPDATE_SUPPORT
    assert 'chown root:root -- "$temp_file"' in UPDATE_SUPPORT
    assert 'mv -fT -- "$temp_file" "$LUMI_INSTALL_STATE_FILE"' in UPDATE_SUPPORT
    assert "source \"$STATE_FILE\"" not in INSTALLER


def test_install_state_parser_treats_values_as_plain_data(tmp_path: Path) -> None:
    state = tmp_path / "install-state"
    state.write_text(
        "service_user=pi\nproject_dir=/opt/lumistripe=blue\n",
        encoding="utf-8",
    )
    command = 'source "$1"; admin_state_value "$2" "$3"'
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(ADMIN), str(state), "project_dir"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "/opt/lumistripe=blue"

    state.write_text(
        "service_user=pi\nservice_user=other\n",
        encoding="utf-8",
    )
    duplicate = subprocess.run(
        ["bash", "-c", command, "bash", str(ADMIN), str(state), "service_user"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert duplicate.returncode == 2


def test_admin_delegates_to_existing_scripts_and_privileged_snapshot() -> None:
    assert 'script="$ADMIN_SNAPSHOT_DIR/update.sh"' in ADMIN_TEXT
    assert 'script="$ADMIN_SNAPSHOT_DIR/uninstall.sh"' in ADMIN_TEXT
    assert 'script="$target/deploy/install.sh"' in ADMIN_TEXT
    assert 'script="$target/deploy/install.sh"' in ADMIN_TEXT
    assert "admin_validate_trusted_checkout \"$target\" \"$ADMIN_SERVICE_USER\"" in ADMIN_TEXT
    assert "install.sh update.sh uninstall.sh update-support.sh lumistripe-admin" in UPDATE_SUPPORT
    assert '"$UPDATE_LIBEXEC_DIR/lumistripe-admin" /usr/local/sbin/lumistripe-admin' in UPDATE_SUPPORT
    assert 'remove_file "$ADMIN_ENTRYPOINT"' in UNINSTALLER


def test_install_delegation_uses_selected_checkout_and_options(tmp_path: Path) -> None:
    project = tmp_path / "trusted-checkout"
    installer = project / "deploy" / "install.sh"
    _mock_child(installer)
    command = (
        'admin_parse_arguments install --user pi --project-dir "$1" --mode source '
        '--spotify-backend librespot || exit $?; '
        'ADMIN_PROJECT_DIR="$1"; ADMIN_PROJECT_VALID=1; ADMIN_SERVICE_USER=pi; '
        'admin_check_platform() { return 0; }; '
        'admin_validate_trusted_checkout() { return 0; }; '
        'admin_print_header() { :; }; admin_finish_operation() { :; }; '
        'admin_run_install'
    )
    result = _source_call(command, str(project))
    assert result.returncode == 0, result.stderr
    assert "CHILD_PROJECT=" + str(project) in result.stdout
    assert "CHILD_USER=pi" in result.stdout
    assert "CHILD_MODE=source" in result.stdout
    assert "CHILD_SPOTIFY_BACKEND=librespot" in result.stdout


def test_repeated_install_delegates_to_the_root_owned_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "root-snapshot"
    _mock_child(snapshot / "install.sh")
    project = tmp_path / "runtime-project"
    service_user = subprocess.check_output(["id", "-un"], text=True).strip()
    command = (
        'admin_parse_arguments install --user "$1" --project-dir "$2" || exit $?; '
        'ADMIN_ORIGIN=installed; ADMIN_SNAPSHOT_DIR="$3"; ADMIN_PROJECT_DIR="$2"; '
        'ADMIN_PROJECT_VALID=1; ADMIN_SERVICE_USER="$1"; '
        'admin_check_platform() { return 0; }; '
        'admin_safe_system_file() { return 0; }; '
        'admin_print_header() { :; }; admin_finish_operation() { :; }; '
        'admin_run_install'
    )
    result = _source_call(command, service_user, str(project), str(snapshot))
    assert result.returncode == 0, result.stderr
    assert f"CHILD_PROJECT={project}" in result.stdout
    assert f"CHILD_USER={service_user}" in result.stdout


def test_update_delegation_uses_snapshot_and_forwards_only_named_options(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "root-snapshot"
    _mock_child(snapshot / "update.sh")
    service_user = subprocess.check_output(["id", "-un"], text=True).strip()
    command = (
        'admin_parse_arguments update --user "$1" --project-dir "$2" '
        '--mode auto --refresh-soloist || exit $?; '
        'ADMIN_SNAPSHOT_DIR="$3"; ADMIN_PROJECT_DIR="$2"; ADMIN_PROJECT_VALID=1; '
        'ADMIN_SERVICE_USER="$1"; ADMIN_INSTALLATION_DETECTED=1; '
        'admin_safe_system_file() { return 0; }; '
        'admin_print_header() { :; }; admin_finish_operation() { :; '
        'printf "POST_UPDATE\\n"; }; '
        'admin_run_update'
    )
    result = _source_call(command, service_user, str(tmp_path / "installed"), str(snapshot))
    assert result.returncode == 0, result.stderr
    assert f"CHILD_PROJECT={tmp_path / 'installed'}" in result.stdout
    assert f"CHILD_USER={service_user}" in result.stdout
    assert "CHILD_MODE=auto" in result.stdout
    assert "CHILD_REFRESH=1" in result.stdout
    assert "CHILD_ARGS=" in result.stdout


def test_repair_requires_and_delegates_to_trusted_source_checkout(tmp_path: Path) -> None:
    project = tmp_path / "repair-checkout"
    installer = project / "deploy" / "install.sh"
    _mock_child(installer)
    (project / ".git").mkdir(parents=True)
    (project / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (project / "apps" / "lumistripe-web" / "frontend").mkdir(parents=True)
    (project / "apps" / "lumistripe-web" / "Cargo.toml").write_text(
        "[package]\nname='web'\nversion='0.1.0'\n", encoding="utf-8"
    )
    service_user = subprocess.check_output(["id", "-un"], text=True).strip()
    command = (
        'admin_parse_arguments repair --source "$1" --user "$2" '
        '--spotify-backend disabled || exit $?; '
        'ADMIN_SERVICE_USER="$2"; ADMIN_INSTALLATION_DETECTED=1; '
        'admin_check_platform() { return 0; }; '
        'admin_validate_trusted_checkout() { return 0; }; '
        'admin_print_header() { :; }; admin_finish_operation() { :; }; '
        'admin_run_repair'
    )
    result = _source_call(command, str(project), service_user)
    assert result.returncode == 0, result.stderr
    assert f"CHILD_PROJECT={project}" in result.stdout
    assert f"CHILD_USER={service_user}" in result.stdout
    assert "CHILD_SPOTIFY_BACKEND=disabled" in result.stdout


@pytest.mark.parametrize(("assume_yes", "expected_args"), [("0", ""), ("1", "--yes")])
def test_uninstall_delegation_keeps_confirmation_default(
    tmp_path: Path, assume_yes: str, expected_args: str
) -> None:
    snapshot = tmp_path / "root-snapshot"
    _mock_child(snapshot / "uninstall.sh")
    service_user = subprocess.check_output(["id", "-un"], text=True).strip()
    command = (
        'admin_parse_arguments uninstall '
        '$(if [[ "$1" == 1 ]]; then printf -- --yes; fi) || exit $?; '
        'ADMIN_SNAPSHOT_DIR="$2"; ADMIN_PROJECT_DIR="$3"; ADMIN_PROJECT_VALID=1; '
        'ADMIN_SERVICE_USER="$4"; ADMIN_INSTALLATION_DETECTED=1; '
        'admin_safe_system_file() { return 0; }; admin_run_uninstall'
    )
    result = _source_call(
        command,
        assume_yes,
        str(snapshot),
        str(tmp_path / "installed"),
        service_user,
    )
    assert result.returncode == 0, result.stderr
    assert f"CHILD_ARGS={expected_args}" in result.stdout


def test_status_path_is_read_only_and_does_not_run_update_or_install() -> None:
    start = ADMIN_TEXT.index("admin_print_status() {")
    end = ADMIN_TEXT.index("\nadmin_print_header() {", start)
    status_function = ADMIN_TEXT[start:end]
    for mutation in (
        "systemctl start",
        "systemctl restart",
        "systemctl enable",
        "apt-get",
        "git fetch",
        "install -",
        "mv -",
    ):
        assert mutation not in status_function
    assert 'status)\n      admin_require_installed_entrypoint || return 1\n      admin_print_status' in ADMIN_TEXT


def test_source_trust_checks_and_uninstall_confirmation_remain_enforced() -> None:
    trust_function = ADMIN_TEXT[
        ADMIN_TEXT.index("admin_validate_trusted_checkout() {"):
        ADMIN_TEXT.index("\nadmin_check_user_exists() {")
    ]
    assert "runuser -u \"$trusted_user\" -- test -w \"$candidate\"" in trust_function
    assert "! -L \"$source_file\"" in trust_function
    assert "admin_validate_trusted_checkout \"$target\" \"$ADMIN_SERVICE_USER\"" in ADMIN_TEXT
    assert 'read -r -p "Continue with LumiStripe uninstall? [y/N] " confirmation' in UNINSTALLER
    assert 'admin_run_script "$script" --yes' in ADMIN_TEXT
    assert "lumistripe-admin" not in (DEPLOY / "lumistripe-update.sudoers").read_text(
        encoding="utf-8"
    )


def test_script_runner_propagates_failure_and_interruption_exit_codes(tmp_path: Path) -> None:
    child = tmp_path / "mock-command.sh"
    child.write_text("#!/usr/bin/env bash\nexit \"$1\"\n", encoding="utf-8")
    child.chmod(0o755)
    command = (
        'source "$1"; shift; '
        'if admin_run_script "$1" "$2"; then exit 0; else exit $?; fi'
    )
    for status in (37, 130, 143):
        result = subprocess.run(
            ["bash", "-c", command, "bash", str(ADMIN), str(child), str(status)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert result.returncode == status

