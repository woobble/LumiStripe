from __future__ import annotations

import json
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).parents[1]
SCRIPT = DEPLOY / "release_qualification.sh"
FIXTURES = DEPLOY / "tests/fixtures"
COMMIT = "b" * 40


def _qualification_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "rust-toolchain.toml").write_text('channel = "1.99.1"\n', encoding="utf-8")
    (project / ".bun-version").write_text("1.4.4\n", encoding="utf-8")
    release_dir = tmp_path / "release-root" / "release"
    release_dir.mkdir(parents=True)
    (release_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_commit": COMMIT,
                "target": {
                    "triple": "aarch64-unknown-linux-gnu",
                    "architecture": "aarch64",
                    "abi": "GNU/Linux",
                },
                "runtime_abi": {"distribution": "debian-trixie", "glibc_minimum": "2.38"},
                "toolchain": {
                    "rust": "1.99.1",
                    "bun": "1.4.4",
                    "builder": "debian:trixie-slim",
                },
                "binaries": [
                    "lumistripe-web",
                    "lumistripe-engine",
                    "lumistripe",
                    "lumistripe-deploy-helper",
                ],
                "checksums": {},
            }
        ),
        encoding="utf-8",
    )
    (release_dir.parent / "release-build-info.json").write_text(
        json.dumps(
            {
                "architecture": "aarch64",
                "target": "aarch64-unknown-linux-gnu",
                "distribution": "debian-trixie",
                "glibc_minimum": "2.38",
            }
        ),
        encoding="utf-8",
    )
    return project, release_dir


def _arguments(project: Path, output: Path, *, release_dir: Path | None = None) -> list[str]:
    arguments = [
        str(SCRIPT),
        "--project-root",
        str(project),
        "--commit",
        COMMIT,
        "--deployment-tests",
        "success",
        "--frontend-tests",
        "success",
        "--rust-x86-tests",
        "success",
        "--application-tests",
        "success",
        "--stability-tests",
        "skipped",
        "--rust-arm64-tests",
        "success",
        "--release-source",
        "success",
        "--release-smoke",
        "success",
        "--trixie-runtime",
        "passed",
        "--ubuntu-2604-runtime",
        "passed",
        "--artifact-integrity",
        "passed",
        "--provenance",
        "passed",
        "--output-dir",
        str(output),
    ]
    if release_dir is not None:
        arguments.extend(["--release-dir", str(release_dir)])
    return arguments


def _run(project: Path, output: Path, *, release_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", *_arguments(project, output, release_dir=release_dir)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_report_matches_legacy_json_and_markdown_fixtures(tmp_path: Path) -> None:
    project, release_dir = _qualification_fixture(tmp_path)
    output = tmp_path / "reports"

    result = _run(project, output, release_dir=release_dir)

    assert result.returncode == 0, result.stderr
    assert (output / "release-qualification.json").read_bytes() == (
        FIXTURES / "release-qualification.json"
    ).read_bytes()
    assert (output / "release-qualification.md").read_bytes() == (
        FIXTURES / "release-qualification.md"
    ).read_bytes()


def test_report_distinguishes_passed_failed_and_not_verified(tmp_path: Path) -> None:
    project, _release_dir = _qualification_fixture(tmp_path)
    output = tmp_path / "reports"
    arguments = _arguments(project, output)
    arguments[arguments.index("--frontend-tests") + 1] = "failure"
    arguments[arguments.index("--trixie-runtime") + 1] = "skipped"

    result = subprocess.run(["bash", *arguments], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    report = json.loads((output / "release-qualification.json").read_text(encoding="utf-8"))
    by_name = {check["name"]: check for check in report["checks"]}
    assert by_name["Deployment regression tests"]["result"] == "passed"
    assert by_name["Frontend contract, lint, tests, and build"]["result"] == "failed"
    assert by_name["Debian Trixie ARM64 runtime compatibility"]["result"] == "not verified"
    assert by_name["Raspberry Pi OS Trixie ARM64 userspace compatibility"]["verification"] == "static inspection"


def test_report_rejects_invalid_status_and_commit(tmp_path: Path) -> None:
    project, _release_dir = _qualification_fixture(tmp_path)
    output = tmp_path / "reports"
    arguments = _arguments(project, output)
    arguments[arguments.index("--deployment-tests") + 1] = "unknown"

    result = subprocess.run(["bash", *arguments], check=False, capture_output=True, text=True)

    assert result.returncode != 0
    assert "invalid check result" in result.stderr
    arguments = _arguments(project, output)
    arguments[arguments.index("--commit") + 1] = "not-a-commit"
    result = subprocess.run(["bash", *arguments], check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert "full lowercase Git SHA" in result.stderr


def test_report_rejects_release_metadata_for_a_different_commit(tmp_path: Path) -> None:
    project, release_dir = _qualification_fixture(tmp_path)
    manifest_path = release_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_commit"] = "c" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "reports"

    result = _run(project, output, release_dir=release_dir)

    assert result.returncode != 0
    assert "release manifest" in result.stderr
    assert not (output / "release-qualification.json").exists()


def test_markdown_escapes_untrusted_build_environment(tmp_path: Path) -> None:
    project, _release_dir = _qualification_fixture(tmp_path)
    output = tmp_path / "reports"
    arguments = _arguments(project, output)
    arguments.extend(["--build-environment", "runner|name\nsecond-line`"])

    result = subprocess.run(["bash", *arguments], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    markdown = (output / "release-qualification.md").read_text(encoding="utf-8")
    assert "runner\\|name second-line\\`" in markdown
    assert "| second-line` |" not in markdown
