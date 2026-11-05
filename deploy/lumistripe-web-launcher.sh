#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR_INPUT="${LUMI_PROJECT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}"
PROJECT_DIR="$(cd -- "$PROJECT_DIR_INPUT" 2>/dev/null && pwd -P)" || {
  echo "LumiStripe project directory is missing: $PROJECT_DIR_INPUT" >&2
  exit 127
}
BACKEND="${LUMI_WEB_BACKEND:-rust}"
source "$PROJECT_DIR/deploy/update-support.sh"
require_rust_backend "$BACKEND" || exit 78

RUST_DEPLOY_CURRENT="$PROJECT_DIR/rust/target/rust-deploy/current"
if [[ ! -L "$RUST_DEPLOY_CURRENT" ]]; then
  echo "The Rust release pointer is missing: $RUST_DEPLOY_CURRENT. Run deploy/install.sh or deploy/update.sh to build and activate a commit-matched release." >&2
  exit 127
fi
PROJECT_COMMIT="$(git -C "$PROJECT_DIR" rev-parse --verify HEAD 2>/dev/null || true)"
DEPLOYED_COMMIT="$(cat -- "$RUST_DEPLOY_CURRENT/source-commit" 2>/dev/null || true)"
if [[ ! "$PROJECT_COMMIT" =~ ^[0-9a-f]{40}$ || "$PROJECT_COMMIT" != "$DEPLOYED_COMMIT" ]]; then
  echo "Rust binaries do not match the checked-out project commit. Run deploy/update.sh before starting the Rust backend." >&2
  exit 127
fi
RUST_DEPLOY_RELEASE="$(readlink -f -- "$RUST_DEPLOY_CURRENT" 2>/dev/null || true)"
EXPECTED_RELEASE="$PROJECT_DIR/rust/target/rust-deploy/releases/$PROJECT_COMMIT"
if [[ "$RUST_DEPLOY_RELEASE" != "$EXPECTED_RELEASE" ]]; then
  echo "Rust release pointer resolves outside the expected commit-matched release: $RUST_DEPLOY_CURRENT" >&2
  exit 127
fi
for binary in lumistripe-web lumistripe-engine lumistripe-deploy-helper lumistripe; do
  if [[ ! -f "$RUST_DEPLOY_RELEASE/$binary" || -L "$RUST_DEPLOY_RELEASE/$binary" || ! -x "$RUST_DEPLOY_RELEASE/$binary" ]]; then
    echo "Rust release $PROJECT_COMMIT is incomplete: missing executable $RUST_DEPLOY_RELEASE/$binary. Run deploy/update.sh to rebuild it." >&2
    exit 127
  fi
done

# Pin the web process and every supervised engine restart to this release.
WEB_BIN="$RUST_DEPLOY_RELEASE/lumistripe-web"
ENGINE_BIN="$RUST_DEPLOY_RELEASE/lumistripe-engine"
STATIC_DIR="$PROJECT_DIR/apps/lumistripe-web/static"
SETTINGS_FILE="${LUMISTRIPE_SETTINGS_FILE:-${HOME:?HOME must be set}/.config/lumistripe/settings.json}"
OUTPUT_BACKEND="${LUMI_RUST_OUTPUT_BACKEND:-spi}"

case "$OUTPUT_BACKEND" in
  spi|gpio|mock|mock-spi) ;;
  *)
    echo "Unsupported LUMI_RUST_OUTPUT_BACKEND: $OUTPUT_BACKEND" >&2
    exit 2
    ;;
esac

exec "$WEB_BIN" \
  --host 127.0.0.1 \
  --port 8000 \
  --engine-binary "$ENGINE_BIN" \
  --output-backend "$OUTPUT_BACKEND" \
  --static-dir "$STATIC_DIR" \
  --settings-file "$SETTINGS_FILE" \
  --secure-cookie
