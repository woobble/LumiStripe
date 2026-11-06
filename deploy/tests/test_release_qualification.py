from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "release_qualification.py"
spec = importlib.util.spec_from_file_location("release_qualification", SCRIPT)
assert spec is not None and spec.loader is not None
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)

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
                "runtime_abi": {"glibc_minimum": "2.38"},
            }
        ),
        encoding="utf-8",
    )
    (release_dir.parent / "release-build-info.json").write_text(
        json.dumps(
            {
                "architecture": "aarch64",
                "target": "aarch64-unknown-linux-gnu",
            }
        ),
        encoding="utf-8",
    )
    return project, release_dir


def test_report_records_automated_static_and_unverified_evidence(tmp_path: Path) -> None:
    project, release_dir = _qualification_fixture(tmp_path)

    report = qualification.make_report(
        project_root=project,
        release_dir=release_dir,
        commit=COMMIT,
        deployment_tests="success",
        frontend_tests="success",
        rust_x86_tests="success",
        application_tests="success",
        stability_tests="skipped",
        rust_arm64_tests="success",
        trixie_runtime="passed",
        ubuntu_2604_runtime="passed",
        artifact_integrity="passed",
        provenance="passed",
    )

    by_name = {check["name"]: check for check in report["checks"]}
    assert report["source_commit"] == COMMIT
    assert report["toolchain"] == {"rust": "1.99.1", "bun": "1.4.4"}
    assert report["target"]["architecture"] == "aarch64"
    assert by_name["Deployment regression tests"]["result"] == "passed"
    assert by_name["Application REST, WebSocket, simulator, and engine tests"]["result"] == "passed"
    assert by_name["Opt-in simulator stability profile"]["result"] == "not verified"
    assert by_name["Sigstore build provenance"]["verification"] == "automated"
    raspios = by_name["Raspberry Pi OS Trixie ARM64 userspace compatibility"]
    assert raspios["result"] == "not verified"
    assert raspios["verification"] == "static inspection"
    assert by_name["Physical Raspberry Pi hardware behavior"]["result"] == "not verified"

    rendered = qualification.markdown_report(report)
    assert f"`{COMMIT}`" in rendered
    assert "not verified" in rendered
    assert "static inspection" in rendered


def test_report_rejects_status_values_that_could_be_misreported_as_passed() -> None:
    with pytest.raises(ValueError, match="invalid check result"):
        qualification._status("unknown")


def test_report_rejects_release_metadata_from_a_different_commit(tmp_path: Path) -> None:
    project, release_dir = _qualification_fixture(tmp_path)
    with pytest.raises(ValueError, match="do not match"):
        qualification.make_report(
            project_root=project,
            release_dir=release_dir,
            commit="c" * 40,
            deployment_tests="success",
            frontend_tests="success",
            rust_x86_tests="success",
            application_tests="success",
            stability_tests="skipped",
            rust_arm64_tests="success",
            trixie_runtime="passed",
            ubuntu_2604_runtime="passed",
            artifact_integrity="passed",
            provenance="passed",
        )
