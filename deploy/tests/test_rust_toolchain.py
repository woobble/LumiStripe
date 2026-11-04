from __future__ import annotations

import os
import subprocess
from pathlib import Path

TOOLCHAIN_HELPER = Path(__file__).parents[1] / "rust-toolchain.sh"


def test_installer_bootstraps_and_verifies_pinned_rust_toolchain(
    tmp_path: Path,
) -> None:
    service_home = tmp_path / "service-home"
    rustup_bin = service_home / ".cargo" / "bin" / "rustup"
    rustup_bin.parent.mkdir(parents=True)
    rustup_log = tmp_path / "rustup.log"
    bootstrap_log = tmp_path / "bootstrap.log"
    environment = os.environ.copy()
    environment.update(
        {
            "SERVICE_HOME": str(service_home),
            "SERVICE_USER": "lumistripe",
            "RUSTUP_LOG": str(rustup_log),
            "BOOTSTRAP_LOG": str(bootstrap_log),
        }
    )
    script = r"""
set -eu
source "$1"
write_fake_rustup() {
  cat > "$SERVICE_HOME/.cargo/bin/rustup" <<'RUSTUP'
#!/bin/sh
printf '%s\n' "$*" >> "$RUSTUP_LOG"
if [ "$1" = run ]; then
  printf 'rustc 1.88.0 (test build)\n'
fi
RUSTUP
  chmod 755 "$SERVICE_HOME/.cargo/bin/rustup"
}
as_user() { "$@"; }
download_and_run_as_user() {
  printf '%s\n' "$*" >> "$BOOTSTRAP_LOG"
  write_fake_rustup
}
install_rust_toolchain
install_rust_toolchain
"""
    result = subprocess.run(
        ["bash", "-c", script, "install-rust-toolchain", str(TOOLCHAIN_HELPER)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert bootstrap_log.read_text(encoding="utf-8").splitlines() == [
        "https://sh.rustup.rs /bin/sh -y --default-toolchain 1.88.0 --profile minimal"
    ]
    assert rustup_log.read_text(encoding="utf-8").splitlines() == [
        "toolchain install 1.88.0 --profile minimal",
        "run 1.88.0 rustc --version",
        "toolchain install 1.88.0 --profile minimal",
        "run 1.88.0 rustc --version",
    ]
