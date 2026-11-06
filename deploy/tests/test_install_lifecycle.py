from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DEPLOY_TESTS = Path(__file__).parent


def _context_archive() -> bytes:
    content = io.BytesIO()

    def include_deploy_source(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(member.name).parts
        if any(part in {"tests", "__pycache__", "docker"} for part in parts):
            return None
        return member

    with tarfile.open(fileobj=content, mode="w:gz") as archive:
        archive.add(
            DEPLOY_TESTS / "install_lifecycle.Dockerfile",
            arcname="Dockerfile",
        )
        archive.add(
            DEPLOY_TESTS / "install_lifecycle.sh",
            arcname="install_lifecycle.sh",
        )
        archive.add(
            DEPLOY_TESTS / "install_binary_lifecycle.sh",
            arcname="install_binary_lifecycle.sh",
        )
        archive.add(
            DEPLOY_TESTS / "install_lifecycle_setup.sh",
            arcname="install_lifecycle_setup.sh",
        )
        for filename in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", ".bun-version", ".gitignore"):
            source = ROOT / filename
            if source.is_file():
                archive.add(source, arcname=f"repo/{filename}")
        archive.add(
            ROOT / "deploy",
            arcname="repo/deploy",
            filter=include_deploy_source,
        )
        archive.add(
            ROOT / "apps/lumistripe-web/Cargo.toml",
            arcname="repo/apps/lumistripe-web/Cargo.toml",
        )
        archive.add(
            ROOT / "apps/lumistripe-web/frontend",
            arcname="repo/apps/lumistripe-web/frontend",
            recursive=False,
        )
    return content.getvalue()


def test_production_admin_install_and_update_lifecycle_in_isolated_container(
    tmp_path: Path,
) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is unavailable")

    docker_environment = os.environ.copy()
    docker_environment["DOCKER_CONFIG"] = str(tmp_path / "docker-config")
    Path(docker_environment["DOCKER_CONFIG"]).mkdir()
    daemon = subprocess.run(
        [docker, "info"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=docker_environment,
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable")

    image = f"lumistripe-install-lifecycle:{uuid.uuid4().hex[:12]}"
    try:
        build = subprocess.run(
            [docker, "build", "--tag", image, "--file", "Dockerfile", "-"],
            input=_context_archive(),
            check=False,
            capture_output=True,
            timeout=900,
            env=docker_environment,
        )
        if build.returncode != 0:
            pytest.fail(
                "Isolated installer image build failed:\n"
                f"{build.stdout.decode('utf-8', 'replace')}\n"
                f"{build.stderr.decode('utf-8', 'replace')}"
            )

        run = subprocess.run(
            [docker, "run", "--rm", "--network", "none", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
            env=docker_environment,
        )
        if run.returncode != 0:
            pytest.fail(
                "Production install/update lifecycle failed in the isolated container:\n"
                f"{run.stdout}\n{run.stderr}"
            )
        assert "isolated install and update lifecycle passed" in run.stdout

        binary_run = subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "--network",
                "none",
                image,
                "/usr/local/sbin/lumistripe-install-binary-lifecycle",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
            env=docker_environment,
        )
        if binary_run.returncode != 0:
            pytest.fail(
                "Production binary install lifecycle failed in the isolated container:\n"
                f"{binary_run.stdout}\n{binary_run.stderr}"
            )
        assert "isolated binary install lifecycle passed" in binary_run.stdout
    finally:
        subprocess.run(
            [docker, "image", "rm", "--force", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=docker_environment,
        )
