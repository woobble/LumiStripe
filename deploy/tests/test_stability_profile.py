from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).parents[1]
SCRIPT = DEPLOY / "stability_profile.sh"
DRIVER_SOURCE = DEPLOY.parent / "apps/lumistripe-web/src/bin/stability_driver.rs"


def test_failed_profile_writes_machine_readable_diagnostic_report(tmp_path: Path) -> None:
    output = tmp_path / "stability.json"
    environment = os.environ.copy()
    environment["LUMI_STABILITY_DRIVER"] = str(tmp_path / "missing-driver")

    result = subprocess.run(
        ["bash", str(SCRIPT), "--binary", "/bin/true", "--duration-seconds", "30", "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 1
    assert "Rust stability protocol driver is missing" in result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["result"] == "failed"
    assert report["validation_errors"] == [
        f"Rust stability protocol driver is missing: {tmp_path / 'missing-driver'}"
    ]
    assert report["task_termination"] == "failed"
    assert not list(tmp_path.glob(".stability.json.*"))


def test_duration_is_bounded_before_any_process_starts(tmp_path: Path) -> None:
    output = tmp_path / "stability.json"
    result = subprocess.run(
        ["bash", str(SCRIPT), "--binary", "/bin/true", "--duration-seconds", "29", "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "between 30 and 1800" in result.stderr
    assert not output.exists()


def test_bash_orchestrates_http_while_rust_owns_websocket_protocols() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    driver = DRIVER_SOURCE.read_text(encoding="utf-8")

    assert "curl --disable" in script
    assert "setsid \"$binary\"" in script
    assert "proc_sample" in script
    assert "python3" not in script
    assert "struct _AudioSocket" not in script
    assert "MockSpotify" in driver
    assert "AudioTelemetry" in driver
    assert "tokio_tungstenite" in driver
    assert "set -Eeuo pipefail" in script
