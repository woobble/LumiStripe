#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}"
BACKEND="${LUMI_WEB_BACKEND:-python}"

case "$BACKEND" in
  python)
    UV_BIN="${LUMI_UV_BIN:-${HOME:?HOME must be set}/.local/bin/uv}"
    exec "$UV_BIN" run \
      --directory "$PROJECT_DIR" \
      --python "${LUMI_PYTHON_VERSION:-3.12}" \
      --no-dev \
      --extra hardware-gain \
      lumistripe-web --host 127.0.0.1 --hardware
    ;;
  rust)
    RUST_DEPLOY_CURRENT="$PROJECT_DIR/rust/target/rust-deploy/current"
    if [[ -e "$RUST_DEPLOY_CURRENT" || -L "$RUST_DEPLOY_CURRENT" ]]; then
      if [[ ! -L "$RUST_DEPLOY_CURRENT" ]]; then
        echo "Rust deployment pointer is not a symbolic link: $RUST_DEPLOY_CURRENT" >&2
        exit 127
      fi
      PROJECT_COMMIT="$(git -C "$PROJECT_DIR" rev-parse --verify HEAD 2>/dev/null || true)"
      DEPLOYED_COMMIT="$(cat "$RUST_DEPLOY_CURRENT/source-commit" 2>/dev/null || true)"
      if [[ -z "$PROJECT_COMMIT" || "$PROJECT_COMMIT" != "$DEPLOYED_COMMIT" ]]; then
        echo "Rust binaries do not match the checked-out project commit. Run deploy/update.sh before starting the Rust backend." >&2
        exit 127
      fi
      RUST_DEPLOY_RELEASE="$(readlink -f -- "$RUST_DEPLOY_CURRENT" 2>/dev/null || true)"
      if [[ -z "$RUST_DEPLOY_RELEASE" ]]; then
        echo "Rust deployment pointer does not resolve to a release directory." >&2
        exit 127
      fi
      # Pin the web process and any supervised engine restart to this release.
      WEB_BIN="$RUST_DEPLOY_RELEASE/lumistripe-web"
      ENGINE_BIN="$RUST_DEPLOY_RELEASE/lumistripe-engine"
    else
      # Preserve the documented manual build path for the first Rust startup.
      WEB_BIN="$PROJECT_DIR/rust/target/release/lumistripe-web"
      ENGINE_BIN="$PROJECT_DIR/rust/target/release/lumistripe-engine"
    fi
    STATIC_DIR="$PROJECT_DIR/apps/lumistripe-web/src/lumistripe_web/static"
    SETTINGS_FILE="${LUMISTRIPE_SETTINGS_FILE:-${HOME:?HOME must be set}/.config/lumistripe/settings.json}"
    OUTPUT_BACKEND="${LUMI_RUST_OUTPUT_BACKEND:-spi}"

    if [[ ! -x "$WEB_BIN" || ! -x "$ENGINE_BIN" ]]; then
      echo "Rust web binaries are missing. Install libdbus-1-dev and libpipewire-0.3-dev, then build lumistripe-engine and lumistripe-web with the bluez,pipewire features." >&2
      exit 127
    fi
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
    ;;
  *)
    echo "Unsupported LUMI_WEB_BACKEND: $BACKEND (expected python or rust)." >&2
    exit 2
    ;;
esac
