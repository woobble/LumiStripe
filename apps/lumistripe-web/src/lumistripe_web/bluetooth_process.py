"""Process-environment and terminal-output helpers for platform commands."""

from __future__ import annotations

import os
import re

_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def pipewire_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in environment:
        candidate = f"/run/user/{os.getuid()}"
        if os.path.isdir(candidate):
            environment["XDG_RUNTIME_DIR"] = candidate
    return environment


def clean_command_output(output: str) -> str:
    """Remove terminal formatting emitted by bluetoothctl."""
    prompt = re.compile(r"^\[bluetoothctl\][>#]\s*")
    lines: list[str] = []
    for raw_line in _ANSI_ESCAPE.sub("", output).replace("\r", "").splitlines():
        line = prompt.sub("", raw_line.strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


__all__ = ["clean_command_output", "pipewire_environment"]
