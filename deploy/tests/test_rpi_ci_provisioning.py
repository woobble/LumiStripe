from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROVISIONER = Path(__file__).parents[2] / "scripts/provision_rpi_ci_native_deps.sh"


def _fake_command(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _base_environment(tmp_path: Path, bin_dir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "RUNNER_TEMP": str(tmp_path / "runner-temp"),
            "SSL_CERT_FILE": str(tmp_path / "missing-ca-bundle"),
            "APT_CALL_LOG": str(tmp_path / "apt-calls.log"),
        }
    )
    return environment


def _install_fakes(bin_dir: Path) -> None:
    bin_dir.mkdir()
    for name in ("cc", "make", "curl", "pactl"):
        _fake_command(bin_dir, name, "exit 0")
    _fake_command(
        bin_dir,
        "pkg-config",
        """
if [[ "${1:-}" == --exists ]]; then
  exit 0
fi
if [[ "${1:-}" == --cflags ]]; then
  printf '%s\\n' '-I/usr/include/alsa -I/usr/include/pipewire-0.3'
fi
""",
    )
    _fake_command(
        bin_dir,
        "apt-get",
        """
printf '%s\\n' "$*" >> "$APT_CALL_LOG"
if [[ " $* " == *' update '* ]]; then
  exit 0
fi
if [[ " $* " == *' install '* ]]; then
  archives="$RUNNER_TEMP/lumistripe-rpi-native-deps/apt/archives"
  mkdir -p "$archives"
  : > "$archives/libasound2-dev.deb"
  : > "$archives/libpipewire-0.3-dev.deb"
  exit 0
fi
echo "unexpected apt-get invocation: $*" >&2
exit 2
""",
    )
    _fake_command(
        bin_dir,
        "sudo",
        """
if [[ "${1:-}" == -n && "${2:-}" == true ]]; then
  exit 1
fi
echo "unexpected sudo invocation: $*" >&2
exit 2
""",
    )
    _fake_command(
        bin_dir,
        "dpkg-deb",
        """
[[ "${1:-}" == --extract && $# -eq 3 ]]
destination="$3"
mkdir -p "$destination/usr/include/alsa" \
  "$destination/usr/lib/pkgconfig" "$destination/etc/ssl/certs"
: > "$destination/usr/include/alsa/asoundlib.h"
: > "$destination/usr/lib/pkgconfig/native-test.pc"
printf 'test CA bundle\\n' > "$destination/etc/ssl/certs/ca-certificates.crt"
""",
    )


def _run_provisioner(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(PROVISIONER)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_provisioner_skips_apt_when_native_dependencies_are_available(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    _install_fakes(bin_dir)
    root = tmp_path / "native-root"
    alsa_header = root / "usr/include/alsa/asoundlib.h"
    alsa_header.parent.mkdir(parents=True)
    alsa_header.touch()
    ca_bundle = tmp_path / "system-ca.crt"
    ca_bundle.write_text("test CA bundle\n", encoding="utf-8")
    environment = _base_environment(tmp_path, bin_dir)
    environment.update(
        {
            "LUMI_RPI_NATIVE_DEPS_ROOT": str(root),
            "SSL_CERT_FILE": str(ca_bundle),
        }
    )

    result = _run_provisioner(environment)

    assert result.returncode == 0, result.stderr
    assert "already available" in result.stdout
    assert not Path(environment["APT_CALL_LOG"]).exists()


def test_provisioner_builds_a_temporary_native_package_overlay(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _install_fakes(bin_dir)
    environment = _base_environment(tmp_path, bin_dir)
    github_env = tmp_path / "github-env"
    environment["GITHUB_ENV"] = str(github_env)

    result = _run_provisioner(environment)

    assert result.returncode == 0, result.stderr
    assert "no system package changes" in result.stdout
    assert "Temporary Raspberry Pi native package environment is ready" in result.stdout

    apt_calls = Path(environment["APT_CALL_LOG"]).read_text(encoding="utf-8").splitlines()
    assert len(apt_calls) == 2
    assert apt_calls[0].endswith("update")
    assert "--download-only" in apt_calls[1]
    assert "libasound2-dev" in apt_calls[1]

    exported = github_env.read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in exported)
    root = Path(values["LUMI_RPI_NATIVE_DEPS_ROOT"])
    assert (root / "usr/include/alsa/asoundlib.h").is_file()
    assert Path(values["SSL_CERT_FILE"]).is_file()
    assert Path(values["PKG_CONFIG"]).is_file()

    wrapper_environment = environment.copy()
    wrapper_environment.update(values)
    flags = subprocess.run(
        [values["PKG_CONFIG"], "--cflags", "alsa"],
        env=wrapper_environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert f"-I{root}/usr/include/alsa" in flags.stdout.split()
    assert "-I/usr/include/alsa" in flags.stdout.split()
