from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

DIAGNOSTICS_SCRIPT = Path(__file__).parents[1] / "soloist_diagnostics.py"
DIAGNOSTICS_SPEC = importlib.util.spec_from_file_location(
    "lumistripe_test_soloist_diagnostics", DIAGNOSTICS_SCRIPT
)
assert DIAGNOSTICS_SPEC is not None and DIAGNOSTICS_SPEC.loader is not None
DIAGNOSTICS = importlib.util.module_from_spec(DIAGNOSTICS_SPEC)
sys.modules[DIAGNOSTICS_SPEC.name] = DIAGNOSTICS
DIAGNOSTICS_SPEC.loader.exec_module(DIAGNOSTICS)

def _version_output(built_at: datetime, *, include_date: bool = True) -> str:
    epoch = int(built_at.timestamp())
    printed_date = f" ({built_at:%Y%m%d})" if include_date else ""
    return (
        f"soloist 1.3.9.4 build {epoch}{printed_date} "
        "(gtest) (linux/aarch64)\n"
    )


def _write_fake_soloist(
    path: Path, output: str, *, exit_status: int = 0
) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = --version ] || exit 2\n"
        "[ -z \"${LUMI_SPOTIFY_API_KEY:-}\" ] || exit 3\n"
        f"printf '%s' '{output}'\n"
        f"exit {exit_status}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_accepts_a_recent_soloist_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    built_at = now - timedelta(days=30)
    executable = tmp_path / "soloist"
    _write_fake_soloist(executable, _version_output(built_at))
    monkeypatch.setenv("LUMI_SPOTIFY_API_KEY", "test-key-must-not-be-inherited")

    build = DIAGNOSTICS.validate_soloist_binary(executable, now=now)

    assert build.built_at == built_at
    assert build.expires_at == built_at + timedelta(days=90)

    without_printed_date = tmp_path / "soloist-without-date"
    _write_fake_soloist(
        without_printed_date,
        _version_output(built_at, include_date=False),
    )
    assert DIAGNOSTICS.validate_soloist_binary(without_printed_date, now=now) == build


def test_rejects_a_soloist_build_at_its_expiry_time(tmp_path: Path) -> None:
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    executable = tmp_path / "soloist"
    _write_fake_soloist(executable, _version_output(now - timedelta(days=90)))

    with pytest.raises(DIAGNOSTICS.SoloistDiagnosticError, match="build expired"):
        DIAGNOSTICS.validate_soloist_binary(executable, now=now)


def test_rejects_missing_or_malformed_soloist_builds(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(DIAGNOSTICS.SoloistDiagnosticError, match="missing"):
        DIAGNOSTICS.validate_soloist_binary(missing)

    not_executable = tmp_path / "not-executable"
    not_executable.write_text("not a program", encoding="utf-8")
    with pytest.raises(DIAGNOSTICS.SoloistDiagnosticError, match="not executable"):
        DIAGNOSTICS.validate_soloist_binary(not_executable)

    malformed = tmp_path / "malformed"
    _write_fake_soloist(malformed, "soloist version without build metadata\n")
    with pytest.raises(DIAGNOSTICS.SoloistDiagnosticError, match="timestamp"):
        DIAGNOSTICS.validate_soloist_binary(malformed)


def test_rejects_version_command_failure_and_timeout(tmp_path: Path) -> None:
    failed = tmp_path / "failed"
    _write_fake_soloist(
        failed,
        _version_output(datetime(2026, 10, 1, tzinfo=UTC)),
        exit_status=1,
    )
    with pytest.raises(DIAGNOSTICS.SoloistDiagnosticError, match="exit status 1"):
        DIAGNOSTICS.validate_soloist_binary(failed)

    hanging = tmp_path / "hanging"
    hanging.write_text("#!/bin/sh\nexec /bin/sleep 1\n", encoding="utf-8")
    hanging.chmod(0o755)
    with pytest.raises(DIAGNOSTICS.SoloistDiagnosticError, match="did not finish"):
        DIAGNOSTICS.validate_soloist_binary(hanging, timeout_seconds=0.01)
