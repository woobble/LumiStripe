from __future__ import annotations

from pathlib import Path


def test_spotify_service_uses_the_version_matched_rust_preflight() -> None:
    service = (Path(__file__).parents[1] / "lumistripe-spotify.service").read_text(
        encoding="utf-8"
    )

    preflight = next(line for line in service.splitlines() if line.startswith("ExecStartPre="))
    assert "/usr/bin/env -u LUMI_SPOTIFY_API_KEY" in preflight
    assert "target/rust-deploy/current/lumistripe-deploy-helper" in preflight
    assert "soloist-check --binary" in preflight
    assert "python" not in preflight


def test_soloist_installer_uses_the_rust_preflight() -> None:
    installer = (Path(__file__).parents[1] / "soloist.sh").read_text(encoding="utf-8")

    assert "current/lumistripe-deploy-helper" in installer
    assert "soloist-download" in installer
    assert 'soloist-check --binary "$executable_file"' in installer
    assert "soloist_diagnostics.py" not in installer
    assert "/usr/bin/python3" not in installer
