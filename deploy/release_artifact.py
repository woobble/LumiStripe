#!/usr/bin/env python3
"""Build, verify, and stage LumiStripe's immutable release bundles."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

REPOSITORY = "woobble/LumiStripe"
API_ROOT = "https://api.github.com/repos/woobble/LumiStripe"
RELEASE_TAG_PREFIX = "build-"
FORMAT_VERSION = 1
ARCHIVE_LIMIT = 512 * 1024 * 1024
SIDECAR_LIMIT = 2 * 1024 * 1024
EXPANDED_LIMIT = 1024 * 1024 * 1024
ENTRY_LIMIT = 20_000
FILE_LIMIT = 128 * 1024 * 1024
RELEASE_BINARIES = (
    "lumistripe-web",
    "lumistripe-engine",
    "lumistripe",
    "lumistripe-deploy-helper",
)
RELEASE_FEATURES = ("bluez", "pipewire")
RELEASE_NAME = "lumistripe-linux-aarch64-{commit}.tar.gz"
ATTESTATION_SUFFIX = ".sigstore.json"
MANIFEST_SUFFIX = ".manifest.json"
CHECKSUM_SUFFIX = ".sha256"
SIGNER_WORKFLOW = "woobble/LumiStripe/.github/workflows/ci.yml"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"


class ReleaseError(Exception):
    """Base class for a release error suitable for a concise CLI message."""


class MissingArtifact(ReleaseError):
    """The requested immutable release has not been published."""


class UnsupportedTarget(ReleaseError):
    """No prebuilt bundle is defined for the current target."""


class VerificationError(ReleaseError):
    """Downloaded or cached release data failed a security check."""


class HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        if urllib.parse.urlparse(new_url).scheme != "https":
            raise VerificationError("GitHub attempted a non-HTTPS redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


HTTPS_OPENER = urllib.request.build_opener(HTTPSOnlyRedirectHandler())


def valid_commit(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{40}", value) is not None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_file_inventory(root: Path) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise VerificationError(f"release directory is not a real directory: {root}")

    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise VerificationError(f"release contains a symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            if relative not in {"static", "static/assets"} and not relative.startswith(
                "static/"
            ):
                raise VerificationError(f"unexpected release directory: {relative}")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise VerificationError(f"release contains a non-regular file: {relative}")
        if metadata.st_size > FILE_LIMIT:
            raise VerificationError(f"release file is too large: {relative}")
        if relative not in {*RELEASE_BINARIES, "source-commit", "manifest.json"} and not (
            relative.startswith("static/") and relative != "static/"
        ):
            raise VerificationError(f"unexpected release file: {relative}")
        files[relative] = path
    return files


def _glibc_version() -> str:
    version = platform.libc_ver()[1]
    if not version:
        return "unknown"
    return version


def migrate_legacy_static(
    release_dir: Path, static_source: Path, commit: str, project_root: Path | None = None
) -> None:
    if not valid_commit(commit):
        raise VerificationError("source commit must be a full lowercase Git SHA")
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise VerificationError("legacy release directory is not a real directory")
    marker = release_dir / "source-commit"
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_text(encoding="ascii").strip() != commit
    ):
        raise VerificationError("legacy release marker does not match the requested commit")
    destination = release_dir / "static"
    if destination.exists() or destination.is_symlink():
        raise VerificationError("legacy release already contains an incomplete static directory")
    if static_source.is_symlink() or not static_source.is_dir():
        raise VerificationError("legacy frontend static directory is missing")

    source_entries = sorted(static_source.rglob("*"))
    total_size = 0
    for source in source_entries:
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise VerificationError("legacy frontend contains a symlink")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise VerificationError("legacy frontend contains a non-regular file")
        total_size += metadata.st_size
        if metadata.st_size > FILE_LIMIT or total_size > EXPANDED_LIMIT:
            raise VerificationError("legacy frontend exceeds the migration size limit")
    if not (static_source / "index.html").is_file():
        raise VerificationError("legacy frontend index.html is missing")
    if not any(
        path.is_file() and path.relative_to(static_source).as_posix().startswith("assets/")
        for path in source_entries
    ):
        raise VerificationError("legacy frontend assets are missing")
    manifest_path = release_dir / "manifest.json"
    if manifest_path.is_symlink() or (manifest_path.exists() and not manifest_path.is_file()):
        raise VerificationError("legacy release manifest is not a regular file")

    temporary = release_dir / f".static-migration.{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        raise VerificationError("legacy release has a leftover static migration directory")
    try:
        shutil.copytree(static_source, temporary, symlinks=False)
        for path in temporary.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        temporary.rename(destination)
        manifest_path.unlink(missing_ok=True)
        make_manifest(release_dir, commit, origin="source", project_root=project_root)
        validate_release_dir(release_dir, commit, expected_origin="source")
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        manifest_path.unlink(missing_ok=True)
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _bun_version(project_root: Path | None) -> str:
    if project_root is None:
        return "unknown"
    version_file = project_root / ".bun-version"
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return version or "unknown"


def make_manifest(
    release_dir: Path,
    commit: str,
    *,
    origin: str,
    project_root: Path | None = None,
    glibc_minimum: str | None = None,
) -> dict[str, Any]:
    if not valid_commit(commit):
        raise VerificationError("source commit must be a full lowercase Git SHA")

    release_dir.mkdir(parents=True, exist_ok=True)
    marker = release_dir / "source-commit"
    marker.write_text(f"{commit}\n", encoding="ascii")
    files = _release_file_inventory(release_dir)
    missing = [name for name in RELEASE_BINARIES if name not in files]
    if missing:
        raise VerificationError(f"release is missing binaries: {', '.join(missing)}")
    for binary in RELEASE_BINARIES:
        path = files[binary]
        if path.stat().st_mode & 0o111 == 0:
            raise VerificationError(f"release binary is not executable: {binary}")

    index = files.get("static/index.html")
    if index is None or index.stat().st_size == 0:
        raise VerificationError("release is missing a non-empty static/index.html")
    if not any(name.startswith("static/assets/") for name in files):
        raise VerificationError("release frontend is missing static/assets files")

    checksums = {
        name: {"sha256": sha256_file(path), "size": path.stat().st_size}
        for name, path in sorted(files.items())
        if name != "manifest.json"
    }
    manifest: dict[str, Any] = {
        "release_format": FORMAT_VERSION,
        "source_commit": commit,
        "origin": origin,
        "target": {
            "triple": "aarch64-unknown-linux-gnu" if origin == "github" else "host",
            "architecture": "aarch64" if origin == "github" else platform.machine(),
            "abi": "GNU/Linux",
        },
        "runtime_abi": {
            "distribution": "debian-trixie" if origin == "github" else "local-host",
            "glibc_minimum": glibc_minimum or _glibc_version(),
            "libraries": [
                "libasound.so.2",
                "libatomic.so.1",
                "libdbus-1.so.3",
                "libpipewire-0.3.so.0",
                "libpulse.so.0",
                "libspa-0.2.so.0",
            ],
        },
        "toolchain": {
            "rust": "1.88.0",
            "bun": _bun_version(project_root),
            "builder": "debian:trixie-slim" if origin == "github" else "local",
        },
        "binaries": list(RELEASE_BINARIES),
        "required_runtime_features": list(RELEASE_FEATURES),
        "checksums": checksums,
    }
    manifest_path = release_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o644)
    return manifest


def validate_release_dir(
    release_dir: Path, commit: str, *, expected_origin: str | None = None
) -> dict[str, Any]:
    if not valid_commit(commit):
        raise VerificationError("source commit must be a full lowercase Git SHA")
    files = _release_file_inventory(release_dir)
    required = {*RELEASE_BINARIES, "source-commit", "manifest.json", "static/index.html"}
    if not required.issubset(files):
        missing = ", ".join(sorted(required - files.keys()))
        raise VerificationError(f"release is incomplete; missing: {missing}")
    marker = files["source-commit"].read_text(encoding="ascii").strip()
    if marker != commit:
        raise VerificationError("source-commit does not match the requested commit")
    try:
        manifest = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError("release manifest is missing or invalid JSON") from error

    if not isinstance(manifest, dict):
        raise VerificationError("release manifest must be a JSON object")
    if manifest.get("release_format") != FORMAT_VERSION:
        raise VerificationError("unsupported release manifest format")
    if manifest.get("source_commit") != commit:
        raise VerificationError("manifest source commit does not match the requested commit")
    origin = manifest.get("origin")
    if origin not in {"github", "source"}:
        raise VerificationError("manifest origin is invalid")
    if expected_origin and manifest.get("origin") != expected_origin:
        raise VerificationError(f"release origin is not {expected_origin}")
    if manifest.get("binaries") != list(RELEASE_BINARIES):
        raise VerificationError("manifest binary list is incomplete or unexpected")
    if manifest.get("required_runtime_features") != list(RELEASE_FEATURES):
        raise VerificationError("manifest runtime features are incomplete or unexpected")
    target = manifest.get("target")
    if not isinstance(target, dict) or target.get("triple") not in {
        "aarch64-unknown-linux-gnu",
        "host",
    } or not isinstance(target.get("architecture"), str) or not target["architecture"]:
        raise VerificationError("manifest target is invalid")
    if origin == "github" and (
        target.get("triple") != "aarch64-unknown-linux-gnu" or target.get("architecture") != "aarch64"
    ):
        raise VerificationError("GitHub release target must be aarch64-unknown-linux-gnu")
    if origin == "source" and target.get("triple") != "host":
        raise VerificationError("source release target must describe the local host")
    if target.get("abi") != "GNU/Linux":
        raise VerificationError("manifest target ABI is invalid")
    abi = manifest.get("runtime_abi")
    if not isinstance(abi, dict) or not isinstance(abi.get("glibc_minimum"), str):
        raise VerificationError("manifest runtime ABI is invalid")
    expected_distribution = "debian-trixie" if origin == "github" else "local-host"
    if abi.get("distribution") != expected_distribution:
        raise VerificationError("manifest runtime distribution does not match its origin")
    if origin == "github" and not re.fullmatch(
        r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", abi["glibc_minimum"]
    ):
        raise VerificationError("manifest glibc minimum version is invalid")
    libraries = abi.get("libraries")
    if not isinstance(libraries, list) or not libraries or not all(
        isinstance(library, str) and library for library in libraries
    ):
        raise VerificationError("manifest runtime library list is invalid")
    toolchain = manifest.get("toolchain")
    if (
        not isinstance(toolchain, dict)
        or toolchain.get("rust") != "1.88.0"
        or not isinstance(toolchain.get("bun"), str)
        or not toolchain["bun"]
        or not isinstance(toolchain.get("builder"), str)
    ):
        raise VerificationError("manifest build toolchain is invalid")
    if origin == "github" and (
        not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", toolchain["bun"])
        or toolchain.get("builder") != "debian:trixie-slim"
    ):
        raise VerificationError("GitHub release toolchain does not match the CI build")
    checksum_entries = manifest.get("checksums")
    if not isinstance(checksum_entries, dict):
        raise VerificationError("manifest checksums are missing")
    expected_files = set(files) - {"manifest.json"}
    if set(checksum_entries) != expected_files:
        raise VerificationError("manifest checksums do not cover exactly the release files")

    for name, path in files.items():
        if name == "manifest.json":
            continue
        if name in RELEASE_BINARIES and path.stat().st_mode & 0o111 == 0:
            raise VerificationError(f"release binary is not executable: {name}")
        if name not in RELEASE_BINARIES and path.stat().st_mode & 0o111:
            raise VerificationError(f"non-executable release file has executable permissions: {name}")
        item = checksum_entries.get(name)
        if not isinstance(item, dict):
            raise VerificationError(f"manifest checksum entry is invalid: {name}")
        if item.get("sha256") != sha256_file(path) or item.get("size") != path.stat().st_size:
            raise VerificationError(f"release checksum does not match: {name}")
    if not any(name.startswith("static/assets/") for name in files):
        raise VerificationError("release frontend is missing static/assets files")
    return manifest


def _archive_entries(release_dir: Path) -> list[tuple[Path, str]]:
    entries = [(release_dir, "release")]
    entries.extend(
        (path, f"release/{path.relative_to(release_dir).as_posix()}")
        for path in sorted(release_dir.rglob("*"))
    )
    return entries


def package_release(
    release_dir: Path,
    output_path: Path,
    commit: str,
    *,
    project_root: Path | None = None,
    glibc_minimum: str | None = None,
    build_info_path: Path | None = None,
) -> Path:
    if build_info_path is not None:
        try:
            build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise VerificationError("ARM64 runtime build information is missing or invalid") from error
        if not isinstance(build_info, dict) or build_info.get("architecture") != "aarch64":
            raise VerificationError("ARM64 runtime validation did not report aarch64")
        if build_info.get("target") != "aarch64-unknown-linux-gnu":
            raise VerificationError("ARM64 runtime validation did not report the GNU target")
        info_glibc = build_info.get("glibc_minimum")
        if not isinstance(info_glibc, str) or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", info_glibc):
            raise VerificationError("ARM64 runtime validation did not report a valid glibc version")
        if glibc_minimum is not None and glibc_minimum != info_glibc:
            raise VerificationError("provided glibc version does not match the validated runtime")
        glibc_minimum = info_glibc
    manifest = make_manifest(
        release_dir,
        commit,
        origin="github",
        project_root=project_root,
        glibc_minimum=glibc_minimum,
    )
    validate_release_dir(release_dir, commit, expected_origin="github")
    if manifest["target"]["triple"] != "aarch64-unknown-linux-gnu":
        raise VerificationError("CI releases must target aarch64-unknown-linux-gnu")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        raw = temporary.open("wb")
        try:
            with (
                gzip.GzipFile(
                fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=9
                ) as compressed,
                tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive,
            ):
                for path, name in _archive_entries(release_dir):
                    metadata = path.lstat()
                    if stat.S_ISDIR(metadata.st_mode):
                        info = tarfile.TarInfo(f"{name}/")
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        info.size = 0
                        source: BinaryIO | None = None
                    elif stat.S_ISREG(metadata.st_mode):
                        info = tarfile.TarInfo(name)
                        info.type = tarfile.REGTYPE
                        info.mode = 0o755 if name.split("/", 1)[1] in RELEASE_BINARIES else 0o644
                        info.size = metadata.st_size
                        source = path.open("rb")
                    else:
                        raise VerificationError(f"release contains unsafe file: {name}")
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    try:
                        archive.addfile(info, source)
                    finally:
                        if source is not None:
                            source.close()
            raw.flush()
            os.fsync(raw.fileno())
        finally:
            raw.close()
        if temporary.stat().st_size > ARCHIVE_LIMIT:
            raise VerificationError("release archive exceeds the 512 MiB package limit")
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return output_path


def _safe_archive_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name or any(ord(ch) < 0x20 for ch in name):
        raise VerificationError("release archive contains an invalid path")
    normalized = name.removesuffix("/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise VerificationError(f"release archive contains an unsafe path: {name!r}")
    if path.parts[0] != "release" or path.as_posix() != normalized:
        raise VerificationError(f"release archive path is outside release/: {name!r}")
    if len(name) > 1024:
        raise VerificationError("release archive path is too long")
    return normalized


def _check_archive_member(info: tarfile.TarInfo, seen: set[str]) -> str:
    name = _safe_archive_name(info.name)
    if name in seen:
        raise VerificationError(f"release archive contains a duplicate path: {name}")
    seen.add(name)
    if not (info.isdir() or info.isfile()):
        raise VerificationError(f"release archive contains an unsupported entry: {name}")
    if info.pax_headers or getattr(info, "sparse", None):
        raise VerificationError(f"release archive contains unsupported metadata: {name}")
    if info.size < 0 or info.size > FILE_LIMIT:
        raise VerificationError(f"release archive entry exceeds the file limit: {name}")
    if info.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        raise VerificationError(f"release archive entry has unsafe mode bits: {name}")
    if name == "release" and not info.isdir():
        raise VerificationError("release archive root must be a directory")
    if name != "release":
        relative = name.removeprefix("release/")
        if info.isdir():
            if relative != "static" and not relative.startswith("static/"):
                raise VerificationError(f"release archive contains an unexpected directory: {name}")
        elif relative not in {*RELEASE_BINARIES, "source-commit", "manifest.json"} and not (
            relative.startswith("static/") and relative != "static/"
        ):
            raise VerificationError(f"release archive contains an unexpected path: {name}")
        if info.isfile() and relative in RELEASE_BINARIES and info.mode & 0o111 == 0:
            raise VerificationError(f"release binary is not executable in archive: {relative}")
        if info.isfile() and relative not in RELEASE_BINARIES and info.mode & 0o111:
            raise VerificationError(f"non-executable release file is executable in archive: {relative}")
    return name


def extract_archive(archive_path: Path, destination: Path, commit: str) -> Path:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise VerificationError("release archive is not a regular file")
    if archive_path.stat().st_size > ARCHIVE_LIMIT:
        raise VerificationError("release archive exceeds the 512 MiB download limit")
    if destination.exists():
        raise VerificationError(f"release extraction destination already exists: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    expected_files: set[str] = set()
    seen: set[str] = set()
    expanded_size = 0
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > ENTRY_LIMIT:
                raise VerificationError("release archive contains too many entries")
            for info in members:
                name = _check_archive_member(info, seen)
                if info.isfile():
                    expanded_size += info.size
                    if expanded_size > EXPANDED_LIMIT:
                        raise VerificationError("release archive exceeds the extraction size limit")
                    expected_files.add(name.removeprefix("release/"))
            required_files = {*RELEASE_BINARIES, "source-commit", "manifest.json", "static/index.html"}
            if not required_files.issubset(expected_files):
                raise VerificationError("release archive is missing required binaries or frontend files")

            release_root = destination / "release"
            release_root.mkdir(mode=0o700)
            for info in members:
                name = _safe_archive_name(info.name)
                if name == "release":
                    continue
                relative = name.removeprefix("release/")
                target = release_root.joinpath(*PurePosixPath(relative).parts)
                if info.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o755)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                source = archive.extractfile(info)
                if source is None:
                    raise VerificationError(f"could not read release archive entry: {name}")
                mode = 0o755 if relative in RELEASE_BINARIES else 0o644
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    source.close()
                target.chmod(mode)
        validate_release_dir(release_root, commit, expected_origin="github")
        return release_root
    except (tarfile.TarError, OSError) as error:
        shutil.rmtree(destination, ignore_errors=True)
        raise VerificationError(f"could not safely extract release archive: {error}") from error
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def parse_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        try:
            parsed = shlex.split(value, posix=True)
        except ValueError:
            continue
        if parsed:
            result[key] = parsed[0]
    return result


def binary_target_supported(
    *, machine: str | None = None, os_release: dict[str, str] | None = None
) -> bool:
    machine = machine or platform.machine()
    os_release = os_release if os_release is not None else parse_os_release()
    codename = os_release.get("VERSION_CODENAME", "")
    distro = os_release.get("ID", "").lower()
    return machine == "aarch64" and (
        distro == "ubuntu" or (codename == "trixie" and distro in {"debian", "raspbian"})
    )


def _safe_url(url: str, *, allowed_hosts: set[str]) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise VerificationError("GitHub returned a non-HTTPS or unexpected download URL")
    return url


def _request_bytes(
    url: str,
    *,
    limit: int,
    expected_hosts: set[str],
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "LumiStripe-release-installer/1",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **(headers or {}),
        },
    )
    try:
        response = HTTPS_OPENER.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        if error.code in {404, 410}:
            return error.code, b""
        if error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0":
            raise ReleaseError("GitHub API rate limit reached; retry the update check later") from error
        raise ReleaseError(f"GitHub request failed with HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ReleaseError(f"GitHub request failed: {error}") from error

    with response:
        final_url = response.geturl()
        _safe_url(final_url, allowed_hosts=expected_hosts)
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise VerificationError("GitHub response exceeds the configured size limit")
        chunks: list[bytes] = []
        received = 0
        while True:
            chunk = response.read(min(1024 * 1024, limit + 1 - received))
            if not chunk:
                break
            received += len(chunk)
            if received > limit:
                raise VerificationError("GitHub response exceeds the configured size limit")
            chunks.append(chunk)
        return response.status, b"".join(chunks)


def _request_json(url: str, *, limit: int = 2 * 1024 * 1024) -> tuple[int, dict[str, Any] | None]:
    status, body = _request_bytes(
        url,
        limit=limit,
        expected_hosts={"api.github.com"},
    )
    if status == 404:
        return status, None
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError("GitHub returned invalid JSON metadata") from error
    if not isinstance(value, dict):
        raise VerificationError("GitHub returned an unexpected metadata document")
    return status, value


def _release_assets(commit: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    tag = f"{RELEASE_TAG_PREFIX}{commit}"
    status, release = _request_json(f"{API_ROOT}/releases/tags/{tag}")
    if status == 404 or release is None:
        raise MissingArtifact(f"no prebuilt release is published for commit {commit}")
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("immutable") is not True
    ):
        raise VerificationError(
            "GitHub release metadata does not identify a published immutable release for the requested commit"
        )
    assets_value = release.get("assets")
    if not isinstance(assets_value, list):
        raise VerificationError("GitHub release metadata has no asset list")
    assets: dict[str, dict[str, Any]] = {}
    for asset in assets_value:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise VerificationError("GitHub release contains malformed asset metadata")
        if asset["name"] in assets:
            raise VerificationError("GitHub release contains duplicate asset names")
        assets[asset["name"]] = asset
    return release, assets


def _asset_url(asset: dict[str, Any], expected_name: str) -> str:
    if asset.get("name") != expected_name:
        raise VerificationError("GitHub release asset name does not match the requested file")
    url = asset.get("browser_download_url")
    if not isinstance(url, str):
        raise VerificationError(f"GitHub release asset has no download URL: {expected_name}")
    return _safe_url(
        url,
        allowed_hosts={"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"},
    )


def _download_asset(
    asset: dict[str, Any],
    name: str,
    destination: Path,
    *,
    max_size: int = ARCHIVE_LIMIT,
) -> str:
    digest_value = asset.get("digest")
    if not isinstance(digest_value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest_value):
        raise VerificationError(f"GitHub release does not publish a SHA-256 digest for {name}")
    expected_size = asset.get("size")
    if not isinstance(expected_size, int) or expected_size < 0 or expected_size > max_size:
        raise VerificationError(f"GitHub release asset has an invalid size: {name}")

    url = _asset_url(asset, name)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "LumiStripe-release-installer/1", "Accept": "application/octet-stream"},
    )
    try:
        response = HTTPS_OPENER.open(request, timeout=45)
    except (urllib.error.URLError, TimeoutError, urllib.error.HTTPError) as error:
        raise ReleaseError(f"could not download GitHub release asset {name}: {error}") from error
    digest = hashlib.sha256()
    size = 0
    with response:
        _safe_url(
            response.geturl(),
            allowed_hosts={"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"},
        )
        length = response.headers.get("Content-Length")
        if length and int(length) != expected_size:
            raise VerificationError(f"GitHub asset size does not match its publication metadata: {name}")
        with destination.open("xb") as output:
            while True:
                chunk = response.read(min(1024 * 1024, max_size + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size:
                    raise VerificationError(f"GitHub asset exceeds the download limit: {name}")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    if size != expected_size:
        raise VerificationError(f"downloaded asset size does not match GitHub metadata: {name}")
    actual = digest.hexdigest()
    if actual != digest_value.removeprefix("sha256:"):
        raise VerificationError(f"downloaded asset digest does not match GitHub metadata: {name}")
    return actual


def _parse_checksum_file(path: Path, archive_name: str) -> str:
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationError("release checksum asset is missing or invalid") from error
    match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^\r\n]+)\n?", text)
    if not match or match.group(2) != archive_name:
        raise VerificationError("release checksum asset does not name the requested archive")
    return match.group(1)


def _verify_attestation(archive: Path, bundle: Path, commit: str, gh_path: str) -> None:
    try:
        version_result = subprocess.run(
            [gh_path, "--version"], check=True, capture_output=True, text=True, timeout=10,
            env={"PATH": "/usr/bin:/bin", "HOME": "/root"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseError("GitHub CLI is required to verify release provenance") from error
    version_match = re.search(r"gh version (\d+)\.(\d+)", version_result.stdout)
    if not version_match or tuple(map(int, version_match.groups())) < (2, 67):
        raise ReleaseError("GitHub CLI 2.67 or newer is required for release attestation verification")

    command = [
        gh_path,
        "attestation",
        "verify",
        str(archive),
        "--repo",
        REPOSITORY,
        "--signer-workflow",
        SIGNER_WORKFLOW,
        "--source-digest",
        commit,
        "--deny-self-hosted-runners",
        "--bundle",
        str(bundle),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
            env={"PATH": "/usr/bin:/bin", "HOME": "/root"},
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "attestation verification failed").strip()
        raise VerificationError(f"GitHub provenance verification failed: {detail[:1000]}") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseError(f"GitHub provenance verification could not complete: {error}") from error


def _verify_cache_dir(cache_dir: Path, commit: str) -> dict[str, Any]:
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        raise VerificationError("previously verified release cache is not a real directory")
    marker = cache_dir / "verified.json"
    archive = cache_dir / "bundle.tar.gz"
    bundle = cache_dir / "bundle.sigstore.json"
    checksum = cache_dir / "bundle.sha256"
    for path in (marker, archive, bundle, checksum):
        if path.is_symlink() or not path.is_file():
            raise VerificationError(f"previously verified release cache is incomplete: {path.name}")
        metadata = path.stat()
        if os.geteuid() == 0 and metadata.st_uid != 0:
            raise VerificationError("previously verified release cache is not root-owned")
        if metadata.st_mode & 0o022:
            raise VerificationError("previously verified release cache is writable by another user")
    directory_metadata = cache_dir.stat()
    if os.geteuid() == 0 and directory_metadata.st_uid != 0:
        raise VerificationError("previously verified release cache directory is not root-owned")
    if os.geteuid() == 0 and directory_metadata.st_mode & 0o077:
        raise VerificationError("previously verified release cache directory is accessible to other users")
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError("previously verified release cache marker is invalid") from error
    if not isinstance(info, dict) or info.get("source_commit") != commit or info.get("verified") is not True:
        raise VerificationError("previously verified release cache does not match the requested commit")
    actual = sha256_file(archive)
    if actual != info.get("archive_sha256"):
        raise VerificationError("previously verified release cache archive was modified")
    if _parse_checksum_file(checksum, RELEASE_NAME.format(commit=commit)) != actual:
        raise VerificationError("previously verified release cache checksum was modified")
    return info


def _write_verified_cache(
    temporary: Path,
    cache_root: Path,
    commit: str,
    release_id: int,
    archive_digest: str,
    manifest: dict[str, Any],
) -> Path:
    cache_dir = cache_root / commit
    if cache_dir.exists() or cache_dir.is_symlink():
        raise VerificationError("a cache entry already exists for this commit and cannot be replaced")
    checksum_file = temporary / "bundle.sha256"
    checksum_file.write_text(
        f"{archive_digest}  {RELEASE_NAME.format(commit=commit)}\n", encoding="ascii"
    )
    (temporary / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    marker = {
        "source_commit": commit,
        "release_id": release_id,
        "archive_sha256": archive_digest,
        "verified": True,
        "verification": "GitHub Actions SLSA provenance and GitHub release asset digest",
    }
    (temporary / "verified.json").write_text(
        json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    for path in temporary.rglob("*"):
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod(0o444)
    temporary.chmod(0o700)
    cache_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    if os.geteuid() == 0:
        os.chown(cache_root, 0, 0)
    os.replace(temporary, cache_dir)
    return cache_dir


def _release_to_target(cache_dir: Path, release_root: Path, commit: str, uid: int, gid: int) -> Path:
    archive = cache_dir / "bundle.tar.gz"
    temporary_parent = release_root
    temporary_parent.mkdir(parents=True, exist_ok=True)
    if temporary_parent.is_symlink():
        raise VerificationError("Rust release directory must not be a symlink")
    target = release_root / commit
    if target.exists() or target.is_symlink():
        try:
            existing = validate_release_dir(target, commit, expected_origin="github")
            expected = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
            if existing != expected:
                raise VerificationError("existing release manifest differs from its verified bundle")
            return target
        except ReleaseError as error:
            raise VerificationError(
                f"refusing to overwrite an existing incomplete release directory: {target}: {error}"
            ) from error

    stage = Path(tempfile.mkdtemp(prefix=f".stage.{commit}.", dir=release_root))
    try:
        extracted = extract_archive(archive, stage / "extract", commit)
        staged_release = stage / "release"
        os.replace(extracted, staged_release)
        (stage / "extract").rmdir()
        manifest = validate_release_dir(staged_release, commit, expected_origin="github")
        expected = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest != expected:
            raise VerificationError("verified archive manifest changed after cache validation")
        for path in [staged_release, *staged_release.rglob("*")]:
            if path.is_dir():
                path.chmod(0o755)
            else:
                path.chmod(0o755 if path.name in RELEASE_BINARIES else 0o644)
            if os.geteuid() == 0:
                os.chown(path, uid, gid)
        if os.geteuid() == 0:
            os.chown(stage, uid, gid)
        os.replace(staged_release, target)
        return target
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def install_release(
    commit: str,
    release_root: Path,
    cache_root: Path,
    *,
    uid: int,
    gid: int,
    gh_path: str = "/usr/bin/gh",
    require_supported_target: bool = True,
    cached_only: bool = False,
) -> Path:
    if not valid_commit(commit):
        raise VerificationError("release commit must be a full lowercase Git SHA")
    if require_supported_target and not binary_target_supported():
        raise UnsupportedTarget(
            "prebuilt releases require aarch64 Debian or Raspberry Pi OS Trixie, or aarch64 Ubuntu"
        )

    release_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    if cache_root.is_symlink():
        raise VerificationError("release cache root must not be a symlink")
    cache_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    if os.geteuid() == 0:
        os.chown(cache_root, 0, 0)
        cache_root.chmod(0o755)
    cache_metadata = cache_root.stat()
    if cache_metadata.st_mode & 0o022:
        raise VerificationError("release cache root must not be group- or world-writable")
    if os.geteuid() == 0 and cache_metadata.st_uid != 0:
        raise VerificationError("release cache root must be owned by root")
    cache_dir = cache_root / commit
    if cache_dir.exists():
        _verify_cache_dir(cache_dir, commit)
    elif cached_only:
        raise MissingArtifact(f"no previously verified cached release exists for commit {commit}")
    else:
        release, assets = _release_assets(commit)
        archive_name = RELEASE_NAME.format(commit=commit)
        asset_names = {
            "archive": archive_name,
            "checksum": archive_name + CHECKSUM_SUFFIX,
            "bundle": archive_name + ATTESTATION_SUFFIX,
            "manifest": archive_name + MANIFEST_SUFFIX,
        }
        missing = [name for name in asset_names.values() if name not in assets]
        if missing:
            raise VerificationError(f"published release is missing required assets: {', '.join(missing)}")
        release_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".release-download.{commit}.", dir=cache_root) as temp_name:
            temporary = Path(temp_name)
            archive_path = temporary / "bundle.tar.gz"
            checksum_path = temporary / "bundle.sha256"
            bundle_path = temporary / "bundle.sigstore.json"
            sidecar_manifest = temporary / "sidecar-manifest.json"
            archive_digest = _download_asset(assets[asset_names["archive"]], archive_name, archive_path)
            _download_asset(
                assets[asset_names["checksum"]],
                asset_names["checksum"],
                checksum_path,
                max_size=SIDECAR_LIMIT,
            )
            _download_asset(
                assets[asset_names["bundle"]],
                asset_names["bundle"],
                bundle_path,
                max_size=SIDECAR_LIMIT,
            )
            _download_asset(
                assets[asset_names["manifest"]],
                asset_names["manifest"],
                sidecar_manifest,
                max_size=SIDECAR_LIMIT,
            )
            if _parse_checksum_file(checksum_path, archive_name) != archive_digest:
                raise VerificationError("release SHA-256 sidecar does not match GitHub's asset digest")
            _verify_attestation(archive_path, bundle_path, commit, gh_path)

            extract_root = temporary / "extracted"
            extracted_release = extract_archive(archive_path, extract_root, commit)
            manifest = validate_release_dir(extracted_release, commit, expected_origin="github")
            try:
                sidecar = json.loads(sidecar_manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise VerificationError("release manifest asset is invalid JSON") from error
            if sidecar != manifest:
                raise VerificationError("release manifest asset does not match the manifest in the archive")
            _check_runtime_compatibility(manifest)

            # The temporary directory is moved into a root-only cache only after
            # TLS, GitHub asset digests, the signed provenance, and archive contents pass.
            for old_name, new_name in (
                (archive_path, "bundle.tar.gz"),
                (checksum_path, "bundle.sha256"),
                (bundle_path, "bundle.sigstore.json"),
            ):
                os.replace(old_name, temporary / new_name)
            extracted_release.rename(temporary / "release-validated")
            shutil.rmtree(extract_root, ignore_errors=True)
            validated_dir = temporary / "release-validated"
            for path in [validated_dir, *validated_dir.rglob("*")]:
                if path.is_dir():
                    path.chmod(0o555)
                else:
                    path.chmod(0o555 if path.name in RELEASE_BINARIES else 0o444)
            validated_dir.rename(temporary / "release")
            cache_dir = _write_verified_cache(
                temporary,
                cache_root,
                commit,
                int(release.get("id", 0)),
                archive_digest,
                manifest,
            )

    cache_info = _verify_cache_dir(cache_dir, commit)
    _ = cache_info
    return _release_to_target(cache_dir, release_root, commit, uid, gid)


def _check_runtime_compatibility(manifest: dict[str, Any]) -> None:
    target = manifest.get("target")
    if not isinstance(target, dict) or target.get("triple") != "aarch64-unknown-linux-gnu":
        raise VerificationError("release manifest target does not match aarch64 Linux")
    abi = manifest.get("runtime_abi")
    if not isinstance(abi, dict) or abi.get("distribution") != "debian-trixie":
        raise VerificationError("release manifest is not built for the Debian Trixie runtime ABI")
    minimum = abi.get("glibc_minimum")
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", str(minimum))
    if not match:
        raise VerificationError("release manifest has an invalid glibc minimum version")
    current = platform.libc_ver()[1]
    current_match = re.fullmatch(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", current)
    if current_match is None:
        raise UnsupportedTarget("could not determine the installed glibc version")
    if tuple(int(item or 0) for item in match.groups()) > tuple(
        int(item or 0) for item in current_match.groups()
    ):
        raise UnsupportedTarget(f"release requires glibc {minimum}; this system has glibc {current}")


def verify_cached_binary(cache_dir: Path, commit: str, binary: str, destination: Path) -> None:
    if binary not in RELEASE_BINARIES:
        raise VerificationError("requested executable is not part of a LumiStripe release")
    _verify_cache_dir(cache_dir, commit)
    archive_path = cache_dir / "bundle.tar.gz"
    expected = f"release/{binary}"
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = archive.getmembers()
        seen: set[str] = set()
        total = 0
        selected: tarfile.TarInfo | None = None
        for member in members:
            name = _check_archive_member(member, seen)
            if member.isfile():
                total += member.size
                if total > EXPANDED_LIMIT:
                    raise VerificationError("cached release archive exceeds the extraction size limit")
            if name == expected:
                selected = member
        if selected is None or not selected.isfile() or selected.mode & 0o111 == 0:
            raise VerificationError(f"verified release is missing executable {binary}")
        source = archive.extractfile(selected)
        if source is None:
            raise VerificationError(f"could not read verified release executable {binary}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o700)
        with source, os.fdopen(descriptor, "wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        destination.chmod(0o755)


def _command_package(args: argparse.Namespace) -> None:
    package_release(
        args.release_dir,
        args.output,
        args.commit,
        project_root=args.project_root,
        glibc_minimum=args.glibc_minimum,
        build_info_path=args.build_info,
    )
    print(args.output)


def _command_seal(args: argparse.Namespace) -> None:
    make_manifest(args.release_dir, args.commit, origin="source", project_root=args.project_root)
    validate_release_dir(args.release_dir, args.commit, expected_origin="source")


def _command_migrate_static(args: argparse.Namespace) -> None:
    migrate_legacy_static(args.release_dir, args.static_dir, args.commit, args.project_root)


def _command_validate(args: argparse.Namespace) -> None:
    manifest = validate_release_dir(args.release_dir, args.commit, expected_origin=args.origin)
    if args.check_runtime:
        _check_runtime_compatibility(manifest)


def _command_install(args: argparse.Namespace) -> None:
    path = install_release(
        args.commit,
        args.release_root,
        args.cache_root,
        uid=args.uid,
        gid=args.gid,
        gh_path=args.gh,
        require_supported_target=not args.skip_target_check,
        cached_only=args.cached_only,
    )
    print(path)


def _command_extract_binary(args: argparse.Namespace) -> None:
    verify_cached_binary(args.cache_dir, args.commit, args.binary, args.destination)


def _add_path_argument(parser: argparse.ArgumentParser, name: str) -> None:
    parser.add_argument(name, type=Path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    package = commands.add_parser("package", help="write and package a CI release bundle")
    package.add_argument("--release-dir", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--commit", required=True)
    package.add_argument("--project-root", type=Path)
    package.add_argument("--glibc-minimum")
    package.add_argument("--build-info", type=Path)
    package.set_defaults(func=_command_package)

    seal = commands.add_parser("seal-local", help="write a manifest for a source build")
    seal.add_argument("--release-dir", type=Path, required=True)
    seal.add_argument("--commit", required=True)
    seal.add_argument("--project-root", type=Path)
    seal.set_defaults(func=_command_seal)

    migrate = commands.add_parser(
        "migrate-legacy-static", help="preserve a legacy frontend for rollback"
    )
    migrate.add_argument("--release-dir", type=Path, required=True)
    migrate.add_argument("--static-dir", type=Path, required=True)
    migrate.add_argument("--commit", required=True)
    migrate.add_argument("--project-root", type=Path)
    migrate.set_defaults(func=_command_migrate_static)

    validate = commands.add_parser("validate", help="validate a staged release directory")
    validate.add_argument("--release-dir", type=Path, required=True)
    validate.add_argument("--commit", required=True)
    validate.add_argument("--origin", choices=("github", "source"))
    validate.add_argument("--check-runtime", action="store_true")
    validate.set_defaults(func=_command_validate)

    install = commands.add_parser("install", help="download, verify, and stage a release")
    install.add_argument("--commit", required=True)
    install.add_argument("--release-root", type=Path, required=True)
    install.add_argument("--cache-root", type=Path, required=True)
    install.add_argument("--uid", type=int, required=True)
    install.add_argument("--gid", type=int, required=True)
    install.add_argument("--gh", default="/usr/bin/gh")
    install.add_argument(
        "--cached-only",
        action="store_true",
        help="stage only a previously verified release already present in the local cache",
    )
    install.add_argument("--skip-target-check", action="store_true", help=argparse.SUPPRESS)
    install.set_defaults(func=_command_install)

    extract = commands.add_parser("extract-binary", help="extract a binary from a verified cache")
    extract.add_argument("--cache-dir", type=Path, required=True)
    extract.add_argument("--commit", required=True)
    extract.add_argument("--binary", required=True)
    extract.add_argument("--destination", type=Path, required=True)
    extract.set_defaults(func=_command_extract_binary)

    args = parser.parse_args()
    try:
        args.func(args)
    except MissingArtifact as error:
        print(f"release artifact missing: {error}", file=sys.stderr)
        return 10
    except UnsupportedTarget as error:
        print(f"unsupported binary target: {error}", file=sys.stderr)
        return 11
    except VerificationError as error:
        print(f"release verification failed: {error}", file=sys.stderr)
        return 12
    except ReleaseError as error:
        print(f"release retrieval failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
