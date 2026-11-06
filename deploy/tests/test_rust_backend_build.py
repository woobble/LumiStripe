from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BUILDER = Path(__file__).parents[1] / "build-rust-backend.sh"
UPDATE_SUPPORT = Path(__file__).parents[1] / "update-support.sh"
INSTALLER = Path(__file__).parents[1] / "install.sh"
UPDATER = Path(__file__).parents[1] / "update.sh"
ENV_TEMPLATE = Path(__file__).parents[1] / "lumistripe-web.env.example"
RELEASE_ARTIFACT = Path(__file__).parents[1] / "release_artifact.py"


def _git(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _commit(project: Path, name: str) -> str:
    marker = project / name
    marker.write_text(name, encoding="utf-8")
    _git(project, "add", name)
    _git(project, "commit", "--quiet", "-m", name)
    return _git(project, "rev-parse", "HEAD")


def _builder_environment(tmp_path: Path, cargo_log: Path) -> dict[str, str]:
    fake_cargo = tmp_path / "fake-cargo"
    fake_cargo.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "if args == ['+1.99.1', '--version']:\n"
        "    print('cargo 1.99.1')\n"
        "    raise SystemExit(0)\n"
        "with open(os.environ['LUMI_TEST_CARGO_LOG'], 'a') as log:\n"
        "    log.write(' '.join(args) + '\\n')\n"
        "if os.environ.get('LUMI_TEST_FAIL_ON') and os.environ['LUMI_TEST_FAIL_ON'] in args:\n"
        "    raise SystemExit(17)\n"
        "name = next((candidate for candidate in ('lumistripe-engine', 'lumistripe-web', 'lumistripe-deploy-helper', 'lumistripe') if candidate in args), 'unknown')\n"
        "build_id = os.environ['LUMI_TEST_BUILD_ID']\n"
        "output = pathlib.Path(os.environ['CARGO_TARGET_DIR']) / 'release' / name\n"
        "output.parent.mkdir(parents=True, exist_ok=True)\n"
        "script = '#!/bin/sh\\n'\n"
        "script += \"printf '%s\\\\n' '\" + build_id + ':' + name + \"' > \\\"$LUMI_TEST_LAUNCH_LOG\\\"\\n\"\n"
        "script += 'printf \"%s\\\\n\" \"$*\" >> \"$LUMI_TEST_LAUNCH_LOG\"\\n'\n"
        "output.write_text(script, encoding='utf-8')\n"
        "output.chmod(0o755)\n",
        encoding="utf-8",
    )
    fake_cargo.chmod(0o755)
    cargo_home = tmp_path / "home" / ".cargo"
    rustup_home = tmp_path / "home" / ".rustup"
    cargo_home.mkdir(parents=True)
    rustup_home.mkdir(parents=True)
    environment = os.environ.copy()
    environment.update(
        {
            "CARGO_HOME": str(cargo_home),
            "RUSTUP_HOME": str(rustup_home),
            "LUMI_RUST_CARGO_BIN": str(fake_cargo),
            "LUMI_TEST_CARGO_LOG": str(cargo_log),
            "LUMI_TEST_BUILD_ID": "first",
            "HOME": str(tmp_path / "home"),
        }
    )
    return environment


def _release(releases_dir: Path, commit: str, build_id: str) -> Path:
    release = releases_dir / commit
    release.mkdir(parents=True)
    (release / "source-commit").write_text(f"{commit}\n", encoding="utf-8")
    for name in (
        "lumistripe-web",
        "lumistripe-engine",
        "lumistripe-deploy-helper",
        "lumistripe",
    ):
        binary = release / name
        binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{build_id}:{name}'\n", encoding="utf-8")
        binary.chmod(0o755)
    static = release / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("release fixture\n", encoding="utf-8")
    (static / "assets/app.js").write_text("fixture\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, str(RELEASE_ARTIFACT), "seal-local", "--release-dir", str(release), "--commit", commit],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return release


def _run_deploy_support(
    project: Path, function: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    (project / "deploy").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RELEASE_ARTIFACT, project / "deploy/release_artifact.py")
    command = 'set -Eeuo pipefail; PROJECT_DIR="$1"; source "$2"; shift 2; ' + function + ' "$@"'
    return subprocess.run(
        ["bash", "-c", command, function, str(project), str(UPDATE_SUPPORT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("LUMI_WEB_BACKEND=rust\n", "rust"),
        ('LUMI_WEB_BACKEND="rust"\n', "rust"),
        ("LUMI_WEB_BACKEND='rust'\n", "rust"),
        ("# LUMI_WEB_BACKEND=rust\n", ""),
        ("LUMI_WEB_BACKEND=python\nLUMI_WEB_BACKEND=rust\n", "rust"),
    ],
)
def test_update_support_reads_backend_without_evaluating_env_file(
    tmp_path: Path, contents: str, expected: str
) -> None:
    environment_file = tmp_path / "lumistripe-web.env"
    marker = tmp_path / "must-not-be-created"
    environment_file.write_text(contents, encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; read_web_backend "$2"',
            "read-web-backend",
            str(UPDATE_SUPPORT),
            str(environment_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert not marker.exists()


def test_update_support_does_not_execute_environment_file(tmp_path: Path) -> None:
    environment_file = tmp_path / "lumistripe-web.env"
    marker = tmp_path / "must-not-be-created"
    value = f"rust; touch {marker}"
    environment_file.write_text(f"LUMI_WEB_BACKEND={value}\n", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; read_web_backend "$2"',
            "read-web-backend",
            str(UPDATE_SUPPORT),
            str(environment_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == value
    assert not marker.exists()


@pytest.mark.parametrize(
    ("requested", "contents", "expected"),
    [
        ("", "LUMI_WEB_BACKEND=rust\n", "rust"),
        ("rust", "", "rust"),
        ("", "", "rust"),
    ],
)
def test_resolve_web_backend_selects_rust_only(
    tmp_path: Path, requested: str, contents: str, expected: str
) -> None:
    environment_file = tmp_path / "lumistripe-web.env"
    environment_file.write_text(contents, encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; resolve_web_backend "$2" "$3"',
            "resolve-web-backend",
            str(UPDATE_SUPPORT),
            requested,
            str(environment_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("requested", ["", "rust"])
def test_resolve_web_backend_rejects_python_without_changing_configuration(
    tmp_path: Path, requested: str
) -> None:
    environment_file = tmp_path / "lumistripe-web.env"
    original = "LUMI_PAIRING_CODE=1234\nLUMI_WEB_BACKEND=python\n"
    environment_file.write_text(original, encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; resolve_web_backend "$2" "$3"',
            "resolve-web-backend",
            str(UPDATE_SUPPORT),
            requested,
            str(environment_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "LUMI_WEB_BACKEND=python is no longer supported" in result.stderr
    assert "settings file" in result.stderr
    assert environment_file.read_text(encoding="utf-8") == original


def test_web_launcher_defaults_to_rust_and_requires_an_active_release(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    (project / "deploy").mkdir()
    (project / "deploy/update-support.sh").write_text(
        UPDATE_SUPPORT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    shutil.copyfile(RELEASE_ARTIFACT, project / "deploy/release_artifact.py")
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    for command in ("cat", "dirname"):
        executable = shutil.which(command)
        assert executable is not None
        (empty_path / command).symlink_to(executable)
    assert shutil.which("python", path=str(empty_path)) is None
    launch_log = tmp_path / "launch.log"
    environment = os.environ.copy()
    environment.pop("LUMI_WEB_BACKEND", None)
    environment.pop("LUMI_UV_BIN", None)
    environment.update(
        {
            "HOME": str(home),
            "PATH": str(empty_path),
            "LUMI_PROJECT_DIR": str(project),
            "LUMI_TEST_LAUNCH_LOG": str(launch_log),
        }
    )

    launcher = Path(__file__).parents[1] / "lumistripe-web-launcher.sh"
    result = subprocess.run(
        ["/bin/bash", str(launcher)],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 127
    assert "Rust release pointer is missing" in result.stderr
    assert "deploy/install.sh or deploy/update.sh" in result.stderr
    assert not launch_log.exists()


def test_web_launcher_rejects_python_backend(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "deploy").mkdir()
    (project / "deploy/update-support.sh").write_text(
        UPDATE_SUPPORT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    for command in ("cat", "dirname"):
        executable = shutil.which(command)
        assert executable is not None
        (empty_path / command).symlink_to(executable)
    assert shutil.which("python", path=str(empty_path)) is None
    launch_log = tmp_path / "launch.log"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": str(empty_path),
            "LUMI_WEB_BACKEND": "python",
            "LUMI_PROJECT_DIR": str(project),
            "LUMI_TEST_LAUNCH_LOG": str(launch_log),
        }
    )

    launcher = Path(__file__).parents[1] / "lumistripe-web-launcher.sh"
    result = subprocess.run(
        ["/bin/bash", str(launcher)],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 78
    assert "LUMI_WEB_BACKEND=python is no longer supported" in result.stderr
    assert not launch_log.exists()


def test_rust_cli_command_points_to_the_commit_matched_release(tmp_path: Path) -> None:
    project = tmp_path / "project"
    home = tmp_path / "service-home"
    release = project / "target/rust-deploy/releases/0123456789abcdef"
    release.mkdir(parents=True)
    current = project / "target/rust-deploy/current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to("releases/0123456789abcdef")
    binary = release / "lumistripe"
    marker = tmp_path / "cli-invoked"
    binary.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{marker}'\n", encoding="utf-8"
    )
    binary.chmod(0o755)

    script = f"""
set -Eeuo pipefail
PROJECT_DIR={str(project)!r}
as_user() {{ "$@"; }}
source "$1"
install_rust_cli_command "$2"
"$2/.local/bin/lumistripe-cli" --help
"""
    result = subprocess.run(
        ["/bin/bash", "-c", script, "install-rust-cli", str(UPDATE_SUPPORT), str(home)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    command_path = home / ".local/bin/lumistripe-cli"
    assert command_path.is_symlink()
    assert command_path.resolve() == binary
    assert marker.read_text(encoding="utf-8").strip() == "--help"


def test_legacy_release_migration_preserves_commit_matched_rollback_state(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    legacy_root = project / "rust/target/rust-deploy"
    legacy_releases = legacy_root / "releases"
    old_commit = "1" * 40
    first_commit = "2" * 40
    second_commit = "3" * 40
    old_release = _release(legacy_releases, old_commit, "old")
    (legacy_root / "current").symlink_to(f"releases/{old_commit}")

    migrated = _run_deploy_support(project, "migrate_legacy_rust_deploy_root")
    assert migrated.returncode == 0, migrated.stderr

    root = project / "target/rust-deploy"
    assert (root / ".layout-migrated-from-rust-target").read_text(
        encoding="utf-8"
    ).strip() == "layout-v1"
    assert os.readlink(root / "current") == f"releases/{old_commit}"
    for name in (
        "lumistripe-web",
        "lumistripe-engine",
        "lumistripe-deploy-helper",
        "lumistripe",
        "source-commit",
    ):
        assert (root / "releases" / old_commit / name).read_bytes() == (
            old_release / name
        ).read_bytes()
    assert (legacy_root / "releases" / old_commit).is_dir()

    _release(root / "releases", first_commit, "first")
    (root / "current").unlink()
    (root / "current").symlink_to(f"releases/{first_commit}")
    retained = _run_deploy_support(
        project, "retire_legacy_rust_deploy_root", first_commit, old_commit
    )
    assert retained.returncode == 0, retained.stderr
    assert (legacy_root / "releases" / old_commit).is_dir()

    _release(root / "releases", second_commit, "second")
    (root / "current").unlink()
    (root / "current").symlink_to(f"releases/{second_commit}")
    shutil.rmtree(root / "releases" / old_commit)
    retired = _run_deploy_support(
        project, "retire_legacy_rust_deploy_root", second_commit, first_commit
    )
    assert retired.returncode == 0, retired.stderr
    assert not legacy_root.exists()
    assert not (project / "rust").exists()
    assert os.readlink(root / "current") == f"releases/{second_commit}"
    assert not (root / "releases" / old_commit).exists()


def test_legacy_release_migration_refuses_incomplete_or_conflicting_state(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    old_commit = "4" * 40
    legacy_root = project / "rust/target/rust-deploy"
    legacy_release = _release(legacy_root / "releases", old_commit, "legacy")
    (legacy_release / "lumistripe-engine").unlink()
    (legacy_root / "current").symlink_to(f"releases/{old_commit}")

    incomplete = _run_deploy_support(project, "migrate_legacy_rust_deploy_root")
    assert incomplete.returncode != 0
    assert "left untouched" in incomplete.stderr
    assert (legacy_root / "current").is_symlink()
    assert not (project / "target/rust-deploy").exists()

    (legacy_release / "lumistripe-engine").write_text("engine", encoding="utf-8")
    (legacy_release / "lumistripe-engine").chmod(0o755)
    root = project / "target/rust-deploy"
    root.mkdir(parents=True)
    (root / "current").write_text("unrecognized state", encoding="utf-8")
    conflict = _run_deploy_support(project, "migrate_legacy_rust_deploy_root")
    assert conflict.returncode != 0
    assert "refusing to merge" in conflict.stderr
    assert (legacy_root / "current").is_symlink()
    assert (root / "current").read_text(encoding="utf-8") == "unrecognized state"


def test_installer_rejects_python_configuration_before_mutating_it(tmp_path: Path) -> None:
    environment_file = tmp_path / "lumistripe-web.env"
    original = (
        "LUMI_PAIRING_CODE=1234\n"
        "LUMI_WEB_BACKEND=python\n"
        "LUMISTRIPE_ENABLE_SPOTIFY=true\n"
    )
    environment_file.write_text(original, encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    assert installer.index('resolve_web_backend "${LUMI_WEB_BACKEND:-}" "$ENV_FILE"') < installer.index(
        "create_environment_file\n"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; resolve_web_backend "${LUMI_WEB_BACKEND:-}" "$2"',
            "installer-preflight",
            str(UPDATE_SUPPORT),
            str(environment_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "LUMI_WEB_BACKEND=python is no longer supported" in result.stderr
    assert environment_file.read_text(encoding="utf-8") == original
    assert "LUMI_WEB_BACKEND=" not in ENV_TEMPLATE.read_text(encoding="utf-8")


def test_binary_install_profile_skips_local_build_toolchains() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    updater = UPDATER.read_text(encoding="utf-8")
    assert "python3-minimal" in installer
    for text in (installer, updater):
        assert "uv " not in text
        assert "LUMI_PYTHON_VERSION" not in text
        assert "submodule" not in text.lower()
    assert "sync_dependencies" not in updater
    assert 'DEPLOY_MODE="${LUMI_DEPLOY_MODE:-binary}"' in installer
    assert 'DEPLOY_MODE="${LUMI_DEPLOY_MODE:-binary}"' in updater
    assert "install_release_verifier" in installer
    assert "stage_prebuilt_release" in installer
    assert "stage_prebuilt_release" in updater
    assert 'if [[ "$EFFECTIVE_DEPLOY_MODE" == source ]]; then' in installer
    assert 'if [[ "$EFFECTIVE_DEPLOY_MODE" == source ]]; then' in updater
    assert "resolve_web_backend" in installer
    assert "resolve_web_backend" in updater
    assert "  install_toolchains\n  install_project_dependencies\n  build_rust_release\n" in installer
    assert "  if stage_prebuilt_release; then" in installer
    assert updater.index('stage_prebuilt_release "$TARGET_COMMIT"') < updater.rindex(
        "require_source_toolchains"
    )


@pytest.mark.parametrize(
    ("mode", "status", "should_fallback"),
    [
        ("auto", 10, True),
        ("auto", 11, True),
        ("auto", 12, False),
        ("auto", 1, False),
        ("binary", 10, False),
        ("source", 11, False),
    ],
)
def test_source_fallback_is_limited_to_auto_missing_or_unsupported_artifacts(
    tmp_path: Path, mode: str, status: int, should_fallback: bool
) -> None:
    project = tmp_path / f"project-{mode}-{status}"
    result = _run_deploy_support(project, "may_fallback_to_source", mode, str(status))

    assert result.returncode == (0 if should_fallback else 1), result.stderr


def test_offline_updates_use_only_the_previously_verified_cache() -> None:
    updater = UPDATER.read_text(encoding="utf-8")

    assert 'OFFLINE_COMMIT="${LUMI_OFFLINE_COMMIT:-}"' in updater
    assert 'stage_prebuilt_release "$TARGET_COMMIT" --cached-only' in updater
    assert 'if [[ -z "$OFFLINE_COMMIT" ]]; then\n    install_release_verifier' in updater
    assert 'if [[ -n "$OFFLINE_COMMIT" ]]; then\n    if LUMI_SOLOIST_OFFLINE=1 install_spotify_soloist' in updater
    assert "fetch --prune" in updater


def test_offline_installs_require_a_cached_verified_commit() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    package_setup = installer[
        installer.index("install_system_packages() {"):
        installer.index("install_source_build_packages() {")
    ]
    stage_release = installer[
        installer.index("stage_prebuilt_release() {"):installer.index("create_environment_file() {")
    ]

    assert 'OFFLINE_COMMIT="${LUMI_OFFLINE_COMMIT:-}"' in installer
    assert '"$DEPLOY_MODE" == binary' in installer
    assert "install_args+=(--cached-only)" in stage_release
    assert '[[ "$DEPLOY_RELEASE_COMMIT" == "$OFFLINE_COMMIT" ]]' in stage_release
    assert 'if [[ -z "$OFFLINE_COMMIT" ]]; then\n    install_release_verifier' in installer
    assert 'if [[ -n "$OFFLINE_COMMIT" ]]; then' in package_setup
    assert "package_is_satisfied \"$package\"" in package_setup
    assert "Offline mode: required system packages are already installed; skipping APT." in package_setup
    assert "LUMI_SOLOIST_OFFLINE=1 install_spotify_soloist" in installer
    assert "Offline installation reused the existing Spotify Soloist executable." in installer


@pytest.mark.parametrize(
    ("architecture", "os_release", "supported"),
    [
        ("aarch64", "ID=ubuntu\nVERSION_CODENAME=resolute\n", True),
        ("aarch64", "ID=ubuntu\nVERSION_CODENAME=noble\n", False),
        ("aarch64", "ID=raspbian\nVERSION_CODENAME=trixie\n", True),
        ("aarch64", "ID=debian\nVERSION_CODENAME=bookworm\n", False),
        ("armv7l", "ID=ubuntu\nVERSION_CODENAME=resolute\n", False),
    ],
)
def test_shared_binary_target_support(
    tmp_path: Path, architecture: str, os_release: str, supported: bool
) -> None:
    os_release_file = tmp_path / "os-release"
    os_release_file.write_text(os_release, encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; binary_target_supported "$2" "$3"',
            "bash",
            str(UPDATE_SUPPORT),
            architecture,
            str(os_release_file),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is supported, result.stderr


def test_privileged_update_refresh_reads_only_the_root_owned_snapshot() -> None:
    support = UPDATE_SUPPORT.read_text(encoding="utf-8")
    refresh_support = support[support.index("install_update_support() {"):]

    assert 'source_file="$SCRIPT_DIR/$template"' in refresh_support
    assert '"$PROJECT_DIR/deploy/$template"' not in refresh_support
    assert '"$UPDATE_LIBEXEC_DIR/lumistripe-network.sudoers"' in refresh_support


@pytest.mark.skipif(os.geteuid() == 0, reason="builder intentionally rejects root")
def test_rust_update_build_promotes_a_complete_commit_matched_release(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    (project / "deploy").mkdir()
    (project / "deploy/update-support.sh").write_text(
        UPDATE_SUPPORT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    shutil.copyfile(RELEASE_ARTIFACT, project / "deploy/release_artifact.py")
    (project / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (project / ".gitignore").write_text("/target/\n", encoding="utf-8")
    static = project / "apps/lumistripe-web/static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("build fixture\n", encoding="utf-8")
    (static / "assets/app.js").write_text("fixture\n", encoding="utf-8")
    _git(project, "init", "--quiet")
    _git(project, "config", "user.name", "LumiStripe test")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "commit.gpgsign", "false")
    _git(project, "config", "core.hooksPath", "/dev/null")
    _git(project, "add", "Cargo.toml", ".gitignore", "deploy/update-support.sh", "deploy/release_artifact.py", "apps/lumistripe-web/static")
    first_commit = _commit(project, "first.txt")
    cargo_log = tmp_path / "cargo.log"
    environment = _builder_environment(tmp_path, cargo_log)

    result = subprocess.run(
        ["bash", str(BUILDER), str(project)],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    current = project / "target" / "rust-deploy" / "current"
    release = current.resolve()
    assert os.readlink(current) == f"releases/{first_commit}"
    assert (release / "source-commit").read_text(encoding="utf-8").strip() == first_commit
    assert "first:lumistripe-engine" in (release / "lumistripe-engine").read_text(
        encoding="utf-8"
    )
    assert "first:lumistripe-web" in (release / "lumistripe-web").read_text(
        encoding="utf-8"
    )
    assert "first:lumistripe-deploy-helper" in (
        release / "lumistripe-deploy-helper"
    ).read_text(encoding="utf-8")
    assert "first:lumistripe" in (release / "lumistripe").read_text(
        encoding="utf-8"
    )
    for name in (
        "lumistripe-web",
        "lumistripe-engine",
        "lumistripe-deploy-helper",
        "lumistripe",
    ):
        assert (release / name).stat().st_mode & 0o111

    commands = cargo_log.read_text(encoding="utf-8").splitlines()
    assert len(commands) == 4
    assert all(command.startswith("+1.99.1 build --locked --release") for command in commands)
    assert "lumistripe-engine" in commands[0]
    assert "--features bluez,pipewire" in commands[1]
    assert "lumistripe-deploy-helper" in commands[2]
    assert "lumistripe-cli" in commands[3]
    assert "--bin lumistripe" in commands[3]

    launch_log = tmp_path / "launch.log"
    launcher_env = environment.copy()
    launcher_env.update(
        {
            "LUMI_WEB_BACKEND": "rust",
            "LUMI_PROJECT_DIR": str(project),
            "LUMI_RUST_OUTPUT_BACKEND": "mock",
            "LUMISTRIPE_SETTINGS_FILE": str(tmp_path / "settings.json"),
            "LUMI_TEST_LAUNCH_LOG": str(launch_log),
        }
    )
    launcher_path = tmp_path / "launcher-path"
    launcher_path.mkdir()
    for utility in ("git", "cat", "readlink"):
        system_utility = Path("/usr/bin") / utility
        assert system_utility.is_file()
        os.symlink(system_utility, launcher_path / utility)
    launcher_env["PATH"] = str(launcher_path)
    for interpreter in ("python", "python3"):
        with pytest.raises(FileNotFoundError):
            subprocess.run([interpreter], env=launcher_env, check=False)

    launcher = Path(__file__).parents[1] / "lumistripe-web-launcher.sh"
    engine_binary = release / "lumistripe-engine"
    engine_contents = engine_binary.read_text(encoding="utf-8")
    engine_binary.unlink()
    launched = subprocess.run(
        ["/bin/bash", str(launcher)],
        check=False,
        env=launcher_env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    engine_binary.write_text(engine_contents, encoding="utf-8")
    engine_binary.chmod(0o755)
    assert launched.returncode == 0, launched.stderr
    launch_lines = launch_log.read_text(encoding="utf-8").splitlines()
    assert launch_lines[0] == "first:lumistripe-web"
    assert "--engine-binary" not in launch_lines[1]
    assert str(release / "lumistripe-engine") not in launch_lines[1]
    assert engine_binary.is_file()

    second_commit = _commit(project, "second.txt")
    stale_launch = subprocess.run(
        ["/bin/bash", str(launcher)],
        check=False,
        env=launcher_env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert stale_launch.returncode == 127
    assert "do not match" in stale_launch.stderr
    environment["LUMI_TEST_BUILD_ID"] = "second"
    environment["LUMI_TEST_FAIL_ON"] = "lumistripe-web"
    failed = subprocess.run(
        ["bash", str(BUILDER), str(project)],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 17, failed.stderr
    assert os.readlink(current) == f"releases/{first_commit}"
    assert not (
        project / "target" / "rust-deploy" / "releases" / second_commit
    ).exists()
    assert "first:lumistripe-engine" in (
        current.resolve() / "lumistripe-engine"
    ).read_text(encoding="utf-8")
    assert "first:lumistripe-web" in (current.resolve() / "lumistripe-web").read_text(
        encoding="utf-8"
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="builder intentionally rejects root")
@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_rust_update_build_refuses_sources_that_do_not_match_head(
    tmp_path: Path, dirty_kind: str
) -> None:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    (project / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (project / ".gitignore").write_text("/target/\n", encoding="utf-8")
    _git(project, "init", "--quiet")
    _git(project, "config", "user.name", "LumiStripe test")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "commit.gpgsign", "false")
    _git(project, "config", "core.hooksPath", "/dev/null")
    _git(project, "add", "Cargo.toml", ".gitignore")
    _commit(project, "tracked.txt")
    if dirty_kind == "tracked":
        (project / "tracked.txt").write_text("modified source", encoding="utf-8")
    else:
        (project / "untracked.txt").write_text("uncommitted source", encoding="utf-8")

    cargo_log = tmp_path / "cargo.log"
    result = subprocess.run(
        ["bash", str(BUILDER), str(project)],
        check=False,
        env=_builder_environment(tmp_path, cargo_log),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "clean checkout" in result.stderr
    assert not cargo_log.exists()
    assert not (project / "target" / "rust-deploy" / "current").exists()
