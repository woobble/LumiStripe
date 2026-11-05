from __future__ import annotations

import getpass
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import pytest

UPDATE_SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "update.sh"
UPDATE_SUPPORT_SCRIPT = UPDATE_SCRIPT.with_name("update-support.sh")
TLS_SCRIPT = UPDATE_SCRIPT.with_name("tls.sh")
SOLOIST_SCRIPT = UPDATE_SCRIPT.with_name("soloist.sh")
EXTRACT_SOLOIST_SCRIPT = UPDATE_SCRIPT.with_name("extract_soloist.py")
INSTALL_SCRIPT = UPDATE_SCRIPT.with_name("install.sh")
DOCKERFILE = UPDATE_SCRIPT.parent / "docker" / "Dockerfile"
EXTRACTOR_SPEC = importlib.util.spec_from_file_location(
    "lumistripe_test_extract_soloist", EXTRACT_SOLOIST_SCRIPT
)
assert EXTRACTOR_SPEC is not None and EXTRACTOR_SPEC.loader is not None
EXTRACTOR = importlib.util.module_from_spec(EXTRACTOR_SPEC)
sys.modules[EXTRACTOR_SPEC.name] = EXTRACTOR
EXTRACTOR_SPEC.loader.exec_module(EXTRACTOR)


def _canonical_project_directory(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; canonical_project_directory "$2"',
            "bash",
            str(UPDATE_SUPPORT_SCRIPT),
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_project_directory_canonicalizer_resolves_checkout_symlinks(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    for relative in (".git", "deploy", "apps/lumistripe-web", "packages/lumistripe-core"):
        (checkout / relative).mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    link = tmp_path / "checkout-link"
    link.symlink_to(checkout, target_is_directory=True)

    result = _canonical_project_directory(link)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(checkout)


@pytest.mark.parametrize("path", [Path("/"), Path("/tmp")])
def test_project_directory_canonicalizer_rejects_root_and_non_checkout_paths(
    path: Path,
) -> None:
    result = _canonical_project_directory(path)

    assert result.returncode == 1
    assert "not a valid LumiStripe checkout" in result.stderr


def test_installer_and_updater_validate_project_dir_before_mutating_it() -> None:
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8")
    updater = UPDATE_SCRIPT.read_text(encoding="utf-8")

    assert installer.index('PROJECT_DIR="$(canonical_project_directory "$PROJECT_DIR")"') < (
        installer.index('chown -R "$SERVICE_USER:$SERVICE_GROUP" "$PROJECT_DIR"')
    )
    assert 'PROJECT_DIR="$(canonical_project_directory \\' in updater
    assert updater.index("canonical_project_directory") < updater.index(
        '[[ -e "$PROJECT_DIR/.git" ]]'
    )


def test_installer_downloads_allow_only_https_urls_and_redirects() -> None:
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert installer.count(
        "curl --disable --proto '=https' --proto-redir '=https'"
    ) == 2


class _FakeDownloadResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.headers = headers

    def geturl(self) -> str:
        return "https://soloist-builds.spotifycdn.com/archive.tar.gz"


def _write_soloist_archive(
    path: Path,
    *,
    name: str = "soloist",
    kind: bytes = tarfile.REGTYPE,
    payload: bytes = b"trusted soloist payload",
    include_extra: bool = False,
    include_documentation: bool = False,
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        if include_documentation:
            for documentation_name, documentation_payload in (
                ("CHANGELOG.md", b"Release notes"),
                ("THIRD_PARTY_LICENSES.txt", b"Third party licenses"),
            ):
                documentation = tarfile.TarInfo(documentation_name)
                documentation.size = len(documentation_payload)
                archive.addfile(documentation, io.BytesIO(documentation_payload))
        member = tarfile.TarInfo(name)
        member.type = kind
        if kind == tarfile.SYMTYPE:
            member.linkname = "../../etc/passwd"
        else:
            member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload) if kind == tarfile.REGTYPE else None)
        if include_extra:
            extra = tarfile.TarInfo("extra")
            extra.size = 1
            archive.addfile(extra, io.BytesIO(b"x"))


def _write_rust_soloist_helper(project_dir: Path) -> Path:
    helper = project_dir / "rust/target/rust-deploy/current/lumistripe-deploy-helper"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  soloist-download)\n"
        "    [ \"$2\" = --url ] && [ \"$4\" = --archive ] && "
        "[ \"$6\" = --destination ] || exit 2\n"
        "    cp -- \"$FAKE_ARCHIVE\" \"$5\"\n"
        "    exec \"$PYTHON_EXECUTABLE\" \"$PYTHON_EXTRACTOR_SCRIPT\" \"$5\" \"$7\"\n"
        "    ;;\n"
        "  soloist-check)\n"
        "    [ \"$2\" = --binary ] || exit 2\n"
        "    exec \"$PYTHON_EXECUTABLE\" \"$PYTHON_DIAGNOSTICS_SCRIPT\" --binary \"$3\"\n"
        "    ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    return helper


def test_soloist_extractor_accepts_only_one_regular_executable(tmp_path: Path) -> None:
    archive = tmp_path / "soloist.tar.gz"
    destination = tmp_path / "soloist"
    _write_soloist_archive(archive)

    subprocess.run(
        [sys.executable, str(EXTRACT_SOLOIST_SCRIPT), str(archive), str(destination)],
        check=True,
        capture_output=True,
    )

    assert destination.read_bytes() == b"trusted soloist payload"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


def test_soloist_extractor_checks_member_size_before_reading_next_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "oversized.tar.gz"
    archive_path.write_bytes(b"placeholder")
    destination = tmp_path / "soloist"
    monkeypatch.setattr(EXTRACTOR, "MAX_SOLOIST_BYTES", 8)

    oversized = tarfile.TarInfo("soloist")
    oversized.type = tarfile.REGTYPE
    oversized.size = 9

    class _Archive:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self):
            yield oversized
            raise AssertionError("extractor advanced before validating the header")

    monkeypatch.setattr(EXTRACTOR, "_open_tar_archive", lambda _path: _Archive())

    with pytest.raises(ValueError, match="executable has an invalid size"):
        EXTRACTOR.extract_soloist(archive_path, destination)

    assert not destination.exists()


def test_soloist_extractor_caps_decompressed_pax_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "oversized-pax.tar.gz"
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        entry = tarfile.TarInfo("unexpected/" + "x" * 2048)
        entry.size = 0
        archive.addfile(entry)

    destination = tmp_path / "soloist"
    monkeypatch.setattr(EXTRACTOR, "MAX_UNCOMPRESSED_ARCHIVE_BYTES", 1024)

    with pytest.raises(ValueError, match="decompressed size limit"):
        EXTRACTOR.extract_soloist(archive_path, destination)

    assert not destination.exists()


def test_soloist_download_streams_with_a_hard_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(EXTRACTOR, "MAX_ARCHIVE_BYTES", 8)
    response = _FakeDownloadResponse(b"123456789", {})
    monkeypatch.setattr(EXTRACTOR, "_open_archive_url", lambda _url: response)
    destination = tmp_path / "archive.tar.gz"

    with pytest.raises(ValueError, match="exceeds the size limit"):
        EXTRACTOR.download_archive("https://example.test/archive.tar.gz", destination)

    assert not destination.exists()


def test_soloist_download_rejects_bad_size_and_http_compression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(EXTRACTOR, "MAX_ARCHIVE_BYTES", 8)
    destination = tmp_path / "archive.tar.gz"
    too_large = _FakeDownloadResponse(b"", {"Content-Length": "9"})
    monkeypatch.setattr(EXTRACTOR, "_open_archive_url", lambda _url: too_large)
    with pytest.raises(ValueError, match="exceeds the size limit"):
        EXTRACTOR.download_archive("https://example.test/archive.tar.gz", destination)
    assert not destination.exists()

    compressed = _FakeDownloadResponse(b"archive", {"Content-Encoding": "gzip"})
    monkeypatch.setattr(EXTRACTOR, "_open_archive_url", lambda _url: compressed)
    with pytest.raises(ValueError, match="unexpected HTTP compression"):
        EXTRACTOR.download_archive("https://example.test/archive.tar.gz", destination)
    assert not destination.exists()


def test_soloist_download_mode_enforces_https_and_extracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_archive_url = EXTRACTOR._open_archive_url
    archive = tmp_path / "soloist.tar.gz"
    _write_soloist_archive(archive)
    archive_bytes = archive.read_bytes()
    monkeypatch.setattr(EXTRACTOR, "MAX_ARCHIVE_BYTES", len(archive_bytes) + 1)
    response = _FakeDownloadResponse(
        archive_bytes, {"Content-Length": str(len(archive_bytes))}
    )
    monkeypatch.setattr(EXTRACTOR, "_open_archive_url", lambda _url: response)
    destination_archive = tmp_path / "download.tar.gz"
    destination = tmp_path / "soloist"

    assert EXTRACTOR.main(
        [
            str(EXTRACT_SOLOIST_SCRIPT),
            "--download",
            "https://soloist-builds.spotifycdn.com/archive.tar.gz",
            str(destination_archive),
            str(destination),
        ]
    ) == 0
    assert destination.read_bytes() == b"trusted soloist payload"

    with pytest.raises(ValueError, match="must use HTTPS"):
        open_archive_url("http://example.test/archive.tar.gz")
    with pytest.raises(ValueError, match="official Spotify HTTPS host"):
        open_archive_url("https://example.test/archive.tar.gz")

    handler = EXTRACTOR._HTTPSRedirectHandler()
    with pytest.raises(urllib.error.HTTPError, match="non-HTTPS"):
        handler.redirect_request(
            urllib.request.Request(
                "https://soloist-builds.spotifycdn.com/archive.tar.gz"
            ),
            io.BytesIO(),
            302,
            "Found",
            {},
            "http://soloist-builds.spotifycdn.com/archive.tar.gz",
        )
    with pytest.raises(urllib.error.HTTPError, match="official Spotify Soloist"):
        handler.redirect_request(
            urllib.request.Request(
                "https://soloist-builds.spotifycdn.com/archive.tar.gz"
            ),
            io.BytesIO(),
            302,
            "Found",
            {},
            "https://attacker.example/archive.tar.gz",
        )
    assert handler.redirect_request(
        urllib.request.Request("https://soloist-builds.spotifycdn.com/archive.tar.gz"),
        io.BytesIO(),
        302,
        "Found",
        {},
        "https://soloist-builds.spotifycdn.com/release.tar.gz",
    ) is not None


def test_soloist_extractor_accepts_known_documentation_members(tmp_path: Path) -> None:
    archive = tmp_path / "soloist-with-docs.tar.gz"
    destination = tmp_path / "soloist"
    _write_soloist_archive(archive, include_documentation=True)

    subprocess.run(
        [sys.executable, str(EXTRACT_SOLOIST_SCRIPT), str(archive), str(destination)],
        check=True,
        capture_output=True,
    )

    assert destination.read_bytes() == b"trusted soloist payload"


@pytest.mark.parametrize(
    ("name", "kind", "include_extra"),
    [
        ("../../escape", tarfile.REGTYPE, False),
        ("soloist", tarfile.SYMTYPE, False),
        ("soloist", tarfile.REGTYPE, True),
    ],
)
def test_soloist_extractor_rejects_unexpected_members(
    tmp_path: Path, name: str, kind: bytes, include_extra: bool
) -> None:
    archive = tmp_path / "untrusted.tar.gz"
    destination = tmp_path / "soloist"
    _write_soloist_archive(
        archive, name=name, kind=kind, include_extra=include_extra
    )

    result = subprocess.run(
        [sys.executable, str(EXTRACT_SOLOIST_SCRIPT), str(archive), str(destination)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert not destination.exists()
    assert "Could not safely extract Soloist" in result.stderr


def test_docker_image_uses_the_rust_soloist_helper() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "lumistripe-deploy-helper soloist-download" in dockerfile
    assert "deploy/extract_soloist.py" not in dockerfile
    assert "python" not in dockerfile.lower()
    assert "uv" not in dockerfile.lower()
    assert "pip install" not in dockerfile.lower()
    assert "pip3 install" not in dockerfile.lower()
    assert "tar -xzf" not in dockerfile
    assert "USER 10001:10001" in dockerfile


def test_soloist_download_and_install_run_through_service_user_boundary(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "soloist.tar.gz"
    built_at = datetime.now(UTC)
    epoch = int(built_at.timestamp())
    payload = (
        "#!/bin/sh\n"
        "[ \"$1\" = --version ] || exit 2\n"
        f"printf '%s\\n' 'soloist 1.3.9.4 build {epoch} ({built_at:%Y%m%d}) "
        "(gtest) (linux/x86_64)'\n"
    ).encode()
    _write_soloist_archive(archive, payload=payload)
    service_home = tmp_path / "service home"
    project_dir = tmp_path / "project"
    _write_rust_soloist_helper(project_dir)
    calls = tmp_path / "service-user-calls"
    script = """
set -Eeuo pipefail
SCRIPT_DIR="${SOLOIST_SCRIPT%/*}"
as_user() {
  printf '%s\\n' "$*" >> "$CALL_LOG"
  "$@"
}
source "$SCRIPT_DIR/soloist.sh"
install_spotify_soloist "$SERVICE_HOME"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "FAKE_ARCHIVE": str(archive),
            "PROJECT_DIR": str(project_dir),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHON_DIAGNOSTICS_SCRIPT": str(
                UPDATE_SCRIPT.with_name("soloist_diagnostics.py")
            ),
            "PYTHON_EXTRACTOR_SCRIPT": str(EXTRACT_SOLOIST_SCRIPT),
            "SERVICE_HOME": str(service_home),
            "SOLOIST_SCRIPT": str(SOLOIST_SCRIPT),
            "CALL_LOG": str(calls),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    installed = service_home / ".local/bin/soloist"
    assert installed.read_bytes() == payload
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755
    logged_commands = calls.read_text(encoding="utf-8")
    assert "soloist-download" in logged_commands
    assert "--url https://soloist-builds.spotifycdn.com/soloist_release_x86_64.tar.gz" in logged_commands
    assert "/usr/bin/python3" not in logged_commands
    assert "install -m 755" in logged_commands
    assert "install -o root" not in logged_commands


def test_soloist_install_keeps_existing_binary_when_download_is_expired(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "expired-soloist.tar.gz"
    expired_payload = (
        b"#!/bin/sh\n"
        b"[ \"$1\" = --version ] || exit 2\n"
        b"printf '%s\\n' 'soloist 1.0 build 946684800 (20000101) (gtest) "
        b"(linux/x86_64)'\n"
    )
    _write_soloist_archive(archive, payload=expired_payload)
    service_home = tmp_path / "service home"
    project_dir = tmp_path / "project"
    _write_rust_soloist_helper(project_dir)
    installed = service_home / ".local/bin/soloist"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"existing build")
    script = """
set -Eeuo pipefail
SCRIPT_DIR="${SOLOIST_SCRIPT%/*}"
as_user() {
  "$@"
}
source "$SCRIPT_DIR/soloist.sh"
install_spotify_soloist "$SERVICE_HOME"
"""

    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "FAKE_ARCHIVE": str(archive),
            "PROJECT_DIR": str(project_dir),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHON_DIAGNOSTICS_SCRIPT": str(
                UPDATE_SCRIPT.with_name("soloist_diagnostics.py")
            ),
            "PYTHON_EXTRACTOR_SCRIPT": str(EXTRACT_SOLOIST_SCRIPT),
            "SERVICE_HOME": str(service_home),
            "SOLOIST_SCRIPT": str(SOLOIST_SCRIPT),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "build expired" in result.stderr
    assert installed.read_bytes() == b"existing build"


def test_update_restarts_spotify_after_installing_new_service_unit(
    tmp_path: Path,
) -> None:
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    start = source.index("if spotify_environment_ready; then\n  systemctl enable")
    end = source.index("systemctl restart lumistripe-web.service", start)
    activation = source[start:end]
    script = f"""
set -Eeuo pipefail
spotify_environment_ready() {{ return 0; }}
systemctl() {{ printf '%s\\n' "$*" >> "$CALL_LOG"; }}
{activation}
"""
    log = tmp_path / "calls"
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "CALL_LOG": str(log)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "enable lumistripe-spotify.service",
        "restart lumistripe-spotify.service",
    ]


def test_root_updater_uses_the_version_matched_soloist_helper() -> None:
    updater = UPDATE_SCRIPT.read_text(encoding="utf-8")
    support = UPDATE_SCRIPT.with_name("update-support.sh").read_text(encoding="utf-8")
    service = UPDATE_SCRIPT.with_name("lumistripe-spotify.service").read_text(
        encoding="utf-8"
    )
    web_service = UPDATE_SCRIPT.with_name("lumistripe-web.service").read_text(
        encoding="utf-8"
    )

    assert 'source "$SCRIPT_DIR/soloist.sh"' in updater
    assert "extract_soloist.py" not in support
    assert "soloist_diagnostics.py" not in support
    assert "spotify_device_name.py" not in support
    assert '"$PROJECT_DIR/deploy/lumistripe-spotify-device-name"' not in support
    assert "rm -f -- /usr/local/sbin/lumistripe-spotify-device-name" in support
    assert "lumistripe-spotify-service.sudoers" in support
    assert "rust/target/rust-deploy/current/lumistripe-deploy-helper" in service
    assert "ExecStartPre=/usr/bin/env -u LUMI_SPOTIFY_API_KEY" in service
    assert '--binary "@LUMI_HOME@/.local/bin/soloist"' in service
    assert "EnvironmentFile=-@LUMI_HOME@/.config/lumistripe/spotify-device-name.env" in service
    assert (
        "Environment=LUMI_DEPLOY_HELPER=@LUMI_PROJECT_DIR@/rust/target/rust-deploy/current/"
        "lumistripe-deploy-helper"
    ) in web_service
    assert "exec soloist --device-name" in service


def test_spotify_name_sudoers_only_allows_its_service_restart() -> None:
    sudoers = UPDATE_SCRIPT.with_name("lumistripe-spotify-service.sudoers").read_text(
        encoding="utf-8"
    )

    assert sudoers.strip() == (
        "@LUMI_SERVICE_USER@ ALL=(root) NOPASSWD: "
        "/usr/bin/systemctl restart --no-block lumistripe-spotify.service"
    )


@pytest.mark.parametrize(
    ("function", "failure_step", "expected_steps"),
    [
        ("sync_dependencies", 1, 1),
        ("sync_dependencies", 2, 2),
        ("sync_dependencies", 3, 3),
        ("build_frontend", 1, 1),
        ("build_frontend", 2, 2),
    ],
)
def test_update_steps_report_failure_even_in_conditional_context(
    tmp_path: Path, function: str, failure_step: int, expected_steps: int
) -> None:
    # The rollback invokes these functions on the left of ||, where Bash does
    # not apply errexit inside them. Exercise the actual definitions with mock
    # user commands, without running the root updater or touching the host.
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    definitions = source[source.index("sync_dependencies() ("):source.index("install_wireplumber_config() {")]
    (tmp_path / "apps/lumistripe-web/frontend").mkdir(parents=True)
    log = tmp_path / "calls"
    script = f"""
set -Eeuo pipefail
step=0
web_backend_requires_python() {{ [[ "$1" == python ]]; }}
as_user() {{
  step=$((step + 1))
  printf '%s\\n' "$step" >> "$CALL_LOG"
  if [[ "$step" -eq "$FAILURE_STEP" ]]; then return 1; fi
}}
as_user_with_env() {{ as_user "$@"; }}
{definitions}
if {function}; then exit 0; else exit 42; fi
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PROJECT_DIR": str(tmp_path),
            "WEB_BACKEND": "python",
            "UV_BIN": "uv",
            "BUN_BIN": "bun",
            "PYTHON_VERSION": "3.12",
            "CALL_LOG": str(log),
            "FAILURE_STEP": str(failure_step),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 42, result.stderr
    assert len(log.read_text(encoding="utf-8").splitlines()) == expected_steps


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
sync_dependencies() {{ rollback_step sync_dependencies; }}
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
    assert calls[:2] == [
        "git switch --detach previous-rust-commit",
        "git submodule update --init --recursive",
    ]
    assert calls[2:6] == [
        "sync_dependencies",
        "build_rust_backend",
        "build_frontend",
        "refresh_deployment_files",
    ]
    assert "systemctl restart lumistripe-web.service" in calls
    assert ("systemctl restart lumistripe-spotify.service" in calls) is spotify_configured
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
    sync = source[source.index("sync_dependencies() ("):source.index("build_frontend() (")]
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
    release = project_dir / "rust/target/rust-deploy/releases/previous-rust-commit"
    release.mkdir(parents=True)
    cli_binary = release / "lumistripe"
    cli_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli_binary.chmod(0o755)
    current_release = project_dir / "rust/target/rust-deploy/current"
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
configured_web_backend() {{ printf '%s\\n' "$WEB_BACKEND"; }}
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
{sync}
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
    assert calls[:2] == [
        "git switch --detach previous-rust-commit",
        "git submodule update --init --recursive",
    ]
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
    assert (tmp_path / "service-home/.local/bin/lumistripe-cli").resolve() == cli_binary
    health_probes = [line for line in calls if line.startswith("curl ")]
    assert len(health_probes) == probes
    assert all("--max-time 0.25" in line for line in health_probes)
    if return_code == 42:
        assert "Rollback failed; manual recovery is required." in result.stderr


def test_update_uses_installed_deployment_templates(tmp_path: Path) -> None:
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    definitions = source[
        source.index("install_wireplumber_config() {"):source.index("refresh_deployment_files() {")
    ]
    trusted = tmp_path / "installed"
    checkout = tmp_path / "checkout"
    trusted.mkdir()
    (checkout / "deploy").mkdir(parents=True)
    for name in (
        "lumistripe-web.service",
        "lumistripe-spotify.service",
        "90-lumistripe-bluetooth.conf",
        "90-lumistripe-spotify.conf",
    ):
        (trusted / name).write_text("trusted @LUMI_SERVICE_USER@\n", encoding="utf-8")
        (checkout / "deploy" / name).write_text("untrusted\n", encoding="utf-8")

    # Execute the actual functions with host writes redirected to a temporary
    # directory; install arguments and the as_user boundary remain visible.
    script = f"""
set -Eeuo pipefail
as_user() {{ "$@"; }}
install() {{
  if [[ "$1" == -d ]]; then command install "$@"; else cp -- "${{@: -2:1}}" "${{@: -1}}"; fi
}}
{definitions}
install_wireplumber_config
render_service_unit
render_spotify_service_unit
"""
    env = {
        **os.environ,
        "SCRIPT_DIR": str(trusted),
        "PROJECT_DIR": str(checkout),
        "PROJECT_HOME": str(tmp_path),
        "PROJECT_USER": getpass.getuser(),
        "PROJECT_GROUP": "group",
        "PYTHON_VERSION": "3.12",
        "WIREPLUMBER_DIR": str(tmp_path / "wireplumber"),
        "WIREPLUMBER_FILE": str(tmp_path / "wireplumber/bluetooth.conf"),
        "PIPEWIRE_DIR": str(tmp_path / "pipewire"),
        "SPOTIFY_PIPEWIRE_FILE": str(tmp_path / "pipewire/spotify.conf"),
        "SERVICE_FILE": str(tmp_path / "web.service"),
        "SPOTIFY_SERVICE_FILE": str(tmp_path / "spotify.service"),
    }
    subprocess.run(["bash", "-c", script], env=env, check=True, capture_output=True)
    for path in (
        tmp_path / "web.service",
        tmp_path / "spotify.service",
        tmp_path / "wireplumber/bluetooth.conf",
        tmp_path / "pipewire/spotify.conf",
    ):
        assert path.read_text(encoding="utf-8").startswith("trusted ")


def test_update_records_checkout_status_as_service_user(tmp_path: Path) -> None:
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    definition = source[
        source.index("record_update_state() {"):source.index("handle_update_error() {")
    ]
    script = f"""
set -Eeuo pipefail
as_user() {{ printf '%s\\n' "$@" > "$CALL_LOG"; }}
{definition}
record_update_state install-start
"""
    log = tmp_path / "calls"
    subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "SCRIPT_DIR": str(tmp_path / "installed"),
            "PROJECT_DIR": str(tmp_path / "checkout"),
            "UPDATE_STATE_FILE": str(tmp_path / "status.json"),
            "CALL_LOG": str(log),
        },
        check=True,
    )
    assert log.read_text(encoding="utf-8").splitlines() == [
        str(tmp_path / "checkout/rust/target/rust-deploy/current/lumistripe-deploy-helper"),
        "update-state",
        "install-start",
        "--project-dir",
        str(tmp_path / "checkout"),
        "--state-file",
        str(tmp_path / "status.json"),
    ]


def test_periodic_update_check_uses_rust_deploy_helper() -> None:
    service = UPDATE_SCRIPT.with_name("lumistripe-update-check.service").read_text(
        encoding="utf-8"
    )
    support = UPDATE_SCRIPT.with_name("update-support.sh").read_text(encoding="utf-8")

    assert "rust/target/rust-deploy/current/lumistripe-deploy-helper" in service
    assert "update-state check" in service
    assert "update_state.py" not in support


def test_update_rejects_missing_tls_before_reconfiguring_services(tmp_path: Path) -> None:
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    definition = source[
        source.index("refresh_deployment_files() {"):source.index("rollback() {")
    ]
    result = subprocess.run(
        ["bash", "-c", f"set -Eeuo pipefail\n{definition}\nrefresh_deployment_files"],
        env={
            **os.environ,
            "TLS_CERT": str(tmp_path / "missing.crt"),
            "TLS_KEY": str(tmp_path / "missing.key"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "TLS certificate or key missing" in result.stderr


def test_refresh_installs_network_helper_before_updater_snapshot(tmp_path: Path) -> None:
    source = UPDATE_SCRIPT.read_text(encoding="utf-8")
    definition = source[
        source.index("refresh_deployment_files() {"):source.index("rollback() {")
    ]
    cert = tmp_path / "cert"
    key = tmp_path / "key"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    log = tmp_path / "calls"
    script = f"""
set -Eeuo pipefail
install_wireplumber_config() {{ :; }}
render_service_unit() {{ :; }}
render_spotify_service_unit() {{ :; }}
install_privileged_network_helper() {{ printf 'network-helper\\n' >> "$CALL_LOG"; }}
install_update_support() {{ printf 'update-support\\n' >> "$CALL_LOG"; }}
install() {{ printf '%s\\n' "$*" >> "$CALL_LOG"; }}
systemctl() {{ :; }}
ln() {{ :; }}
nginx() {{ :; }}
{definition}
refresh_deployment_files
"""
    subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "TLS_CERT": str(cert),
            "TLS_KEY": str(key),
            "SCRIPT_DIR": str(tmp_path / "installed"),
            "NGINX_AVAILABLE": str(tmp_path / "nginx.conf"),
            "SERVICE_RUNTIME_DIR": str(tmp_path / "no-user-bus"),
            "CALL_LOG": str(log),
        },
        check=True,
    )
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls.index("network-helper") < calls.index("update-support")
    assert any(
        str(tmp_path / "installed/nginx/led-controller.conf") in call for call in calls
    )


@pytest.mark.parametrize(("size", "success"), [(0, False), (128, True), (1_048_577, False)])
def test_tls_import_reads_as_service_user_and_limits_size(
    tmp_path: Path, size: int, success: bool
) -> None:
    source = tmp_path / "generated"
    destination = tmp_path / "imported"
    log = tmp_path / "runuser"
    source.write_bytes(b"x" * size)
    script = """
source "$TLS_SCRIPT"
timeout() { shift; "$@"; }
runuser() {
  printf '%s\\n' "$1" "$2" "$3" "$4" > "$CALL_LOG"
  shift 3
  "$@"
}
copy_lumistripe_tls_file service-user "$SOURCE" "$DESTINATION"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "TLS_SCRIPT": str(TLS_SCRIPT),
            "SOURCE": str(source),
            "DESTINATION": str(destination),
            "CALL_LOG": str(log),
        },
        check=False,
    )
    assert (result.returncode == 0) is success
    assert log.read_text(encoding="utf-8").splitlines() == [
        "-u", "service-user", "--", "/usr/bin/head"
    ]
    if success:
        assert destination.read_bytes() == source.read_bytes()
