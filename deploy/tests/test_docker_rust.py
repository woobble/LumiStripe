from __future__ import annotations

import io
import os
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DOCKERFILE = ROOT / "deploy/docker/Dockerfile"
COMPOSE_FILE = ROOT / "deploy/docker-compose.dev.yml"


def _docker_context_archive() -> bytes:
    archive_buffer = io.BytesIO()

    def include_build_inputs(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
        excluded_parts = {
            ".git",
            ".venv",
            "__pycache__",
            "dist",
            "graphify-out",
            "node_modules",
            "target",
        }
        if any(part in excluded_parts for part in Path(member.name).parts):
            return None
        if member.name.endswith(".py"):
            return None
        return member

    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        archive.add(DOCKERFILE, arcname="Dockerfile")
        for filename in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml"):
            archive.add(ROOT / filename, arcname=filename)
        for app in (
            "lumistripe-cli",
            "lumistripe-engine",
            "lumistripe-deploy-helper",
        ):
            archive.add(
                ROOT / "apps" / app,
                arcname=f"apps/{app}",
                filter=include_build_inputs,
            )
        for component in ("Cargo.toml", "src", "tests"):
            source = ROOT / "apps/lumistripe-web" / component
            if source.exists():
                archive.add(
                    source,
                    arcname=f"apps/lumistripe-web/{component}",
                    filter=include_build_inputs,
                )
        archive.add(ROOT / "crates", arcname="crates", filter=include_build_inputs)
        archive.add(
            ROOT / "tests/fixtures",
            arcname="tests/fixtures",
            filter=include_build_inputs,
        )
        archive.add(
            ROOT / "apps/lumistripe-web/frontend",
            arcname="apps/lumistripe-web/frontend",
            filter=include_build_inputs,
        )
        archive.add(
            ROOT / "deploy/docker/spotify-entrypoint.sh",
            arcname="deploy/docker/spotify-entrypoint.sh",
        )

    return archive_buffer.getvalue()


def test_docker_development_image_builds_rust_binaries_without_python() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8").lower()
    compose = COMPOSE_FILE.read_text(encoding="utf-8").lower()
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "arg rust_version=1.88.0" in dockerfile
    assert "from rust:${rust_version}-slim-trixie" in dockerfile
    assert "arg bun_version=1.4.4" in dockerfile
    assert "from oven/bun:${bun_version}" in dockerfile
    assert "--package lumistripe-engine" in dockerfile
    assert "--package lumistripe-web" in dockerfile
    assert "--package lumistripe-cli" in dockerfile
    assert "--package lumistripe-deploy-helper" in dockerfile
    assert "--features bluez,pipewire" in dockerfile
    assert "--engine-binary" not in dockerfile
    assert "arg lumi_soloist_fixture=0" in dockerfile
    assert "soloist-download" in dockerfile
    assert "python" not in dockerfile
    assert "uv" not in dockerfile
    assert "pip install" not in dockerfile
    assert "pip3 install" not in dockerfile
    assert "!Cargo.toml" in dockerignore
    assert "!apps/lumistripe-engine/src/*.rs" in dockerignore
    assert "!crates/**" in dockerignore
    assert "!tests/fixtures/**" in dockerignore
    assert "!target/" not in dockerignore
    assert "rust/Cargo.toml" not in dockerfile
    assert "!apps/lumistripe-web/frontend/**" in dockerignore
    assert "apps/lumistripe-web/frontend/.env*" in dockerignore

    assert ".venv" not in compose
    assert "python" not in compose
    assert "curl --fail" in compose
    assert 'profiles: ["spotify"]' in compose
    assert "depends_on:" not in compose
    assert "/var/lib/lumistripe/settings.json" in compose
    assert "--engine-binary" not in compose
    assert ".docker-dev/data/soloist" in compose
    assert "/run/secrets/spotify_api_key" in compose
    assert "user: " in compose


def test_rust_docker_image_builds_and_starts_the_simulator(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is unavailable")
    try:
        docker_config = tmp_path / "docker-config"
        docker_config.mkdir()
        docker_environment = os.environ.copy()
        docker_environment["DOCKER_CONFIG"] = str(docker_config)
        daemon = subprocess.run(
            [docker, "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=docker_environment,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("Docker daemon did not respond")
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable")

    image = f"lumistripe-rust-smoke:{uuid.uuid4().hex[:12]}"
    container = f"lumistripe-rust-smoke-{uuid.uuid4().hex[:12]}"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    try:
        build = subprocess.run(
            [
                docker,
                "build",
                "--tag",
                image,
                "--build-arg",
                "LUMI_SOLOIST_FIXTURE=1",
                "--file",
                "Dockerfile",
                "-",
            ],
            check=False,
            capture_output=True,
            input=_docker_context_archive(),
            timeout=3600,
            env=docker_environment,
        )
        if build.returncode != 0:
            pytest.fail(
                "Rust Docker build failed:\n"
                f"{build.stdout.decode('utf-8', 'replace')}\n"
                f"{build.stderr.decode('utf-8', 'replace')}"
            )

        run = subprocess.run(
            [
                docker,
                "run",
                "--detach",
                "--name",
                container,
                "--network",
                "host",
                "--tmpfs",
                "/var/lib/lumistripe:rw,uid=10001,gid=10001",
                image,
                "--simulate",
                "--port",
                str(port),
                "--static-dir",
                "/opt/lumistripe/static",
                "--settings-file",
                "/var/lib/lumistripe/settings.json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=docker_environment,
        )
        if run.returncode != 0:
            pytest.fail(f"Rust Docker simulator did not start:\n{run.stdout}\n{run.stderr}")

        health_url = f"http://127.0.0.1:{port}/api/health"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=1) as response:
                    assert response.status == 200
                    break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.25)
        else:
            logs = subprocess.run(
                [docker, "logs", container],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=docker_environment,
            )
            pytest.fail(f"Rust Docker simulator did not become healthy:\n{logs.stdout}\n{logs.stderr}")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
            assert response.status == 200
            assert b"LumiStripe" in response.read()

        user = subprocess.run(
            [docker, "exec", container, "/usr/bin/id", "-u"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=docker_environment,
        )
        assert user.stdout.strip() == "10001"
        python = subprocess.run(
            [docker, "exec", container, "/bin/sh", "-c", "command -v python3"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=docker_environment,
        )
        assert python.returncode != 0
    finally:
        subprocess.run(
            [docker, "rm", "--force", container],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=docker_environment,
        )
        subprocess.run(
            [docker, "image", "rm", "--force", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=docker_environment,
        )
