from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "release_artifact.py"
WORKFLOW = Path(__file__).parents[2] / ".github/workflows/ci.yml"
SPEC = importlib.util.spec_from_file_location("release_artifact", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_artifact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_artifact)

COMMIT = "a" * 40


def _release(directory: Path, commit: str = COMMIT) -> Path:
    directory.mkdir(parents=True)
    for name in release_artifact.RELEASE_BINARIES:
        binary = directory / name
        binary.write_text(f"#!/bin/sh\necho {name}\n", encoding="utf-8")
        binary.chmod(0o755)
    static = directory / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html>release\n", encoding="utf-8")
    (static / "assets/app.js").write_text("console.log('release')\n", encoding="utf-8")
    project_root = directory.parent
    (project_root / ".bun-version").write_text("1.4.4\n", encoding="utf-8")
    release_artifact.make_manifest(
        directory,
        commit,
        origin="github",
        project_root=project_root,
        glibc_minimum="2.36",
    )
    return directory


def _bundle(tmp_path: Path, commit: str = COMMIT) -> tuple[Path, Path]:
    release = _release(tmp_path / "release", commit)
    archive = tmp_path / f"lumistripe-linux-aarch64-{commit}.tar.gz"
    release_artifact.package_release(
        release,
        archive,
        commit,
        project_root=tmp_path,
        glibc_minimum="2.36",
    )
    return release, archive


def _asset(name: str, payload: bytes) -> dict[str, object]:
    return {
        "name": name,
        "size": len(payload),
        "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
        "browser_download_url": f"https://github.com/woobble/LumiStripe/releases/download/build-{COMMIT}/{name}",
    }


def test_package_is_deterministic_and_extracts_a_commit_matched_release(tmp_path: Path) -> None:
    release, archive = _bundle(tmp_path)
    first_digest = release_artifact.sha256_file(archive)
    second_archive = tmp_path / "second.tar.gz"
    release_artifact.package_release(
        release,
        second_archive,
        COMMIT,
        project_root=tmp_path,
        glibc_minimum="2.36",
    )

    assert release_artifact.sha256_file(second_archive) == first_digest
    extracted = release_artifact.extract_archive(archive, tmp_path / "extracted", COMMIT)
    manifest = release_artifact.validate_release_dir(
        extracted,
        COMMIT,
        expected_origin="github",
    )
    assert manifest["target"]["triple"] == "aarch64-unknown-linux-gnu"
    assert (extracted / "static/index.html").read_text(encoding="utf-8").startswith("<!doctype")


def test_manifest_detects_corruption_missing_files_and_wrong_commits(tmp_path: Path) -> None:
    release = _release(tmp_path / "release")
    (release / "lumistripe-web").write_text("modified\n", encoding="utf-8")
    with pytest.raises(release_artifact.VerificationError, match="checksum does not match"):
        release_artifact.validate_release_dir(release, COMMIT)

    release = _release(tmp_path / "other-release")
    with pytest.raises(release_artifact.VerificationError, match="does not match"):
        release_artifact.validate_release_dir(release, "b" * 40)

    (release / "manifest.json").unlink()
    with pytest.raises(release_artifact.VerificationError, match="missing"):
        release_artifact.validate_release_dir(release, COMMIT)

    release = _release(tmp_path / "missing-binary")
    (release / "lumistripe-engine").unlink()
    with pytest.raises(release_artifact.VerificationError, match="lumistripe-engine"):
        release_artifact.validate_release_dir(release, COMMIT)


@pytest.mark.parametrize(
    ("manifest_update", "message"),
    [
        ({"origin": "unknown"}, "origin is invalid"),
        ({"target": {"triple": "host", "architecture": "aarch64", "abi": "GNU/Linux"}}, "GitHub release target"),
        ({"target": {"triple": "aarch64-unknown-linux-gnu", "architecture": "aarch64", "abi": "unknown"}}, "target ABI"),
        ({"runtime_abi": {"distribution": "unknown", "glibc_minimum": "2.36"}}, "runtime distribution"),
        ({"toolchain": {"rust": "1.80.0", "bun": "1.4.4", "builder": "debian:trixie-slim"}}, "build toolchain"),
    ],
)
def test_manifest_rejects_untrusted_origin_and_runtime_claims(
    tmp_path: Path, manifest_update: dict[str, object], message: str
) -> None:
    release = _release(tmp_path / "release")
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(manifest_update)
    (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(release_artifact.VerificationError, match=message):
        release_artifact.validate_release_dir(release, COMMIT)


@pytest.mark.parametrize(
    "name",
    ["../outside", "/release/lumistripe-web", "release\\lumistripe-web", "release/./bad"],
)
def test_archive_rejects_unsafe_paths_and_cleans_partial_output(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        payload = b"bad"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    destination = tmp_path / "extract"
    with pytest.raises(release_artifact.VerificationError):
        release_artifact.extract_archive(archive, destination, COMMIT)
    assert not destination.exists()
    assert not (tmp_path / "outside").exists()


def test_archive_rejects_links_and_unlisted_entries(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("release/lumistripe-web")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp/attacker"
        tar.addfile(link)

    with pytest.raises(release_artifact.VerificationError, match="unsupported entry"):
        release_artifact.extract_archive(archive, tmp_path / "extract", COMMIT)


def test_legacy_frontend_migration_seals_the_existing_release(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "source-commit").write_text(f"{COMMIT}\n", encoding="ascii")
    for name in release_artifact.RELEASE_BINARIES:
        binary = release / name
        binary.write_text("binary fixture\n", encoding="utf-8")
        binary.chmod(0o755)
    legacy_static = tmp_path / "legacy-static"
    (legacy_static / "assets").mkdir(parents=True)
    (legacy_static / "index.html").write_text("legacy\n", encoding="utf-8")
    (legacy_static / "assets/app.js").write_text("legacy asset\n", encoding="utf-8")

    release_artifact.migrate_legacy_static(release, legacy_static, COMMIT)

    manifest = release_artifact.validate_release_dir(
        release,
        COMMIT,
        expected_origin="source",
    )
    assert manifest["origin"] == "source"
    assert (release / "static/assets/app.js").read_text(encoding="utf-8") == "legacy asset\n"


def test_prebuilt_target_supports_aarch64_trixie_and_ubuntu_2604(tmp_path: Path) -> None:
    target = {"ID": "raspbian", "VERSION_CODENAME": "trixie"}
    assert release_artifact.binary_target_supported(machine="aarch64", os_release=target)
    assert not release_artifact.binary_target_supported(machine="armv7l", os_release=target)
    assert not release_artifact.binary_target_supported(
        machine="aarch64",
        os_release={"ID": "debian", "VERSION_CODENAME": "bookworm"},
    )
    assert release_artifact.binary_target_supported(
        machine="aarch64",
        os_release={"ID": "ubuntu", "VERSION_CODENAME": "resolute"},
    )
    assert not release_artifact.binary_target_supported(
        machine="aarch64",
        os_release={"ID": "ubuntu", "VERSION_CODENAME": "noble"},
    )
    assert not release_artifact.binary_target_supported(
        machine="armv7l",
        os_release={"ID": "ubuntu", "VERSION_CODENAME": "resolute"},
    )


def test_release_assets_require_a_published_immutable_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        release_artifact,
        "_request_json",
        lambda _url: (
            200,
            {
                "tag_name": f"build-{COMMIT}",
                "draft": False,
                "prerelease": False,
                "immutable": False,
                "assets": [],
            },
        ),
    )

    with pytest.raises(release_artifact.VerificationError, match="published immutable release"):
        release_artifact._release_assets(COMMIT)


def test_ci_release_publisher_checks_github_immutability() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "publish-arm64-release:" in workflow
    assert "--json tagName,isDraft,isImmutable,assets" in workflow
    assert "isDraft == false and .isImmutable == true" in workflow


def test_ci_release_smoke_platform_checks_and_qualification_gate_precede_publication() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    publisher = workflow.split("  publish-arm64-release:", maxsplit=1)[1]
    required_steps = [
        "Build and smoke-test the ARM64 release in Debian Trixie",
        "Validate and smoke-test the packaged ARM64 release",
        "Verify the release on Ubuntu 26.04 ARM64",
        "Verify release provenance",
        "Generate and upload release qualification report",
        "Upload release qualification report",
        "Publish immutable GitHub release",
    ]
    positions = [publisher.index(step) for step in required_steps]
    assert positions == sorted(positions)
    assert "--target release-smoke" in publisher
    assert "--require-manifest" in publisher
    assert "needs: [deployment-tests, frontend, rust-engine, rust-engine-arm64]" in publisher
    assert publisher.index("gh release create") > positions[-1]

    smoke = Path(__file__).parents[1] / "release_smoke.py"
    smoke_source = smoke.read_text(encoding="utf-8")
    assert '"--network",\n        "none"' in smoke_source
    assert '"--simulate"' in smoke_source
    assert '"--spotify-backend",\n            "disabled"' in smoke_source
    assert '"LUMISTRIPE_ENABLE_SPOTIFY": "0"' not in smoke_source


def test_signed_release_is_verified_before_cache_and_release_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, archive = _bundle(tmp_path)
    archive_name = archive.name
    checksum_name = f"{archive_name}.sha256"
    bundle_name = f"{archive_name}.sigstore.json"
    manifest_name = f"{archive_name}.manifest.json"
    checksum = f"{release_artifact.sha256_file(archive)}  {archive_name}\n".encode("ascii")
    bundle = b'{"verificationMaterial": "fixture"}\n'
    manifest = (release / "manifest.json").read_bytes()
    contents = {
        archive_name: archive.read_bytes(),
        checksum_name: checksum,
        bundle_name: bundle,
        manifest_name: manifest,
    }
    assets = {
        name: _asset(name, payload)
        for name, payload in contents.items()
    }
    monkeypatch.setattr(
        release_artifact,
        "_release_assets",
        lambda commit: ({"id": 7}, assets),
    )

    def fake_download(
        asset: dict[str, object], name: str, destination: Path, *, max_size: int = release_artifact.ARCHIVE_LIMIT
    ) -> str:
        payload = contents[name]
        assert asset["digest"] == f"sha256:{hashlib.sha256(payload).hexdigest()}"
        assert len(payload) <= max_size
        destination.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    verified: list[tuple[Path, Path, str, str]] = []
    monkeypatch.setattr(release_artifact, "_download_asset", fake_download)
    monkeypatch.setattr(
        release_artifact,
        "_verify_attestation",
        lambda artifact, proof, commit, gh: verified.append((artifact, proof, commit, gh)),
    )
    real_extract = release_artifact.extract_archive

    def extract_only_after_attestation(archive_path: Path, destination: Path, commit: str) -> Path:
        assert len(verified) == 1, "unverified archive contents must not be extracted"
        return real_extract(archive_path, destination, commit)

    monkeypatch.setattr(release_artifact, "extract_archive", extract_only_after_attestation)
    monkeypatch.setattr(release_artifact, "_check_runtime_compatibility", lambda manifest: None)

    staged = release_artifact.install_release(
        COMMIT,
        tmp_path / "project/target/rust-deploy/releases",
        tmp_path / "cache",
        uid=os.getuid(),
        gid=os.getgid(),
        gh_path="/usr/bin/gh",
        require_supported_target=False,
    )

    assert staged.name == COMMIT
    assert release_artifact.validate_release_dir(staged, COMMIT, expected_origin="github")
    assert len(verified) == 1
    assert verified[0][2:] == (COMMIT, "/usr/bin/gh")
    assert (tmp_path / f"cache/{COMMIT}/verified.json").is_file()
    assert release_artifact.verify_cached_binary(
        tmp_path / f"cache/{COMMIT}",
        COMMIT,
        "lumistripe-deploy-helper",
        tmp_path / "extracted-helper",
    ) is None
    assert (tmp_path / "extracted-helper").stat().st_mode & 0o111

    monkeypatch.setattr(
        release_artifact,
        "_release_assets",
        lambda _commit: pytest.fail("a repeated cached install must not contact GitHub"),
    )
    repeated = release_artifact.install_release(
        COMMIT,
        tmp_path / "project/target/rust-deploy/releases",
        tmp_path / "cache",
        uid=os.getuid(),
        gid=os.getgid(),
        require_supported_target=False,
        cached_only=True,
    )
    assert repeated == staged


def test_cached_only_install_rejects_a_missing_verified_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        release_artifact,
        "_release_assets",
        lambda _commit: pytest.fail("cached-only installation must not contact GitHub"),
    )

    with pytest.raises(release_artifact.MissingArtifact, match="verified cached release"):
        release_artifact.install_release(
            COMMIT,
            tmp_path / "project/target/rust-deploy/releases",
            tmp_path / "cache",
            uid=os.getuid(),
            gid=os.getgid(),
            require_supported_target=False,
            cached_only=True,
        )


def test_checksum_mismatch_never_stages_or_caches_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, archive = _bundle(tmp_path)
    archive_name = archive.name
    names = [
        archive_name,
        f"{archive_name}.sha256",
        f"{archive_name}.sigstore.json",
        f"{archive_name}.manifest.json",
    ]
    contents = {
        names[0]: archive.read_bytes(),
        names[1]: f"{'0' * 64}  {archive_name}\n".encode("ascii"),
        names[2]: b"fixture attestation bundle",
        names[3]: (release / "manifest.json").read_bytes(),
    }
    assets = {name: _asset(name, payload) for name, payload in contents.items()}
    monkeypatch.setattr(release_artifact, "_release_assets", lambda _commit: ({"id": 7}, assets))

    def fake_download(
        asset: dict[str, object], name: str, destination: Path, *, max_size: int = release_artifact.ARCHIVE_LIMIT
    ) -> str:
        payload = contents[name]
        assert asset["digest"] == f"sha256:{hashlib.sha256(payload).hexdigest()}"
        assert len(payload) <= max_size
        destination.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(release_artifact, "_download_asset", fake_download)
    monkeypatch.setattr(
        release_artifact,
        "_verify_attestation",
        lambda *_args: pytest.fail("checksum mismatch must fail before provenance verification"),
    )
    release_root = tmp_path / "project/target/rust-deploy/releases"
    cache_root = tmp_path / "cache"

    with pytest.raises(release_artifact.VerificationError, match="sidecar does not match"):
        release_artifact.install_release(
            COMMIT,
            release_root,
            cache_root,
            uid=os.getuid(),
            gid=os.getgid(),
            require_supported_target=False,
        )

    assert list(release_root.iterdir()) == []
    assert list(cache_root.iterdir()) == []


def test_partial_download_is_removed_without_staging_or_caching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, archive = _bundle(tmp_path)
    archive_name = archive.name
    asset_names = [
        archive_name,
        f"{archive_name}.sha256",
        f"{archive_name}.sigstore.json",
        f"{archive_name}.manifest.json",
    ]
    assets = {name: _asset(name, b"published metadata") for name in asset_names}
    monkeypatch.setattr(release_artifact, "_release_assets", lambda _commit: ({"id": 7}, assets))

    def interrupted_download(
        _asset_metadata: dict[str, object], _name: str, destination: Path, *, max_size: int = release_artifact.ARCHIVE_LIMIT
    ) -> str:
        assert max_size == release_artifact.ARCHIVE_LIMIT
        destination.write_bytes(b"partial archive")
        raise release_artifact.ReleaseError("fixture connection ended during download")

    monkeypatch.setattr(release_artifact, "_download_asset", interrupted_download)
    release_root = tmp_path / "project/target/rust-deploy/releases"
    cache_root = tmp_path / "cache"

    with pytest.raises(release_artifact.ReleaseError, match="connection ended"):
        release_artifact.install_release(
            COMMIT,
            release_root,
            cache_root,
            uid=os.getuid(),
            gid=os.getgid(),
            require_supported_target=False,
        )

    assert list(release_root.iterdir()) == []
    assert list(cache_root.iterdir()) == []


def test_attestation_verification_pins_ci_workflow_and_source_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "release.tar.gz"
    bundle = tmp_path / "release.sigstore.json"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        if command == ["/usr/bin/gh", "--version"]:
            return SimpleNamespace(stdout="gh version 2.67.0 (2026-01-01)\n")
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="verified\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    release_artifact._verify_attestation(archive, bundle, COMMIT, "/usr/bin/gh")

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == ["/usr/bin/gh", "attestation", "verify"]
    assert command[command.index("--repo") + 1] == release_artifact.REPOSITORY
    assert command[command.index("--signer-workflow") + 1] == release_artifact.SIGNER_WORKFLOW
    assert command[command.index("--source-digest") + 1] == COMMIT
    assert "--deny-self-hosted-runners" in command
    assert command[command.index("--bundle") + 1] == str(bundle)
    assert kwargs["check"] is True


def test_attestation_failure_never_stages_or_caches_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, archive = _bundle(tmp_path)
    archive_name = archive.name
    names = [
        archive_name,
        f"{archive_name}.sha256",
        f"{archive_name}.sigstore.json",
        f"{archive_name}.manifest.json",
    ]
    contents = {
        archive_name: archive.read_bytes(),
        names[1]: f"{release_artifact.sha256_file(archive)}  {archive_name}\n".encode("ascii"),
        names[2]: b"bundle",
        names[3]: (release / "manifest.json").read_bytes(),
    }
    assets = {name: _asset(name, payload) for name, payload in contents.items()}
    monkeypatch.setattr(release_artifact, "_release_assets", lambda commit: ({"id": 7}, assets))

    def fake_download(
        asset: dict[str, object], name: str, destination: Path, *, max_size: int = release_artifact.ARCHIVE_LIMIT
    ) -> str:
        payload = contents[name]
        assert asset["digest"] == f"sha256:{hashlib.sha256(payload).hexdigest()}"
        assert len(payload) <= max_size
        destination.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(release_artifact, "_download_asset", fake_download)
    monkeypatch.setattr(
        release_artifact,
        "_verify_attestation",
        lambda *_args: (_ for _ in ()).throw(
            release_artifact.VerificationError("fixture provenance rejected")
        ),
    )
    monkeypatch.setattr(
        release_artifact,
        "extract_archive",
        lambda *_args: pytest.fail("archive contents must not be used before provenance passes"),
    )

    with pytest.raises(release_artifact.VerificationError, match="provenance rejected"):
        release_artifact.install_release(
            COMMIT,
            tmp_path / "project/target/rust-deploy/releases",
            tmp_path / "cache",
            uid=os.getuid(),
            gid=os.getgid(),
            require_supported_target=False,
        )

    assert not (tmp_path / f"project/target/rust-deploy/releases/{COMMIT}").exists()
    assert not (tmp_path / f"cache/{COMMIT}").exists()


def test_cached_release_requires_root_protected_files_and_matching_commit(tmp_path: Path) -> None:
    _, archive = _bundle(tmp_path)
    cache = tmp_path / "cache" / COMMIT
    cache.mkdir(parents=True)
    cache.chmod(0o700)
    (cache / "bundle.sigstore.json").write_bytes(b"bundle")
    (cache / "bundle.sha256").write_text(
        f"{release_artifact.sha256_file(archive)}  {archive.name}\n", encoding="ascii"
    )
    (cache / "bundle.tar.gz").write_bytes(archive.read_bytes())
    (cache / "verified.json").write_text(
        json.dumps(
            {
                "source_commit": COMMIT,
                "archive_sha256": release_artifact.sha256_file(archive),
                "verified": True,
            }
        ),
        encoding="utf-8",
    )
    for path in cache.iterdir():
        path.chmod(0o444)

    release_artifact._verify_cache_dir(cache, COMMIT)
    with pytest.raises(release_artifact.VerificationError, match="does not match"):
        release_artifact._verify_cache_dir(cache, "b" * 40)
    (cache / "bundle.tar.gz").chmod(0o644)
    (cache / "bundle.tar.gz").write_bytes(b"tampered")
    with pytest.raises(release_artifact.VerificationError, match="modified"):
        release_artifact._verify_cache_dir(cache, COMMIT)
