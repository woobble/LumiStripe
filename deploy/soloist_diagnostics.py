"""Validate the installed Spotify Soloist build before starting the service."""

from __future__ import annotations

import argparse
import math
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

BUILD_PATTERN = re.compile(r"\bbuild\s+(\d+)(?:\s+\((\d{8})\))?")
BUILD_LIFETIME = timedelta(days=90)
VERSION_TIMEOUT_SECONDS = 5
WARNING_WINDOW = timedelta(days=7)


class SoloistDiagnosticError(RuntimeError):
    """An installed Soloist executable cannot be used safely."""


@dataclass(frozen=True)
class SoloistBuild:
    built_at: datetime
    expires_at: datetime


def validate_soloist_binary(
    executable: Path,
    *,
    now: datetime | None = None,
    timeout_seconds: float = VERSION_TIMEOUT_SECONDS,
) -> SoloistBuild:
    """Read Soloist build metadata and reject missing, invalid, or expired builds."""
    try:
        executable_mode = executable.stat().st_mode
    except OSError as exc:
        raise SoloistDiagnosticError(
            f"Spotify Soloist is missing or unreadable at {executable}; "
            "run deploy/update.sh to install the official build."
        ) from exc
    if not stat.S_ISREG(executable_mode) or not executable_mode & 0o111:
        raise SoloistDiagnosticError(
            f"Spotify Soloist is missing or not executable at {executable}; "
            "run deploy/update.sh to install the official build."
        )

    try:
        result = subprocess.run(
            [str(executable), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout_seconds,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        raise SoloistDiagnosticError(
            "Spotify Soloist --version did not finish within "
            f"{timeout_seconds:g} seconds; run deploy/update.sh."
        ) from exc
    except OSError as exc:
        raise SoloistDiagnosticError(
            "Spotify Soloist could not be run for its version check; "
            "run deploy/update.sh."
        ) from exc

    if result.returncode != 0:
        raise SoloistDiagnosticError(
            "Spotify Soloist --version failed with exit status "
            f"{result.returncode}; run deploy/update.sh."
        )

    match = BUILD_PATTERN.search(result.stdout)
    if match is None:
        raise SoloistDiagnosticError(
            "Spotify Soloist --version did not include a recognized build "
            "timestamp; run deploy/update.sh."
        )

    try:
        epoch = int(match.group(1))
        built_at = datetime.fromtimestamp(epoch, tz=UTC)
        printed_date = match.group(2)
        printed_build_date = (
            datetime.strptime(printed_date, "%Y%m%d").date()
            if printed_date is not None
            else None
        )
    except (OverflowError, OSError, ValueError) as exc:
        raise SoloistDiagnosticError(
            "Spotify Soloist reported an invalid build timestamp; "
            "run deploy/update.sh."
        ) from exc

    if printed_build_date is not None and built_at.date() != printed_build_date:
        raise SoloistDiagnosticError(
            "Spotify Soloist reported inconsistent build timestamp fields; "
            "run deploy/update.sh."
        )

    expires_at = built_at + BUILD_LIFETIME
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current_time = current_time.astimezone(UTC)
    if current_time >= expires_at:
        raise SoloistDiagnosticError(
            "Spotify Soloist build expired on "
            f"{expires_at:%Y-%m-%d %H:%M UTC}; run deploy/update.sh "
            "to download the current official build."
        )

    return SoloistBuild(built_at=built_at, expires_at=expires_at)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    arguments = parser.parse_args()

    try:
        build = validate_soloist_binary(arguments.binary)
    except SoloistDiagnosticError as exc:
        print(f"Spotify Soloist check failed: {exc}", file=sys.stderr)
        return 1

    remaining = build.expires_at - datetime.now(UTC)
    remaining_days = max(1, math.ceil(remaining.total_seconds() / 86400))
    message = (
        f"Spotify Soloist build from {build.built_at:%Y-%m-%d} is valid "
        f"for {remaining_days} more day(s)."
    )
    if remaining <= WARNING_WINDOW:
        message += " Update it soon with deploy/update.sh."
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
