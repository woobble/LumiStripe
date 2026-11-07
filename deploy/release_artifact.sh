#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
umask 077

REPOSITORY=woobble/LumiStripe
SIGNER_WORKFLOW=woobble/LumiStripe/.github/workflows/ci.yml
SNAPSHOT_DIR=/usr/local/libexec/lumistripe/deploy
SNAPSHOT_HELPER="$SNAPSHOT_DIR/lumistripe-deploy-helper"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
BOOTSTRAP_DIR=
SNAPSHOT_TMP=

fail() {
  printf 'release artifact: %s\n' "$1" >&2
  exit "${2:-1}"
}

safe_root_snapshot_directory() {
  local directory="$1" metadata owner mode
  [[ -d "$directory" && ! -L "$directory" ]] || return 1
  metadata="$(stat -c '%u:%a' -- "$directory" 2>/dev/null)" || return 1
  owner="${metadata%%:*}"
  mode="${metadata#*:}"
  [[ "$owner" == 0 && "$mode" =~ ^[0-7]+$ ]] && (( (8#$mode & 0022) == 0 ))
}

safe_helper() {
  local helper="$1" metadata owner mode parent
  [[ -f "$helper" && ! -L "$helper" && -x "$helper" ]] || return 1
  if (( EUID == 0 )); then
    metadata="$(stat -c '%u:%a' -- "$helper" 2>/dev/null)" || return 1
    owner="${metadata%%:*}"
    mode="${metadata#*:}"
    [[ "$owner" == 0 && "$mode" =~ ^[0-7]+$ ]] && (( (8#$mode & 0022) == 0 )) || return 1
    parent="$(dirname -- "$helper")"
    while [[ "$parent" != / ]]; do
      safe_root_snapshot_directory "$parent" || return 1
      parent="$(dirname -- "$parent")"
    done
    return 0
  else
    return 0
  fi
}

ensure_snapshot_directory() {
  local current=/ path_component
  local -a components=(usr local libexec lumistripe deploy)
  for path_component in "${components[@]}"; do
    current="$current$path_component"
    if [[ ! -e "$current" && ! -L "$current" ]]; then
      mkdir -m 755 -- "$current" || fail "could not create trusted helper directory $current"
      chown root:root -- "$current" || fail "could not protect trusted helper directory $current"
      chmod 755 -- "$current" || fail "could not protect trusted helper directory $current"
    fi
    safe_root_snapshot_directory "$current" || fail "trusted helper directory is missing or unsafe: $current"
    current="$current/"
  done
}

extract_commit_argument() {
  local previous=0 argument
  for argument in "$@"; do
    if (( previous )); then
      printf '%s\n' "$argument"
      return 0
    fi
    case "$argument" in
      --commit) previous=1 ;;
      --commit=*) printf '%s\n' "${argument#*=}"; return 0 ;;
    esac
  done
  return 1
}

has_helper_destination() {
  local previous=0 argument
  for argument in "$@"; do
    if (( previous )); then return 0; fi
    case "$argument" in
      --helper-destination) previous=1 ;;
      --helper-destination=*) return 0 ;;
    esac
  done
  return 1
}

has_cached_only() {
  local argument
  for argument in "$@"; do
    [[ "$argument" == --cached-only ]] && return 0
  done
  return 1
}

cleanup() {
  if [[ -n "$SNAPSHOT_TMP" ]]; then
    rm -f -- "$SNAPSHOT_TMP"
    SNAPSHOT_TMP=
  fi
  if [[ -n "$BOOTSTRAP_DIR" && -d "$BOOTSTRAP_DIR" ]]; then
    rm -rf -- "$BOOTSTRAP_DIR"
  fi
}

if (($# == 1)) && [[ "$1" == --help || "$1" == -h ]]; then
  cat <<'EOF'
Usage: release_artifact.sh SUBCOMMAND [OPTIONS]

Subcommands: package, seal-local, migrate-legacy-static, validate, install,
extract-binary. Security-sensitive release operations run in the Rust
lumistripe-deploy-helper. First binary install verifies a commit-matched helper
attestation with GitHub CLI before executing the downloaded helper.
EOF
  exit 0
fi

bootstrap_helper() {
  local commit="$1" tag asset bundle release_json candidate candidate_bundle expected_digest digest response_status
  [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || fail 'install requires a full lowercase Git commit before bootstrapping the helper' 12
  [[ "$(uname -m)" == aarch64 ]] || fail 'prebuilt release helpers require an AArch64 host' 11
  command -v jq >/dev/null 2>&1 || fail 'jq is required to verify release metadata'
  command -v sha256sum >/dev/null 2>&1 || fail 'sha256sum is required to verify the bootstrap asset'
  command -v file >/dev/null 2>&1 || fail 'file is required to inspect the bootstrap executable'
  command -v /usr/bin/curl >/dev/null 2>&1 || fail 'curl is required to retrieve immutable release metadata'
  command -v /usr/bin/gh >/dev/null 2>&1 || fail 'GitHub CLI is required to verify the bootstrap helper attestation'
  ensure_snapshot_directory
  BOOTSTRAP_DIR="$(mktemp -d /tmp/lumistripe-release-bootstrap.XXXXXX)" || fail 'could not create private bootstrap directory'
  chmod 700 -- "$BOOTSTRAP_DIR"
  tag="build-$commit"
  asset=lumistripe-deploy-helper-linux-aarch64
  bundle="$asset.sigstore.json"
  release_json="$BOOTSTRAP_DIR/release.json"
  response_status="$(/usr/bin/curl --disable --silent --show-error --location \
    --proto '=https' --proto-redir '=https' --max-time 30 --max-filesize 2097152 \
    --header 'Accept: application/vnd.github+json' \
    --header 'X-GitHub-Api-Version: 2022-11-28' \
    --output "$release_json" --write-out '%{http_code}' \
    "https://api.github.com/repos/$REPOSITORY/releases/tags/$tag")" || \
    fail "could not inspect immutable release $tag for the trusted bootstrap helper"
  case "$response_status" in
    200) ;;
    404|410) fail "no prebuilt release is published for commit $commit" 10 ;;
    *) fail "GitHub returned HTTP $response_status while inspecting release $tag" ;;
  esac
  jq -e --arg tag "$tag" --arg asset "$asset" --arg bundle "$bundle" \
    --arg repository "$REPOSITORY" '
    .tag_name == $tag and .draft == false and .prerelease == false and .immutable == true and
    ([.assets[] | select(.name == $asset)] | length) == 1 and
    ([.assets[] | select(.name == $bundle)] | length) == 1 and
    ([.assets[] | select(.name == $asset)][0] |
      (.size | type == "number" and . > 0 and . <= 134217728)) and
    ([.assets[] | select(.name == $bundle)][0] |
      (.size | type == "number" and . > 0 and . <= 2097152)) and
    ([.assets[] | select(.name == $asset)][0].digest |
      type == "string" and test("^sha256:[0-9a-f]{64}$")) and
    ([.assets[] | select(.name == $asset)][0].browser_download_url ==
      ("https://github.com/" + $repository + "/releases/download/" + $tag + "/" + $asset)) and
    ([.assets[] | select(.name == $bundle)][0].browser_download_url ==
      ("https://github.com/" + $repository + "/releases/download/" + $tag + "/" + $bundle))
  ' "$release_json" >/dev/null || fail "release $tag has no unique immutable bootstrap helper and attestation bundle" 12
  expected_digest="$(jq -er --arg asset "$asset" '.assets[] | select(.name == $asset) | .digest' "$release_json")"
  /usr/bin/curl --disable --fail --silent --show-error --location \
    --proto '=https' --proto-redir '=https' --max-time 120 --max-filesize 134217728 \
    "https://github.com/$REPOSITORY/releases/download/$tag/$asset" -o "$BOOTSTRAP_DIR/$asset" || \
    fail "could not download the bootstrap helper for $commit"
  /usr/bin/curl --disable --fail --silent --show-error --location \
    --proto '=https' --proto-redir '=https' --max-time 30 --max-filesize 2097152 \
    "https://github.com/$REPOSITORY/releases/download/$tag/$bundle" -o "$BOOTSTRAP_DIR/$bundle" || \
    fail "could not download the bootstrap helper for $commit"
  candidate="$BOOTSTRAP_DIR/$asset"
  candidate_bundle="$BOOTSTRAP_DIR/$bundle"
  [[ -f "$candidate" && ! -L "$candidate" && -s "$candidate" &&
     -f "$candidate_bundle" && ! -L "$candidate_bundle" && -s "$candidate_bundle" ]] || \
    fail 'the downloaded bootstrap helper or attestation bundle is missing' 12
  [[ "$(stat -c '%s' -- "$candidate")" =~ ^[0-9]+$ ]] &&
    (( $(stat -c '%s' -- "$candidate") <= 134217728 )) || fail 'bootstrap helper exceeds the executable size limit' 12
  [[ "$(stat -c '%s' -- "$candidate_bundle")" =~ ^[0-9]+$ ]] &&
    (( $(stat -c '%s' -- "$candidate_bundle") <= 2097152 )) || fail 'bootstrap attestation bundle exceeds the size limit' 12
  /usr/bin/gh attestation verify "$candidate" \
    --repo "$REPOSITORY" \
    --signer-workflow "$SIGNER_WORKFLOW" \
    --source-digest "$commit" \
    --deny-self-hosted-runners \
    --bundle "$candidate_bundle" || fail 'bootstrap helper provenance verification failed' 12
  local file_description
  file_description="$(file -- "$candidate")" || fail 'could not inspect bootstrap helper executable' 12
  [[ "$file_description" == *'ELF 64-bit'* && "$file_description" == *'ARM aarch64'* ]] || \
    fail 'attested bootstrap helper is not an AArch64 ELF executable' 12
  digest="$(sha256sum -- "$candidate")" || fail 'could not hash bootstrap helper'
  digest="sha256:${digest%% *}"
  [[ "$digest" == "$expected_digest" ]] || fail 'bootstrap helper digest does not match immutable release metadata' 12
  SNAPSHOT_TMP="$(mktemp "$SNAPSHOT_DIR/.lumistripe-deploy-helper.XXXXXX")" || fail 'could not create trusted helper staging file'
  install -o root -g root -m 755 -- "$candidate" "$SNAPSHOT_TMP" || fail 'could not stage trusted bootstrap helper'
  mv -fT -- "$SNAPSHOT_TMP" "$SNAPSHOT_HELPER" || fail 'could not atomically install trusted bootstrap helper'
  SNAPSHOT_TMP=
  sync -f "$SNAPSHOT_DIR" || fail 'could not sync trusted deployment snapshot'
  helper="$SNAPSHOT_HELPER"
}

(($# > 0)) || fail 'expected a release_artifact subcommand'
subcommand="$1"
shift
if [[ "${LUMI_RELEASE_ARTIFACT_PYTHON_COMPAT:-0}" == 1 ]]; then
  [[ -f "$SCRIPT_DIR/release_artifact.py" && ! -L "$SCRIPT_DIR/release_artifact.py" ]] || \
    fail 'Python compatibility verifier was requested but its source file is missing' 12
  command -v python3 >/dev/null 2>&1 || fail 'Python compatibility verifier was requested but python3 is unavailable' 12
  exec python3 "$SCRIPT_DIR/release_artifact.py" "$subcommand" "$@"
fi
helper="${LUMI_RELEASE_HELPER:-}"
if [[ -n "$helper" ]] && safe_helper "$helper"; then
  :
elif safe_helper "$SNAPSHOT_HELPER"; then
  helper="$SNAPSHOT_HELPER"
else
  helper=
  for candidate in \
    "$PROJECT_DIR/target/rust-deploy/current/lumistripe-deploy-helper" \
    "$PROJECT_DIR/target/rust-deploy/build/release/lumistripe-deploy-helper" \
    "$PROJECT_DIR/target/release/lumistripe-deploy-helper" \
    "$SCRIPT_DIR/lumistripe-deploy-helper"; do
    if [[ -f "$candidate" && ! -L "$candidate" && -x "$candidate" ]]; then
      if (( EUID != 0 )) || safe_helper "$candidate"; then
        helper="$candidate"
        break
      fi
    fi
  done
fi

if [[ -z "$helper" && "$subcommand" == install ]] && has_cached_only "$@"; then
  fail 'offline cached installation requires an already installed trusted Rust deployment helper' 12
fi

if [[ -z "$helper" && "$subcommand" == install ]]; then
  commit="$(extract_commit_argument "$@")" || fail 'install requires --commit'
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  bootstrap_helper "$commit"
  [[ -n "$helper" ]] || fail 'trusted bootstrap helper is unavailable' 12
fi

if [[ -z "$helper" ]]; then
  fail 'trusted Rust deployment helper is unavailable; use a release with an attested bootstrap helper or the documented Python compatibility path' 12
fi

if (( EUID == 0 )) && [[ "$subcommand" == install ]] && \
  ! has_helper_destination "$@" && ! has_cached_only "$@"; then
  "$helper" release-artifact install "$@" --helper-destination "$SNAPSHOT_HELPER"
  exit "$?"
fi
if [[ -n "$BOOTSTRAP_DIR" ]]; then
  "$helper" release-artifact "$subcommand" "$@"
  exit "$?"
fi
exec "$helper" release-artifact "$subcommand" "$@"
