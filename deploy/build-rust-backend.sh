#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Build the Rust backend as the LumiStripe service user, not root." >&2
  exit 1
fi

PROJECT_DIR="${1:-}"
if [[ -z "$PROJECT_DIR" ]] || ! PROJECT_DIR="$(cd -- "$PROJECT_DIR" 2>/dev/null && pwd -P)"; then
  echo "Usage: $0 PROJECT_DIR" >&2
  exit 2
fi
[[ -f "$PROJECT_DIR/Cargo.toml" ]] || {
  echo "Rust workspace not found under $PROJECT_DIR." >&2
  exit 1
}
WORKTREE_STATUS="$(git -C "$PROJECT_DIR" status --porcelain --untracked-files=all)" || {
  echo "Could not inspect the Rust backend source checkout." >&2
  exit 1
}
[[ -z "$WORKTREE_STATUS" ]] || {
  echo "Rust backend builds require a clean checkout; refusing to label modified sources with a commit ID." >&2
  exit 1
}

CARGO_HOME_DIR="${CARGO_HOME:-$HOME/.cargo}"
RUSTUP_HOME_DIR="${RUSTUP_HOME:-$HOME/.rustup}"
CARGO_BIN="${LUMI_RUST_CARGO_BIN:-$CARGO_HOME_DIR/bin/cargo}"
BUILD_JOBS="${LUMI_RUST_BUILD_JOBS:-2}"
[[ "$BUILD_JOBS" =~ ^[1-9][0-9]?$ ]] && ((BUILD_JOBS <= 32)) || {
  echo "LUMI_RUST_BUILD_JOBS must be between 1 and 32." >&2
  exit 2
}
[[ -x "$CARGO_BIN" ]] || {
  echo "Rust backend updates require cargo at $CARGO_BIN." >&2
  exit 1
}
if ! env RUSTUP_HOME="$RUSTUP_HOME_DIR" CARGO_HOME="$CARGO_HOME_DIR" \
  "$CARGO_BIN" +1.88.0 --version >/dev/null 2>&1; then
  echo "Rust backend updates require the Rust 1.88.0 toolchain for this user." >&2
  exit 1
fi

COMMIT="$(git -C "$PROJECT_DIR" rev-parse --verify HEAD)"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "Could not identify the checked-out LumiStripe commit." >&2
  exit 1
}

# The updater installs the new release beside Cargo's root workspace output.
# Keep one validated copy of legacy releases so a failed layout transition can
# still restart the previous checkout.
source "$PROJECT_DIR/deploy/update-support.sh"
migrate_legacy_rust_deploy_root || exit 1

DEPLOY_ROOT="$PROJECT_DIR/target/rust-deploy"
BUILD_DIR="$DEPLOY_ROOT/build"
RELEASES_DIR="$DEPLOY_ROOT/releases"
RELEASE_DIR="$RELEASES_DIR/$COMMIT"
mkdir -p -- "$BUILD_DIR" "$RELEASES_DIR"

activate_release() {
  local link_tmp="$DEPLOY_ROOT/.current.$$.tmp"
  ln -s "releases/$COMMIT" "$link_tmp"
  mv -Tf -- "$link_tmp" "$DEPLOY_ROOT/current"
}

if [[ -d "$RELEASE_DIR" ]]; then
  if [[ "$(cat "$RELEASE_DIR/source-commit" 2>/dev/null || true)" == "$COMMIT" && \
    -x "$RELEASE_DIR/lumistripe-web" && -x "$RELEASE_DIR/lumistripe-engine" && \
    -x "$RELEASE_DIR/lumistripe-deploy-helper" && -x "$RELEASE_DIR/lumistripe" ]]; then
    activate_release
    echo "Reusing Rust binaries for $COMMIT."
    exit 0
  fi
  echo "An incomplete Rust release already exists for $COMMIT: $RELEASE_DIR" >&2
  exit 1
fi

run_cargo() {
  env \
    RUSTUP_HOME="$RUSTUP_HOME_DIR" \
    CARGO_HOME="$CARGO_HOME_DIR" \
    CARGO_TARGET_DIR="$BUILD_DIR" \
    CARGO_BUILD_JOBS="$BUILD_JOBS" \
    "$CARGO_BIN" +1.88.0 "$@"
}

run_cargo build --locked --release --manifest-path "$PROJECT_DIR/Cargo.toml" \
  --package lumistripe-engine --bin lumistripe-engine
run_cargo build --locked --release --manifest-path "$PROJECT_DIR/Cargo.toml" \
  --package lumistripe-web --bin lumistripe-web --features bluez,pipewire
run_cargo build --locked --release --manifest-path "$PROJECT_DIR/Cargo.toml" \
  --package lumistripe-deploy-helper --bin lumistripe-deploy-helper
run_cargo build --locked --release --manifest-path "$PROJECT_DIR/Cargo.toml" \
  --package lumistripe-cli --bin lumistripe

BUILT_ENGINE="$BUILD_DIR/release/lumistripe-engine"
BUILT_WEB="$BUILD_DIR/release/lumistripe-web"
BUILT_DEPLOY_HELPER="$BUILD_DIR/release/lumistripe-deploy-helper"
BUILT_CLI="$BUILD_DIR/release/lumistripe"
[[ -x "$BUILT_ENGINE" && -x "$BUILT_WEB" && -x "$BUILT_DEPLOY_HELPER" && -x "$BUILT_CLI" ]] || {
  echo "Cargo completed without producing all version-matched Rust release binaries." >&2
  exit 1
}

STAGE_DIR="$(mktemp -d "$RELEASES_DIR/.stage.$COMMIT.XXXXXX")"
cleanup() {
  if [[ -n "${STAGE_DIR:-}" && -d "$STAGE_DIR" ]]; then
    rm -f -- "$STAGE_DIR/lumistripe-web" "$STAGE_DIR/lumistripe-engine" \
      "$STAGE_DIR/lumistripe-deploy-helper" "$STAGE_DIR/lumistripe" \
      "$STAGE_DIR/source-commit"
    rmdir -- "$STAGE_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT
install -m 755 -- "$BUILT_ENGINE" "$STAGE_DIR/lumistripe-engine"
install -m 755 -- "$BUILT_WEB" "$STAGE_DIR/lumistripe-web"
install -m 755 -- "$BUILT_DEPLOY_HELPER" "$STAGE_DIR/lumistripe-deploy-helper"
install -m 755 -- "$BUILT_CLI" "$STAGE_DIR/lumistripe"
printf '%s\n' "$COMMIT" > "$STAGE_DIR/source-commit"
mv -- "$STAGE_DIR" "$RELEASE_DIR"
STAGE_DIR=
activate_release
echo "Activated version-matched Rust application and deployment binaries for $COMMIT."
