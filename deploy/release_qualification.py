#!/usr/bin/env python3
"""Create machine-readable and Markdown release qualification summaries."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

RESULTS = {"passed", "failed", "not verified"}


def _status(value: str) -> str:
    normalized = value.strip().lower().replace("_", " ").replace("-", " ")
    normalized = {
        "success": "passed",
        "failure": "failed",
        "cancelled": "failed",
        "skipped": "not verified",
    }.get(normalized, normalized)
    if normalized not in RESULTS:
        raise ValueError(f"invalid check result: {value}")
    return normalized


def _toolchain_version(project_root: Path) -> str:
    toolchain = (project_root / "rust-toolchain.toml").read_text(encoding="utf-8")
    match = re.search(r"(?m)^channel\s*=\s*\"([^\"]+)\"\s*$", toolchain)
    if match is None:
        raise ValueError("rust-toolchain.toml has no pinned channel")
    return match.group(1)


def _check(
    name: str,
    result: str,
    verification: str,
    evidence: str,
) -> dict[str, str]:
    return {
        "name": name,
        "result": _status(result),
        "verification": verification,
        "evidence": evidence,
    }


def make_report(
    *,
    project_root: Path,
    release_dir: Path,
    commit: str,
    deployment_tests: str,
    frontend_tests: str,
    rust_x86_tests: str,
    application_tests: str,
    stability_tests: str,
    rust_arm64_tests: str,
    trixie_runtime: str,
    ubuntu_2604_runtime: str,
    artifact_integrity: str,
    provenance: str,
) -> dict[str, Any]:
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    build_info = json.loads(
        (release_dir.parent / "release-build-info.json").read_text(encoding="utf-8")
    )
    if manifest.get("source_commit") != commit or build_info.get("architecture") != "aarch64":
        raise ValueError("qualification inputs do not match the requested release commit/architecture")

    checks = [
        _check(
            "Deployment regression tests",
            deployment_tests,
            "automated",
            "GitHub-hosted deployment-tests job",
        ),
        _check(
            "Frontend contract, lint, tests, and build",
            frontend_tests,
            "automated",
            "GitHub-hosted frontend job",
        ),
        _check(
            "Rust formatting, Clippy, and x86_64 build",
            rust_x86_tests,
            "automated",
            "GitHub-hosted rust-engine job",
        ),
        _check(
            "Application REST, WebSocket, simulator, and engine tests",
            application_tests,
            "automated",
            "Rust workspace test suite on the GitHub-hosted x86_64 runner",
        ),
        _check(
            "Opt-in simulator stability profile",
            stability_tests,
            "automated when selected",
            "Bounded synthetic audio, mocked Soloist, REST, resource, and shutdown profile",
        ),
        _check(
            "Rust ARM64 tests and build",
            rust_arm64_tests,
            "automated",
            "GitHub-hosted rust-engine-arm64 job",
        ),
        _check(
            "Debian Trixie ARM64 runtime and release startup",
            trixie_runtime,
            "automated",
            "AArch64 release-smoke image and packaged release smoke test",
        ),
        _check(
            "Ubuntu 26.04 LTS ARM64 runtime compatibility",
            ubuntu_2604_runtime,
            "automated",
            "Ubuntu 26.04 ARM64 container using the packaged release directory",
        ),
        _check(
            "Raspberry Pi OS Trixie ARM64 userspace compatibility",
            "not verified",
            "static inspection",
            "Raspberry Pi OS is Trixie-based; no OS image is executed in hosted CI",
        ),
        _check(
            "Release manifest, source commit, ELF architecture, and dependencies",
            artifact_integrity,
            "automated",
            "release_artifact.py validation and ARM64 smoke checks",
        ),
        _check(
            "Sigstore build provenance",
            provenance,
            "automated",
            "gh attestation verify pinned to ci.yml and the source commit",
        ),
        _check(
            "Live Spotify authentication and playback",
            "not verified",
            "manual acceptance required",
            "No Spotify account or live provider is used by CI",
        ),
        _check(
            "Physical Raspberry Pi hardware behavior",
            "not verified",
            "manual acceptance required",
            "GPIO/SPI, Bluetooth radio, audio devices, power, and thermal behavior are not exercised",
        ),
    ]
    return {
        "schema_version": 1,
        "source_commit": commit,
        "toolchain": {
            "rust": _toolchain_version(project_root),
            "bun": (project_root / ".bun-version").read_text(encoding="utf-8").strip(),
        },
        "target": {
            "architecture": build_info["architecture"],
            "triple": build_info["target"],
            "build_environment": "debian:trixie-slim",
            "glibc_minimum": manifest["runtime_abi"]["glibc_minimum"],
        },
        "checks": checks,
        "known_limitations": [
            "Raspberry Pi OS Trixie is not independently run in a hosted container; its hardware remains unverified.",
            "Ubuntu 24.04 LTS is outside the prebuilt binary support matrix; source mode is available.",
            "Live Spotify authentication and physical audio/LED behavior are manual acceptance checks.",
        ],
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        f"# LumiStripe release qualification: `{report['source_commit']}`",
        "",
        f"- Rust toolchain: `{report['toolchain']['rust']}`",
        f"- Bun: `{report['toolchain']['bun']}`",
        f"- Target: `{report['target']['triple']}` ({report['target']['architecture']})",
        f"- Build environment: `{report['target']['build_environment']}`",
        f"- Declared glibc minimum: `{report['target']['glibc_minimum']}`",
        "",
        "| Validation | Result | Evidence type | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for check in report["checks"]:
        lines.append(
            "| {name} | {result} | {verification} | {evidence} |".format(
                **{key: str(value).replace("|", "\\|") for key, value in check.items()}
            )
        )
    lines.extend(["", "## Known limitations", ""])
    lines.extend(f"- {limitation}" for limitation in report["known_limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--deployment-tests", required=True)
    parser.add_argument("--frontend-tests", required=True)
    parser.add_argument("--rust-x86-tests", required=True)
    parser.add_argument("--application-tests", required=True)
    parser.add_argument("--stability-tests", required=True)
    parser.add_argument("--rust-arm64-tests", required=True)
    parser.add_argument("--trixie-runtime", required=True)
    parser.add_argument("--ubuntu-2604-runtime", required=True)
    parser.add_argument("--artifact-integrity", required=True)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        report = make_report(
            project_root=args.project_root,
            release_dir=args.release_dir,
            commit=args.commit,
            deployment_tests=args.deployment_tests,
            frontend_tests=args.frontend_tests,
            rust_x86_tests=args.rust_x86_tests,
            application_tests=args.application_tests,
            stability_tests=args.stability_tests,
            rust_arm64_tests=args.rust_arm64_tests,
            trixie_runtime=args.trixie_runtime,
            ubuntu_2604_runtime=args.ubuntu_2604_runtime,
            artifact_integrity=args.artifact_integrity,
            provenance=args.provenance,
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "release-qualification.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (args.output_dir / "release-qualification.md").write_text(
            markdown_report(report),
            encoding="utf-8",
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"could not generate release qualification report: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
