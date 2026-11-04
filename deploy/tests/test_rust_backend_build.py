from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BUILDER = Path(__file__).parents[1] / "build-rust-backend.sh"
UPDATE_SUPPORT = Path(__file__).parents[1] / "update-support.sh"


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
        "if args == ['+1.88.0', '--version']:\n"
        "    print('cargo 1.88.0')\n"
        "    raise SystemExit(0)\n"
        "with open(os.environ['LUMI_TEST_CARGO_LOG'], 'a') as log:\n"
        "    log.write(' '.join(args) + '\\n')\n"
        "if os.environ.get('LUMI_TEST_FAIL_ON') and os.environ['LUMI_TEST_FAIL_ON'] in args:\n"
        "    raise SystemExit(17)\n"
        "name = 'lumistripe-engine' if 'lumistripe-engine' in args else 'lumistripe-web'\n"
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


@pytest.mark.skipif(os.geteuid() == 0, reason="builder intentionally rejects root")
def test_rust_update_build_promotes_a_complete_commit_matched_pair(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "rust").mkdir(parents=True)
    (project / "rust" / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (project / ".gitignore").write_text("/rust/target/\n", encoding="utf-8")
    _git(project, "init", "--quiet")
    _git(project, "config", "user.name", "LumiStripe test")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "commit.gpgsign", "false")
    _git(project, "config", "core.hooksPath", "/dev/null")
    _git(project, "add", "rust/Cargo.toml", ".gitignore")
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

    current = project / "rust" / "target" / "rust-deploy" / "current"
    release = current.resolve()
    assert os.readlink(current) == f"releases/{first_commit}"
    assert (release / "source-commit").read_text(encoding="utf-8").strip() == first_commit
    assert "first:lumistripe-engine" in (release / "lumistripe-engine").read_text(
        encoding="utf-8"
    )
    assert "first:lumistripe-web" in (release / "lumistripe-web").read_text(
        encoding="utf-8"
    )
    for name in ("lumistripe-web", "lumistripe-engine"):
        assert (release / name).stat().st_mode & 0o111

    commands = cargo_log.read_text(encoding="utf-8").splitlines()
    assert len(commands) == 2
    assert all(command.startswith("+1.88.0 build --locked --release") for command in commands)
    assert "lumistripe-engine" in commands[0]
    assert "--features bluez,pipewire" in commands[1]

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
    launcher = Path(__file__).parents[1] / "lumistripe-web-launcher.sh"
    launched = subprocess.run(
        ["bash", str(launcher)],
        check=False,
        env=launcher_env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert launched.returncode == 0, launched.stderr
    launch_lines = launch_log.read_text(encoding="utf-8").splitlines()
    assert launch_lines[0] == "first:lumistripe-web"
    assert "--engine-binary" in launch_lines[1]
    assert f"{release}/lumistripe-engine" in launch_lines[1]
    assert f"{current}/lumistripe-engine" not in launch_lines[1]

    second_commit = _commit(project, "second.txt")
    stale_launch = subprocess.run(
        ["bash", str(launcher)],
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
        project
        / "rust"
        / "target"
        / "rust-deploy"
        / "releases"
        / second_commit
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
    (project / "rust").mkdir(parents=True)
    (project / "rust" / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (project / ".gitignore").write_text("/rust/target/\n", encoding="utf-8")
    _git(project, "init", "--quiet")
    _git(project, "config", "user.name", "LumiStripe test")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "commit.gpgsign", "false")
    _git(project, "config", "core.hooksPath", "/dev/null")
    _git(project, "add", "rust/Cargo.toml", ".gitignore")
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
    assert not (project / "rust" / "target" / "rust-deploy" / "current").exists()
