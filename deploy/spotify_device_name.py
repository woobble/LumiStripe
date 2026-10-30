"""Safely update the Soloist Connect name and restart only its service."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENV_FILE = Path("/etc/lumistripe/lumistripe-spotify.env")
SERVICE = "lumistripe-spotify.service"


def main() -> int:
    if len(sys.argv) != 2:
        print("expected one Spotify player name", file=sys.stderr)
        return 2
    name = sys.argv[1].strip()
    if (
        not name
        or len(name) > 64
        or any(not char.isprintable() for char in name)
    ):
        print("Spotify player name must be 1 to 64 printable characters", file=sys.stderr)
        return 2
    if not ENV_FILE.is_file():
        print("Spotify environment file is not installed", file=sys.stderr)
        return 1

    original = ENV_FILE.read_text(encoding="utf-8")
    encoded_name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = original.splitlines()
    replaced = False
    result: list[str] = []
    for line in lines:
        if line.startswith("LUMI_SPOTIFY_DEVICE_NAME="):
            if not replaced:
                result.append(f"LUMI_SPOTIFY_DEVICE_NAME={encoded_name}")
                replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(f"LUMI_SPOTIFY_DEVICE_NAME={encoded_name}")
    updated = "\n".join(result) + "\n"

    metadata = ENV_FILE.stat()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".spotify-env-", dir=ENV_FILE.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.chmod(temporary, metadata.st_mode & 0o777)
        os.replace(temporary, ENV_FILE)
    finally:
        temporary.unlink(missing_ok=True)

    restarted = subprocess.run(
        ["/usr/bin/systemctl", "restart", "--no-block", SERVICE],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if restarted.returncode:
        print(restarted.stderr.strip() or "Spotify service restart failed", file=sys.stderr)
        return restarted.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
