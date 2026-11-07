from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
DEPLOY = ROOT / "deploy"

# This checked-in Python entry point is retained only for the explicitly
# selected verifier recovery path. New files outside deploy/tests must be added
# here with a reason.
LEGACY_PYTHON_ALLOWLIST = {
    "deploy/release_artifact.py": "explicit LUMI_RELEASE_ARTIFACT_PYTHON_COMPAT recovery path",
}

DEVELOPMENT_ONLY_PATHS = ("deploy/tests/",)
PRODUCTION_TEXT_SUFFIXES = {
    ".conf",
    ".service",
    ".sh",
    ".timer",
    ".yml",
}
PRODUCTION_TEXT_NAMES = {
    "Dockerfile",
    "lumistripe-admin",
    "lumistripe-network",
    "lumistripe-update-trigger",
}
WORKFLOW_PATH = ".github/workflows/ci.yml"

PYTHON_SHEBANG = re.compile(r"^#!.*\bpython(?:3(?:\.\d+)?)?\b", re.IGNORECASE)
PYTHON_COMMAND = re.compile(r"(?<![\w./-])python(?:3(?:\.\d+)?)?(?=\s|$)")
PYTHON_HEREDOC = re.compile(
    r"(?<![\w./-])python(?:3(?:\.\d+)?)?[^\n]*<<-?\s*['\"]?[A-Za-z_][A-Za-z0-9_]*"
)
RETIRED_RUNTIME_REFERENCE = re.compile(
    r"\b(?:release_smoke|release_qualification|stability_profile)\.py\b"
)
LEGACY_VERIFIER_REFERENCE = re.compile(r"\brelease_artifact\.py\b")

# Each exception is a complete line (or a unique message fragment) so a new
# command cannot inherit an allowlist entry by merely sharing a file path.
ALLOWED_PYTHON_LINES = {
    "deploy/release_artifact.sh": {
        '[[ -f "$SCRIPT_DIR/release_artifact.py" && ! -L "$SCRIPT_DIR/release_artifact.py" ]] ||',
        "fail 'Python compatibility verifier was requested but its source file is missing' 12",
        "command -v python3 >/dev/null 2>&1 || fail 'Python compatibility verifier was requested but python3 is unavailable' 12",
        'exec python3 "$SCRIPT_DIR/release_artifact.py" "$subcommand" "$@"',
    },
    "deploy/update-support.sh": {
        "build-rust-backend.sh release_artifact.sh release_artifact.py install-github-cli.sh rust-toolchain.sh",
        "install.sh|update.sh|uninstall.sh|lumistripe-admin|spotify-provider.sh|build-rust-backend.sh|release_artifact.sh|release_artifact.py|install-github-cli.sh|lumistripe-network|lumistripe-update-trigger) mode=755 ;;",
        "LUMI_WEB_BACKEND=python is no longer supported. LumiStripe now runs the native Rust web application.",
    },
    WORKFLOW_PATH: {
        "run: uv run --no-project --with pytest python -m pytest -q deploy/tests",
    },
}


def _production_text_files() -> list[Path]:
    files = [
        path
        for path in DEPLOY.rglob("*")
        if path.is_file()
        and not _is_development_only_path(path)
        and (path.suffix in PRODUCTION_TEXT_SUFFIXES or path.name in PRODUCTION_TEXT_NAMES)
    ]
    workflow = ROOT / WORKFLOW_PATH
    if workflow.is_file():
        files.append(workflow)
    return sorted(files)


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_development_only_path(path: Path) -> bool:
    relative = _relative(path)
    return any(relative.startswith(prefix) for prefix in DEVELOPMENT_ONLY_PATHS)


def test_python_runtime_usage_is_limited_to_documented_compatibility_and_ci() -> None:
    production_files = _production_text_files()
    findings: list[str] = []

    for path in production_files:
        relative = _relative(path)
        allowed_lines = ALLOWED_PYTHON_LINES.get(relative, set())
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            normalized_line = line.strip().rstrip("\\").rstrip()
            if relative in LEGACY_PYTHON_ALLOWLIST:
                findings.append(f"legacy Python file is treated as production source: {relative}")
                break
            if normalized_line in allowed_lines:
                continue
            if PYTHON_SHEBANG.search(line):
                findings.append(f"{relative}:{line_number}: Python interpreter shebang")
            if PYTHON_COMMAND.search(line):
                findings.append(f"{relative}:{line_number}: Python command")
            if PYTHON_HEREDOC.search(line):
                findings.append(f"{relative}:{line_number}: Python heredoc")
            if RETIRED_RUNTIME_REFERENCE.search(line):
                findings.append(f"{relative}:{line_number}: reference to retired Python runtime script")
            if LEGACY_VERIFIER_REFERENCE.search(line) and normalized_line not in allowed_lines:
                findings.append(f"{relative}:{line_number}: unapproved legacy verifier reference")

    python_files = {
        path.relative_to(ROOT).as_posix()
        for path in DEPLOY.rglob("*.py")
        if not _is_development_only_path(path)
    }
    unexpected_python_files = python_files - set(LEGACY_PYTHON_ALLOWLIST)
    missing_legacy_files = set(LEGACY_PYTHON_ALLOWLIST) - python_files
    findings.extend(f"unapproved deployment Python file: {path}" for path in sorted(unexpected_python_files))
    findings.extend(f"allowlisted migration reference is missing: {path}" for path in sorted(missing_legacy_files))

    wrapper = (DEPLOY / "release_artifact.sh").read_text(encoding="utf-8")
    if 'if [[ "${LUMI_RELEASE_ARTIFACT_PYTHON_COMPAT:-0}" == 1 ]]; then' not in wrapper:
        findings.append("legacy release verifier is not guarded by its explicit compatibility flag")
    assert not findings, "\n".join(findings)


def test_test_fixtures_are_the_only_broad_development_python_allowlist() -> None:
    python_files = {
        _relative(path)
        for path in DEPLOY.rglob("*.py")
        if _is_development_only_path(path)
    }
    assert python_files
    assert all(path.startswith("deploy/tests/") for path in python_files)


def test_audit_patterns_recognize_python_runtime_regressions() -> None:
    assert PYTHON_SHEBANG.search("#!/usr/bin/python3")
    assert PYTHON_COMMAND.search("python3 -m release_tool")
    assert PYTHON_HEREDOC.search("python3 - <<'PY'")
    assert RETIRED_RUNTIME_REFERENCE.search("deploy/release_smoke.py")
