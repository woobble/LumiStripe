#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Install the GitHub CLI provenance verifier as root." >&2
  exit 1
fi

KEYRING_SHA256=6084d5d7bd8e288441e0e94fc6275570895da18e6751f70f057485dc2d1a811b
KEYRING_PATH=/etc/apt/keyrings/lumistripe-github-cli.gpg
SOURCE_PATH=/etc/apt/sources.list.d/lumistripe-github-cli.list

gh_version() {
  /usr/bin/gh --version 2>/dev/null | awk 'NR == 1 { split($3, version, "."); print version[1] "." version[2] }'
}

is_supported_version() {
  local version="$(gh_version)"
  [[ "$version" =~ ^[0-9]+\.[0-9]+$ ]] || return 1
  local major="${version%%.*}"
  local minor="${version#*.}"
  (( major > 2 || (major == 2 && minor >= 67) ))
}

if command -v /usr/bin/gh >/dev/null 2>&1 && is_supported_version; then
  exit 0
fi

command -v curl >/dev/null 2>&1 || {
  echo "curl is required to install the GitHub CLI provenance verifier." >&2
  exit 1
}
command -v dpkg >/dev/null 2>&1 || {
  echo "dpkg is required to install the GitHub CLI provenance verifier." >&2
  exit 1
}

architecture="$(dpkg --print-architecture)"
case "$architecture" in
  arm64|amd64|armhf|armel|i386|ppc64el|s390x|riscv64) ;;
  *) echo "GitHub CLI packages are unavailable for $architecture." >&2; exit 1 ;;
esac

temporary="$(mktemp /tmp/lumistripe-github-cli-keyring.XXXXXX)"
trap 'rm -f -- "$temporary"' EXIT
curl --disable --proto '=https' --proto-redir '=https' \
  --fail --silent --show-error --location --max-time 30 \
  https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  -o "$temporary"
printf '%s  %s\n' "$KEYRING_SHA256" "$temporary" | sha256sum --check --status || {
  echo "The GitHub CLI signing keyring checksum did not match the pinned official key." >&2
  exit 1
}

install -d -o root -g root -m 755 /etc/apt/keyrings /etc/apt/sources.list.d
install -o root -g root -m 644 "$temporary" "$KEYRING_PATH"
source_temporary="$(mktemp /etc/apt/sources.list.d/.lumistripe-github-cli.XXXXXX)"
printf 'deb [arch=%s signed-by=%s] https://cli.github.com/packages stable main\n' \
  "$architecture" "$KEYRING_PATH" > "$source_temporary"
chmod 644 "$source_temporary"
mv -fT -- "$source_temporary" "$SOURCE_PATH"

if [[ "${LUMI_APT_LISTS_UPDATED:-0}" != 1 ]]; then
  apt-get update
fi
DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends gh
is_supported_version || {
  echo "GitHub CLI 2.67 or newer is required for secure provenance verification." >&2
  exit 1
}
