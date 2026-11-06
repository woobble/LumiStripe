#!/usr/bin/env python3
"""Start and exercise a packaged ARM64 release in its target runtime."""

from __future__ import annotations

import argparse
import html.parser
import importlib.util
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _load_release_artifact() -> Any:
    script = Path(__file__).with_name("release_artifact.py")
    spec = importlib.util.spec_from_file_location("release_artifact", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load release verifier: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Assets(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"src", "href"} and value:
                path = urllib.parse.urlparse(value).path
                if path.startswith("/assets/"):
                    self.paths.append(path)


def _run(command: list[str], *, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _check_binary_abi(release_dir: Path, manifest: dict[str, Any]) -> None:
    if platform.machine() not in {"aarch64", "arm64"}:
        raise RuntimeError(f"smoke container is not ARM64: {platform.machine()}")

    minimum = str(manifest["runtime_abi"]["glibc_minimum"])
    required: list[str] = []
    for name in manifest["binaries"]:
        binary = release_dir / name
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeError(f"required executable is missing or not executable: {binary}")

        file_output = _run(["file", str(binary)]).stdout
        if "ELF 64-bit" not in file_output or "ARM aarch64" not in file_output:
            raise RuntimeError(f"{name} is not an AArch64 ELF executable: {file_output.strip()}")

        header = _run(["readelf", "-h", str(binary)]).stdout
        if not re.search(r"^\s*Machine:\s+AArch64\s*$", header, flags=re.MULTILINE):
            raise RuntimeError(f"{name} ELF header does not declare AArch64")

        linked = _run(["ldd", str(binary)]).stdout
        if "not found" in linked:
            raise RuntimeError(f"{name} has unresolved runtime libraries:\n{linked}")

        versions = _run(["readelf", "--version-info", str(binary)]).stdout
        required.extend(re.findall(r"GLIBC_([0-9]+\.[0-9]+(?:\.[0-9]+)?)", versions))

    if not required:
        raise RuntimeError("release binaries declare no dynamic GLIBC requirements")
    maximum = max(required, key=_version_tuple)
    if _version_tuple(maximum) > _version_tuple(minimum):
        raise RuntimeError(
            f"release requires glibc {maximum}, above its manifest minimum {minimum}"
        )

    toolchain = ["cargo", "rustc", "bun", "cc", "gcc", "make"]
    present = [name for name in toolchain if shutil.which(name)]
    if present:
        raise RuntimeError(
            "release runtime unexpectedly contains source-build tools: "
            + ", ".join(present)
        )


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read(url: str, *, timeout: float = 2) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def _wait_for_health(url: str, process: subprocess.Popen[str], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no health response"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"web process exited before health check ({process.returncode})")
        try:
            status, body, _content_type = _read(url)
            if status == 200 and json.loads(body) == {"status": "ok"}:
                return
            last_error = f"unexpected health response: HTTP {status} {body[:300]!r}"
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error)
        time.sleep(0.2)
    raise RuntimeError(f"web health check timed out: {last_error}")


def _exercise_web(release_dir: Path) -> None:
    port = _unused_loopback_port()
    with tempfile.TemporaryDirectory(prefix="lumistripe-release-smoke-") as config_dir:
        config = Path(config_dir)
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(config),
                "XDG_CONFIG_HOME": str(config),
                "LUMI_SPOTIFY_BACKEND": "disabled",
                "RUST_LOG": "warn",
            }
        )
        command = [
            str(release_dir / "lumistripe-web"),
            "--simulate",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--static-dir",
            str(release_dir / "static"),
            "--settings-file",
            str(config / "settings.json"),
            "--spotify-backend",
            "disabled",
        ]
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
                start_new_session=True,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                _wait_for_health(f"{base}/api/health", process)

                status, index, content_type = _read(f"{base}/")
                if status != 200 or "text/html" not in content_type or b"LumiStripe" not in index:
                    raise RuntimeError("release frontend index did not load correctly")

                assets = _Assets()
                assets.feed(index.decode("utf-8", errors="replace"))
                if not assets.paths:
                    raise RuntimeError("release frontend index does not reference static assets")
                for asset in assets.paths:
                    asset_status, asset_body, asset_type = _read(f"{base}{asset}")
                    if asset_status != 200 or not asset_body or "text/html" in asset_type:
                        raise RuntimeError(f"release frontend asset did not load: {asset}")

                process.send_signal(signal.SIGTERM)
                try:
                    result = process.wait(timeout=20)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError("web server did not shut down after SIGTERM") from error
                if result != 0:
                    raise RuntimeError(f"web server exited unsuccessfully during shutdown: {result}")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                log.seek(0)
                captured = log.read()
                if process.returncode not in (0, None):
                    raise RuntimeError(f"web server output:\n{captured[-4000:]}")


def run_in_runtime(release_dir: Path, commit: str, *, require_manifest: bool) -> None:
    verifier = _load_release_artifact()
    if require_manifest:
        manifest = verifier.validate_release_dir(
            release_dir,
            commit,
            expected_origin="github",
        )
        verifier._check_runtime_compatibility(manifest)
    else:
        marker = release_dir / "source-commit"
        if not marker.is_file() or marker.read_text(encoding="ascii").strip() != commit:
            raise RuntimeError("release source-commit marker does not match the expected commit")
        manifest = {
            "binaries": list(verifier.RELEASE_BINARIES),
            "runtime_abi": {"glibc_minimum": verifier._glibc_version()},
        }

    _check_binary_abi(release_dir, manifest)

    cli = _run([str(release_dir / "lumistripe"), "list-animations"])
    if not cli.stdout.strip():
        raise RuntimeError("Rust CLI returned an empty animation catalog")

    helper = _run([str(release_dir / "lumistripe-deploy-helper"), "--help"])
    if "Restricted LumiStripe deployment utilities" not in helper.stdout:
        raise RuntimeError("deployment helper did not display its command interface")

    _exercise_web(release_dir)


def _container_command(args: argparse.Namespace) -> list[str]:
    release_dir = args.release_dir.resolve(strict=True)
    if not args.image:
        raise RuntimeError("--image is required when running the host-side Docker driver")
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--mount",
        f"type=bind,src={release_dir},dst=/release,readonly",
        "--tmpfs",
        "/tmp/lumistripe-smoke:rw,nosuid,nodev,mode=1777",
        "--entrypoint",
        "python3",
        args.image,
        "/usr/local/libexec/lumistripe/release_smoke.py",
        "--inside-container",
        "--release-dir",
        "/release",
        "--commit",
        args.commit,
        *( ["--require-manifest"] if args.require_manifest else [] ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--image", help="runtime image for host-side Docker invocation")
    parser.add_argument("--inside-container", action="store_true")
    parser.add_argument("--require-manifest", action="store_true")
    args = parser.parse_args()

    try:
        if args.inside_container:
            run_in_runtime(args.release_dir, args.commit, require_manifest=args.require_manifest)
        else:
            verifier = _load_release_artifact()
            verifier.validate_release_dir(args.release_dir, args.commit, expected_origin="github")
            _run(_container_command(args), timeout=120)
        print("Release artifact smoke test passed.")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"Release artifact smoke test failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
