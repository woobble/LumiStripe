from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "stability_profile.py"
SPEC = importlib.util.spec_from_file_location("stability_profile", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
stability_profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stability_profile)


def test_failed_profile_writes_diagnostic_report_and_keeps_failure_status(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    binary = tmp_path / "lumistripe-web"
    output = tmp_path / "stability.json"
    report = {
        "result": "failed",
        "api_error_count": 1,
        "api_error_examples": ["/api/state: timed out"],
        "validation_errors": ["observed 1 API errors and 0 dashboard state errors"],
    }
    monkeypatch.setattr(stability_profile, "run_profile", lambda *_args: report)
    monkeypatch.setattr(
        "sys.argv",
        ["stability_profile.py", "--binary", str(binary), "--output", str(output)],
    )

    assert stability_profile.main() == 1

    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert "stability profile failed" in capsys.readouterr().err


def test_successful_profile_writes_report_and_returns_zero(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    binary = tmp_path / "lumistripe-web"
    output = tmp_path / "stability.json"
    report = {"result": "passed", "api_error_count": 0}
    monkeypatch.setattr(stability_profile, "run_profile", lambda *_args: report)
    monkeypatch.setattr(
        "sys.argv",
        ["stability_profile.py", "--binary", str(binary), "--output", str(output)],
    )

    assert stability_profile.main() == 0

    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert "stability profile failed" not in capsys.readouterr().err
