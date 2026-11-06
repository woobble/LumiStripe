from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1]
ADMIN = DEPLOY / "lumistripe-admin"
ADMIN_TEXT = ADMIN.read_text(encoding="utf-8")
UPDATE_SUPPORT = (DEPLOY / "update-support.sh").read_text(encoding="utf-8")
INSTALLER = (DEPLOY / "install.sh").read_text(encoding="utf-8")
UPDATER = (DEPLOY / "update.sh").read_text(encoding="utf-8")
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
        'printf "spotify_backend=%s\\nspotify_action=%s\\nspotify_target=%s\\n" '
        '"$ADMIN_SPOTIFY_BACKEND" "$ADMIN_SPOTIFY_ACTION" "$ADMIN_SPOTIFY_TARGET"'
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
    script: str,
    *arguments: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", 'source "$1"; shift; ' + script, "bash", str(ADMIN), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        env=env,
        input=input_text,
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


def _provider_call(
    script: str,
    *arguments: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; shift; ' + script,
            "bash",
            str(DEPLOY / "spotify-provider.sh"),
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
        input=input_text,
    )


def _provider_switch_call(
    tmp_path: Path,
    *,
    current: str,
    target: str,
    web_active: bool,
    soloist_active: bool,
    web_enabled: bool = True,
    soloist_enabled: bool = False,
    health_fail_first: bool = False,
    fail_action: str = "",
) -> subprocess.CompletedProcess[str]:
    config = tmp_path / "lumistripe-web.env"
    config.write_text(f"LUMI_SPOTIFY_BACKEND={current}\n", encoding="utf-8")
    key_file = tmp_path / "lumistripe-spotify.env"
    key_file.write_text("LUMI_SPOTIFY_API_KEY=existing-key\n", encoding="utf-8")
    unit = tmp_path / "lumistripe-spotify.service"
    sudoers = tmp_path / "lumistripe-spotify.sudoers"
    if soloist_active or soloist_enabled:
        unit.write_text("existing Soloist unit\n", encoding="utf-8")
    backup_root = tmp_path / "runtime"
    backup_root.mkdir()

    harness = r'''
WEB_ENV_FILE="$1"
SOLOIST_ENV_FILE="$2"
SOLOIST_UNIT="$3"
SOLOIST_SUDOERS="$4"
TEST_ROOT="$5"
TARGET="$6"
WEB_ACTIVE="$7"
SOLOIST_ACTIVE="$8"
WEB_ENABLED="$9"
SOLOIST_ENABLED="${10}"
HEALTH_FAIL_FIRST="${11}"
FAIL_ACTION="${12}"
HEALTH_CALLS=0
ACTIONS=

provider_load_context() {
  SERVICE_USER="$(id -un)"
  SERVICE_GROUP="$(id -gn)"
  SERVICE_HOME="$TEST_ROOT"
  PROJECT_DIR="$TEST_ROOT"
  ACTIVE_RELEASE="$TEST_ROOT/release"
}
provider_validate_existing_soloist_files() { :; }
provider_require_backend() { printf 'CHECKED_PROVIDER=%s\n' "$1"; }
provider_acquire_update_lock() { :; }
provider_create_backup_directory() {
  BACKUP_DIR="$(mktemp -d "$TEST_ROOT/provider-switch.XXXXXX")"
  chmod 700 "$BACKUP_DIR"
}
provider_cleanup_backup() {
  local name
  for name in web-env soloist-unit soloist-sudoers; do
    rm -f -- "$BACKUP_DIR/$name" "$BACKUP_DIR/$name.present" "$BACKUP_DIR/$name.absent"
  done
  rmdir -- "$BACKUP_DIR"
  BACKUP_DIR=
}
provider_service_active() {
  case "$1" in
    lumistripe-web.service) [[ "$WEB_ACTIVE" == 1 ]] ;;
    lumistripe-spotify.service) [[ "$SOLOIST_ACTIVE" == 1 ]] ;;
  esac
}
provider_service_enabled() {
  case "$1" in
    lumistripe-web.service) [[ "$WEB_ENABLED" == 1 ]] ;;
    lumistripe-spotify.service) [[ "$SOLOIST_ENABLED" == 1 ]] ;;
  esac
}
provider_web_health() {
  HEALTH_CALLS=$((HEALTH_CALLS + 1))
  [[ "$HEALTH_FAIL_FIRST" != 1 || "$HEALTH_CALLS" != 1 ]]
}
provider_service_state() {
  if provider_service_active "$1"; then printf 'Active\n'; else printf 'Inactive\n'; fi
}
provider_render_soloist_unit() { printf 'rendered Soloist unit\n' > "$SOLOIST_UNIT"; }
systemctl() {
  local action="$1" service
  case "$action" in
    stop|start|enable)
      service="$2"
      ACTIONS="${ACTIONS:+$ACTIONS,}$action:$service"
      ;;
    disable)
      if [[ "$2" == --now ]]; then
        service="$3"
        ACTIONS="${ACTIONS:+$ACTIONS,}disable-now:$service"
      else
        service="$2"
        ACTIONS="${ACTIONS:+$ACTIONS,}disable:$service"
      fi
      ;;
    daemon-reload) return 0 ;;
    *) printf 'unexpected systemctl command: %s\n' "$*" >&2; return 1 ;;
  esac
  [[ "$FAIL_ACTION" != "$action:$service" ]] || return 1
  case "$action:$service" in
    stop:lumistripe-web.service) WEB_ACTIVE=0 ;;
    stop:lumistripe-spotify.service) SOLOIST_ACTIVE=0 ;;
    start:lumistripe-web.service) WEB_ACTIVE=1 ;;
    start:lumistripe-spotify.service) SOLOIST_ACTIVE=1 ;;
    enable:lumistripe-web.service) WEB_ENABLED=1 ;;
    enable:lumistripe-spotify.service) SOLOIST_ENABLED=1 ;;
    disable:lumistripe-web.service) WEB_ENABLED=0 ;;
    disable:lumistripe-spotify.service) SOLOIST_ENABLED=0 ;;
    disable-now:lumistripe-spotify.service) SOLOIST_ACTIVE=0; SOLOIST_ENABLED=0 ;;
    disable-now:lumistripe-web.service) WEB_ACTIVE=0; WEB_ENABLED=0 ;;
  esac
}
write_spotify_backend_setting() {
  local file="$1" backend="$2" temporary="$1.test-temp"
  sed "s/^LUMI_SPOTIFY_BACKEND=.*/LUMI_SPOTIFY_BACKEND=$backend/" "$file" > "$temporary"
  chmod --reference="$file" "$temporary"
  mv -fT -- "$temporary" "$file"
  WEB_ENV_CHANGED=1
}
switch_status=0
provider_switch "$TARGET" || switch_status=$?
printf 'SWITCH_STATUS=%s\n' "$switch_status"
printf 'FINAL_BACKEND=%s\n' "$(read_spotify_backend "$WEB_ENV_FILE")"
printf 'FINAL_WEB_ACTIVE=%s\nFINAL_SOLOIST_ACTIVE=%s\n' "$WEB_ACTIVE" "$SOLOIST_ACTIVE"
printf 'FINAL_WEB_ENABLED=%s\nFINAL_SOLOIST_ENABLED=%s\n' "$WEB_ENABLED" "$SOLOIST_ENABLED"
printf 'HEALTH_CALLS=%s\nACTIONS=%s\n' "$HEALTH_CALLS" "$ACTIONS"
'''
    return _provider_call(
        harness,
        str(config),
        str(key_file),
        str(unit),
        str(sudoers),
        str(backup_root),
        target,
        "1" if web_active else "0",
        "1" if soloist_active else "0",
        "1" if web_enabled else "0",
        "1" if soloist_enabled else "0",
        "1" if health_fail_first else "0",
        fail_action,
        input_text="y\n",
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
    assert "spotify" in main_help.stdout

    update_help = _help("update", "--help")
    assert update_help.returncode == 0
    assert "--mode binary|auto|source" in update_help.stdout
    assert "--offline COMMIT" in update_help.stdout

    install_help = _help("install", "--help")
    assert "--spotify-backend librespot|soloist|disabled" in install_help.stdout

    repair_help = _help("repair", "--help")
    assert repair_help.returncode == 0
    assert "--source PATH" in repair_help.stdout
    assert "--spotify-backend" not in repair_help.stdout

    spotify_help = _help("spotify", "--help")
    assert spotify_help.returncode == 0
    assert "use librespot" in spotify_help.stdout
    assert "disable" in spotify_help.stdout


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

    provider_command = _parse("spotify", "use", "soloist")
    assert "parse_status=0" in provider_command.stdout
    assert "spotify_action=use" in provider_command.stdout
    assert "spotify_target=soloist" in provider_command.stdout

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
        ("repair", "--source", "/opt/lumistripe", "--spotify-backend", "disabled"),
        ("spotify", "use", "disabled"),
        ("spotify", "status", "--anything"),
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
    fresh = _support_call(
        'resolve_spotify_backend "" "$1" librespot', str(env_file)
    )
    assert fresh.returncode == 0
    assert fresh.stdout.strip() == "librespot"

    env_file.write_text("LUMI_SPOTIFY_BACKEND=disabled\n", encoding="utf-8")
    disabled = _support_call(
        'resolve_spotify_backend "" "$1" librespot', str(env_file)
    )
    assert disabled.returncode == 0
    assert disabled.stdout.strip() == "disabled"

    env_file.write_text("LUMI_SPOTIFY_BACKEND=soloist\n", encoding="utf-8")
    retained_soloist = _support_call(
        'resolve_spotify_backend "" "$1" librespot', str(env_file)
    )
    assert retained_soloist.returncode == 0
    assert retained_soloist.stdout.strip() == "soloist"

    env_file.write_text("LUMI_SPOTIFY_BACKEND=unknown\n", encoding="utf-8")
    invalid = _support_call('resolve_spotify_backend "" "$1"', str(env_file))
    assert invalid.returncode != 0
    assert "expected librespot, soloist, or disabled" in invalid.stderr

    env_file.write_text(
        "LUMI_SPOTIFY_BACKEND=librespot\nLUMI_SPOTIFY_BACKEND=soloist\n",
        encoding="utf-8",
    )
    duplicate = _support_call('resolve_spotify_backend "" "$1"', str(env_file))
    assert duplicate.returncode != 0


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
    assert 'LUMI_SPOTIFY_BACKEND=librespot' in (DEPLOY / "lumistripe-web.env.example").read_text(
        encoding="utf-8"
    )
    assert "INSTALL_DEFAULT_SPOTIFY_BACKEND=librespot" in INSTALLER
    assert '"$ENV_FILE" "$SELECTED_SPOTIFY_BACKEND"' in INSTALLER
    assert 'if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]]' in INSTALLER
    assert '[[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]] || return 1' in INSTALLER
    assert '[[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]] || return 1' in (
        (DEPLOY / "update.sh").read_text(encoding="utf-8")
    )
    create_soloist_env = INSTALLER[
        INSTALLER.index("create_spotify_environment_file() {"):
        INSTALLER.index("spotify_environment_ready() {", INSTALLER.index("create_spotify_environment_file() {"))
    ]
    assert 'if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]]' in create_soloist_env
    assert 'Spotify Soloist API key (leave blank to skip Spotify)' in create_soloist_env
    assert create_soloist_env.index('if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]]') < create_soloist_env.index("read -r -s")
    assert 'if [[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]]' in INSTALLER
    assert "spotify-provider.sh \\" in UPDATE_SUPPORT
    assert "lumistripe-spotify-service.sudoers \\" in UPDATE_SUPPORT
    support_install = UPDATE_SUPPORT[
        UPDATE_SUPPORT.index('if [[ "${SELECTED_SPOTIFY_BACKEND:-soloist}" == soloist ]]'):
        UPDATE_SUPPORT.index('fi\n  sed \\', UPDATE_SUPPORT.index('if [[ "${SELECTED_SPOTIFY_BACKEND:-soloist}" == soloist ]]'))
    ]
    assert "install_file_if_changed" in support_install
    assert 'if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]]' in UPDATER
    assert '[[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]] || return 1' in UPDATER
    assert "libatomic1 libpulse0" in UPDATER
    soloist_phase = INSTALLER[
        INSTALLER.index('lumi_phase_start "Soloist"'):
        INSTALLER.index('lumi_phase_end "Soloist"', INSTALLER.index('lumi_phase_start "Soloist"'))
    ]
    assert soloist_phase.index("if spotify_environment_ready; then") < soloist_phase.index(
        "install_spotify_soloist"
    )
    web_unit = (DEPLOY / "lumistripe-web.service").read_text(encoding="utf-8")
    assert "Wants=network-online.target bluetooth.service" in web_unit
    assert "lumistripe-spotify.service" not in web_unit


def test_existing_installation_detection_uses_trusted_evidence_and_legacy_fallback() -> None:
    detector = INSTALLER[
        INSTALLER.index("detect_legacy_spotify_installation() {"):
        INSTALLER.index("\nif [[ $EUID", INSTALLER.index("detect_legacy_spotify_installation() {"))
    ]
    assert 'trusted_existing_file "$candidate" 1' in detector
    assert 'trusted_existing_file "$ENV_FILE" 0' in detector
    assert "INSTALL_DEFAULT_SPOTIFY_BACKEND=soloist" in detector
    assert '"${LUMI_SPOTIFY_BACKEND:-}" "$ENV_FILE" "$INSTALL_DEFAULT_SPOTIFY_BACKEND"' in INSTALLER


def test_partial_existing_web_configuration_is_not_treated_as_a_fresh_install(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "lumistripe-web.env"
    env_file.write_text("LUMI_SPOTIFY_BACKEND=soloist\n", encoding="utf-8")
    command = r'''
ADMIN_STATE_PRESENT=0
ADMIN_STATE_SERVICE_USER=
ADMIN_STATE_PROJECT_DIR=
ADMIN_STATE_DEPLOYMENT_MODE=
ADMIN_UNIT_SERVICE_USER=
ADMIN_UNIT_PROJECT_DIR=
ADMIN_INSTALLATION_DETECTED=0
ADMIN_COMMAND=install
ADMIN_USER_OPTION=
ADMIN_PROJECT_OPTION=
ADMIN_SOURCE_OPTION=
ADMIN_ORIGIN=installed
ADMIN_WEB_ENV="$1"
ADMIN_WEB_UNIT="$1/missing-web.service"
ADMIN_SPOTIFY_ENV="$1/missing-soloist.env"
admin_read_install_state() { return 0; }
admin_read_unit_configuration() { return 0; }
admin_resolve_metadata
printf 'INSTALLATION_DETECTED=%s\n' "$ADMIN_INSTALLATION_DETECTED"
'''
    result = _source_call(command, str(env_file))
    assert result.returncode == 0, result.stderr
    assert "INSTALLATION_DETECTED=1" in result.stdout


def test_offline_and_normal_updates_preserve_provider_and_validate_rollback_support() -> None:
    assert 'SELECTED_SPOTIFY_BACKEND="$(resolve_spotify_backend "" "$WEB_ENV_FILE")"' in UPDATER
    assert "write_spotify_backend_setting" not in UPDATER
    active_guard = UPDATER.index("The active rollback release does not support librespot")
    fetch = UPDATER.index("git_user -c core.hooksPath=/dev/null fetch")
    target_guard = UPDATER.index("The verified target release does not support the configured librespot provider")
    checkout_switch = UPDATER.index("git_user -c core.hooksPath=/dev/null switch --detach")
    assert active_guard < fetch < target_guard < checkout_switch
    assert 'if [[ "$SELECTED_SPOTIFY_BACKEND" == librespot && "$EFFECTIVE_DEPLOY_MODE" != source ]]' in UPDATER
    source_build = UPDATER.index("    build_rust_backend\n", checkout_switch)
    source_provider_check = UPDATER.index(
        "The source-built target does not support the configured librespot provider",
        source_build,
    )
    assert checkout_switch < source_build < source_provider_check
    assert 'if [[ -n "$OFFLINE_COMMIT" ]]; then' in UPDATER


def test_legacy_key_prerequisite_requires_protected_configuration(tmp_path: Path) -> None:
    key_file = tmp_path / "lumistripe-spotify.env"
    key_file.write_text("LUMI_SPOTIFY_API_KEY=existing-key\n", encoding="utf-8")
    key_file.chmod(0o600)
    result = _provider_call(
        'SERVICE_USER="$(id -un)"; SOLOIST_ENV_FILE="$1"; '
        'if provider_soloist_key_ready; then printf "KEY_READY=1\\n"; '
        'else printf "KEY_READY=0\\n"; fi',
        str(key_file),
    )
    assert result.returncode == 0, result.stderr
    assert "KEY_READY=1" in result.stdout

    key_file.chmod(0o640)
    unsafe = _provider_call(
        'SERVICE_USER="$(id -un)"; SOLOIST_ENV_FILE="$1"; '
        'if provider_soloist_key_ready; then printf "KEY_READY=1\\n"; '
        'else printf "KEY_READY=0\\n"; fi',
        str(key_file),
    )
    assert unsafe.returncode == 0, unsafe.stderr
    assert "KEY_READY=0" in unsafe.stdout


def test_native_data_directory_must_be_private_and_service_owned(tmp_path: Path) -> None:
    data_dir = tmp_path / "native-state"
    data_dir.mkdir(mode=0o700)
    command = (
        'SERVICE_USER="$(id -un)"; runuser() { shift 3; "$@"; }; '
        'if provider_native_data_writable "$1"; then printf "NATIVE_DIR_READY=1\\n"; '
        'else printf "NATIVE_DIR_READY=0\\n"; fi'
    )
    safe = _provider_call(command, str(data_dir))
    assert safe.returncode == 0, safe.stderr
    assert "NATIVE_DIR_READY=1" in safe.stdout

    data_dir.chmod(0o755)
    unsafe = _provider_call(command, str(data_dir))
    assert unsafe.returncode == 0, unsafe.stderr
    assert "NATIVE_DIR_READY=0" in unsafe.stdout


def test_release_backend_check_requires_the_requested_clap_value(tmp_path: Path) -> None:
    web_binary = tmp_path / "lumistripe-web"
    web_binary.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' 'Options: --spotify-backend <SPOTIFY_BACKEND> [possible values: librespot, soloist, disabled]'\n",
        encoding="utf-8",
    )
    web_binary.chmod(0o755)
    user = subprocess.check_output(["id", "-un"], text=True).strip()
    supported = _support_call(
        'runuser() { shift 3; "$@"; }; '
        'spotify_web_binary_supports_backend "$1" librespot "$2"',
        str(web_binary),
        user,
    )
    assert supported.returncode == 0, supported.stderr

    web_binary.write_text("#!/usr/bin/env bash\nprintf '%s\\n' 'No Spotify backend option'\n", encoding="utf-8")
    unsupported = _support_call(
        'runuser() { shift 3; "$@"; }; '
        'spotify_web_binary_supports_backend "$1" librespot "$2"',
        str(web_binary),
        user,
    )
    assert unsupported.returncode != 0


def test_provider_switch_uses_mocked_prerequisites_and_keeps_both_credential_stores(
    tmp_path: Path,
) -> None:
    native_state = tmp_path / "native-state"
    native_state.mkdir(mode=0o700)
    native_credentials = native_state / "credentials.json"
    native_credentials.write_text("native credentials\n", encoding="utf-8")
    native_credentials.chmod(0o600)
    soloist_env = tmp_path / "lumistripe-spotify.env"
    soloist_env.write_text("LUMI_SPOTIFY_API_KEY=existing-key\n", encoding="utf-8")

    result = _provider_switch_call(
        tmp_path,
        current="soloist",
        target="librespot",
        web_active=True,
        soloist_active=True,
        web_enabled=True,
        soloist_enabled=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CHECKED_PROVIDER=librespot" in result.stdout
    assert "SWITCH_STATUS=0" in result.stdout
    assert "FINAL_BACKEND=librespot" in result.stdout
    assert "FINAL_WEB_ACTIVE=1" in result.stdout
    assert "FINAL_SOLOIST_ACTIVE=0" in result.stdout
    actions = result.stdout.split("ACTIONS=", 1)[1].splitlines()[0].split(",")
    assert actions.index("stop:lumistripe-spotify.service") < actions.index(
        "start:lumistripe-web.service"
    )
    assert native_credentials.read_text(encoding="utf-8") == "native credentials\n"
    assert soloist_env.read_text(encoding="utf-8") == "LUMI_SPOTIFY_API_KEY=existing-key\n"


@pytest.mark.parametrize(
    ("current", "target", "soloist_active"),
    [
        ("soloist", "librespot", True),
        ("librespot", "soloist", False),
        ("librespot", "disabled", False),
        ("disabled", "librespot", False),
        ("disabled", "soloist", False),
    ],
)
def test_provider_switch_completes_each_supported_migration(
    tmp_path: Path,
    current: str,
    target: str,
    soloist_active: bool,
) -> None:
    result = _provider_switch_call(
        tmp_path,
        current=current,
        target=target,
        web_active=True,
        soloist_active=soloist_active,
        web_enabled=True,
        soloist_enabled=soloist_active,
    )

    assert result.returncode == 0, result.stderr
    assert "SWITCH_STATUS=0" in result.stdout
    assert f"FINAL_BACKEND={target}" in result.stdout
    assert "FINAL_WEB_ACTIVE=1" in result.stdout
    assert f"FINAL_SOLOIST_ACTIVE={int(target == 'soloist')}" in result.stdout
    assert f"FINAL_SOLOIST_ENABLED={int(target == 'soloist')}" in result.stdout
    assert (tmp_path / "lumistripe-spotify.env").read_text(encoding="utf-8") == (
        "LUMI_SPOTIFY_API_KEY=existing-key\n"
    )


def test_failed_provider_switch_restores_provider_config_and_service_state(
    tmp_path: Path,
) -> None:
    result = _provider_switch_call(
        tmp_path,
        current="librespot",
        target="disabled",
        web_active=True,
        soloist_active=False,
        web_enabled=True,
        health_fail_first=True,
    )

    assert result.returncode == 0
    assert "SWITCH_STATUS=1" in result.stdout
    assert "FINAL_BACKEND=librespot" in result.stdout
    assert "FINAL_WEB_ACTIVE=1" in result.stdout
    assert "FINAL_WEB_ENABLED=1" in result.stdout
    assert "HEALTH_CALLS=2" in result.stdout
    assert "could not fully restored" not in result.stderr


def test_failed_first_soloist_switch_removes_new_unit_enablement(
    tmp_path: Path,
) -> None:
    result = _provider_switch_call(
        tmp_path,
        current="librespot",
        target="soloist",
        web_active=True,
        soloist_active=False,
        web_enabled=True,
        soloist_enabled=False,
        health_fail_first=True,
    )

    assert result.returncode == 0
    assert "SWITCH_STATUS=1" in result.stdout
    assert "FINAL_BACKEND=librespot" in result.stdout
    assert "FINAL_WEB_ACTIVE=1" in result.stdout
    assert "FINAL_WEB_ENABLED=1" in result.stdout
    assert "FINAL_SOLOIST_ACTIVE=0" in result.stdout
    assert "FINAL_SOLOIST_ENABLED=0" in result.stdout
    assert not (tmp_path / "lumistripe-spotify.service").exists()


def test_soloist_service_start_failure_restores_the_previous_provider(
    tmp_path: Path,
) -> None:
    result = _provider_switch_call(
        tmp_path,
        current="librespot",
        target="soloist",
        web_active=True,
        soloist_active=False,
        web_enabled=True,
        soloist_enabled=False,
        fail_action="start:lumistripe-spotify.service",
    )

    assert result.returncode == 0
    assert "SWITCH_STATUS=1" in result.stdout
    assert "FINAL_BACKEND=librespot" in result.stdout
    assert "FINAL_WEB_ACTIVE=1" in result.stdout
    assert "FINAL_SOLOIST_ACTIVE=0" in result.stdout
    assert "FINAL_SOLOIST_ENABLED=0" in result.stdout
    assert "Soloist did not become active" in result.stderr


def test_native_provider_switch_rejects_two_active_connect_receivers(tmp_path: Path) -> None:
    result = _provider_switch_call(
        tmp_path,
        current="librespot",
        target="soloist",
        web_active=True,
        soloist_active=True,
        web_enabled=True,
        soloist_enabled=True,
    )

    assert result.returncode == 0
    assert "SWITCH_STATUS=1" in result.stdout
    assert "FINAL_BACKEND=librespot" in result.stdout
    assert "both Spotify Connect providers are active" in result.stderr
    assert "ACTIONS=\n" in result.stdout


def test_native_status_does_not_require_soloist_credentials_and_is_read_only(
    tmp_path: Path,
) -> None:
    config = tmp_path / "lumistripe-web.env"
    native_data = tmp_path / "native-state"
    config.write_text(
        f"LUMI_SPOTIFY_BACKEND=librespot\nLUMI_SPOTIFY_LIBRESPOT_DATA_DIR={native_data}\n",
        encoding="utf-8",
    )
    command = r'''
WEB_ENV_FILE="$1"
SOLOIST_ENV_FILE="$2/missing-soloist.env"
TEST_HOME="$2"
provider_load_context() {
  SERVICE_USER="$(id -un)"
  SERVICE_HOME="$TEST_HOME"
  ACTIVE_RELEASE="$TEST_HOME/release"
}
provider_native_data_writable() { return 0; }
provider_service_state() { printf 'Inactive\n'; }
spotify_web_binary_supports_backend() { return 0; }
provider_print_status
'''
    result = _provider_call(command, str(config), str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "Spotify provider: librespot" in result.stdout
    assert "Spotify configured: Yes" in result.stdout
    assert "Spotify process: LumiStripe web process" in result.stdout
    assert "Spotify pairing: Pairing required" in result.stdout
    assert "Spotify API key" not in result.stdout

    status_function = (DEPLOY / "spotify-provider.sh").read_text(encoding="utf-8")
    status_function = status_function.split("provider_print_status() {", 1)[1].split(
        "\nprovider_main() {", 1
    )[0]
    for mutation in (
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "systemctl enable",
        "write_spotify_backend_setting",
        "install -d",
    ):
        assert mutation not in status_function


def test_uninstall_preserves_both_provider_credential_stores() -> None:
    assert 'remove_file "$ENV_FILE"' in UNINSTALLER
    assert 'remove_file "$SPOTIFY_ENV_FILE"' not in UNINSTALLER
    assert 'remove_directory "$SERVICE_HOME/.local/share/lumistripe/spotify"' not in UNINSTALLER
    assert "remove_directory /var/lib/lumistripe/spotify\n" not in UNINSTALLER
    assert 'remove_file "$SOLOIST_BIN"' in UNINSTALLER


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
            (
                "[Service]\nUser=pi\nWorkingDirectory=/srv/fallback\n"
                "Environment=LUMI_PROJECT_DIR=/opt/lumistripe\n"
            ),
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
        'admin_parse_arguments repair --source "$1" --user "$2" || exit $?; '
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
    assert "CHILD_SPOTIFY_BACKEND=" in result.stdout


def test_spotify_command_delegates_to_the_root_owned_provider_snapshot(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "root-snapshot"
    _mock_child(snapshot / "spotify-provider.sh")
    service_user = subprocess.check_output(["id", "-un"], text=True).strip()
    command = (
        'admin_parse_arguments spotify use librespot || exit $?; '
        'ADMIN_SNAPSHOT_DIR="$1"; ADMIN_PROJECT_DIR="$2"; ADMIN_PROJECT_VALID=1; '
        'ADMIN_SERVICE_USER="$3"; ADMIN_INSTALLATION_DETECTED=1; '
        'admin_require_installed() { return 0; }; '
        'admin_require_project() { return 0; }; '
        'admin_check_user_exists() { return 0; }; '
        'admin_safe_system_file() { return 0; }; '
        'admin_clear_unapproved_environment() { :; }; '
        'admin_run_spotify'
    )
    result = _source_call(
        command,
        str(snapshot),
        str(tmp_path / "runtime"),
        service_user,
    )
    assert result.returncode == 0, result.stderr
    assert "CHILD_ARGS=use librespot" in result.stdout


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
    assert "rm -rf -- \"$BACKUP_DIR\"" not in (DEPLOY / "spotify-provider.sh").read_text(
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

