from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1]
SUPPORT = DEPLOY / "update-support.sh"
WRAPPER = DEPLOY / "lumistripe-network"


def _git(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _release(project: Path, source_commit: str) -> Path:
    deploy_root = project / "rust" / "target" / "rust-deploy"
    release = deploy_root / "releases" / source_commit
    release.mkdir(parents=True)
    (release / "source-commit").write_text(f"{source_commit}\n", encoding="utf-8")
    binary = release / "lumistripe-deploy-helper"
    binary.write_text("rust helper fixture\n", encoding="utf-8")
    binary.chmod(0o755)
    (deploy_root / "current").symlink_to(Path("releases") / source_commit)
    return release


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "--quiet")
    _git(project, "config", "user.name", "LumiStripe test")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "commit.gpgsign", "false")
    marker = project / "marker.txt"
    marker.write_text("source commit\n", encoding="utf-8")
    _git(project, "add", "marker.txt")
    _git(project, "commit", "--quiet", "-m", "fixture")
    return project, _git(project, "rev-parse", "HEAD")


def _run_helper_install(project: Path, destination: Path) -> subprocess.CompletedProcess[str]:
    install = r'''
install() {
  local make_directory=0
  if [[ "$1" == -d ]]; then make_directory=1; shift; fi
  while (($#)); do
    case "$1" in
      -o|-g|-m) shift 2 ;;
      *) break ;;
    esac
  done
  if ((make_directory)); then
    mkdir -p -- "$@"
  else
    cp -- "$1" "$2"
    chmod 755 -- "$2"
  fi
}
'''
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; PROJECT_DIR="$2"; NETWORK_LIBEXEC_DIR="$3"; '
            + install
            + "install_privileged_network_helper",
            "test-network-helper-install",
            str(SUPPORT),
            str(project),
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_network_wrapper_invokes_only_the_rust_network_subcommand() -> None:
    wrapper = WRAPPER.read_text(encoding="utf-8")
    support = SUPPORT.read_text(encoding="utf-8")

    assert "lumistripe-network-helper network" in wrapper
    assert "python" not in wrapper.lower()
    assert "install_privileged_network_helper" in support
    assert 'rm -f -- "$NETWORK_LIBEXEC_DIR/lumistripe_network.py"' in support
    assert '"$PROJECT_DIR/deploy/lumistripe_network.py"' not in support


@pytest.mark.parametrize("match_commit", [True, False])
def test_privileged_helper_install_requires_the_current_commit(
    tmp_path: Path, match_commit: bool
) -> None:
    project, commit = _project(tmp_path)
    release_commit = commit if match_commit else "a" * 40
    release = _release(project, release_commit)
    destination = tmp_path / "root-owned-network"

    result = _run_helper_install(project, destination)

    if match_commit:
        assert result.returncode == 0, result.stderr
        installed = destination / "lumistripe-network-helper"
        assert installed.read_text(encoding="utf-8") == "rust helper fixture\n"
        assert os.stat(installed).st_mode & 0o777 == 0o755
    else:
        assert result.returncode != 0
        assert "does not match" in result.stderr
        assert not (destination / "lumistripe-network-helper").exists()
    assert release.is_dir()
