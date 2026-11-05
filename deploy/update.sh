#!/usr/bin/env bash
set -Eeuo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=deploy/tls.sh
source "$SCRIPT_DIR/tls.sh"
# shellcheck source=deploy/update-support.sh
source "$SCRIPT_DIR/update-support.sh"
# shellcheck source=deploy/soloist.sh
source "$SCRIPT_DIR/soloist.sh"
PROJECT_DIR="$(canonical_project_directory \
  "${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}")" || exit 1
PROJECT_USER="${LUMI_SERVICE_USER:-${SUDO_USER:-}}"
TARGET_REF="${1:-refs/remotes/lumistripe-upstream/main}"
DEPLOY_MODE="${LUMI_DEPLOY_MODE:-binary}"
EFFECTIVE_DEPLOY_MODE="$DEPLOY_MODE"
OFFLINE_COMMIT="${LUMI_OFFLINE_COMMIT:-}"
LOCK_DIR=/run/lumistripe-update
LOCK_FILE="$LOCK_DIR/update.lock"
SERVICE_FILE=/etc/systemd/system/lumistripe-web.service
SPOTIFY_SERVICE_FILE=/etc/systemd/system/lumistripe-spotify.service
SPOTIFY_ENV_FILE=/etc/lumistripe/lumistripe-spotify.env
TLS_DIR=/etc/nginx/certs
TLS_CERT="$TLS_DIR/led.controller.pem"
TLS_KEY="$TLS_DIR/led.controller-key.pem"
PREVIOUS_REF=
TARGET_COMMIT=
ROLLING_BACK=0
UPDATE_SWITCHED=0
RELEASE_ACTIVATED=0

case "$DEPLOY_MODE" in
  binary|source|auto) ;;
  *) echo "LUMI_DEPLOY_MODE must be binary, source, or auto." >&2; exit 2 ;;
esac
if [[ -n "$OFFLINE_COMMIT" ]]; then
  [[ "$DEPLOY_MODE" == binary ]] || {
    echo "LUMI_OFFLINE_COMMIT requires LUMI_DEPLOY_MODE=binary." >&2
    exit 2
  }
  [[ "$OFFLINE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo "LUMI_OFFLINE_COMMIT must be a full lowercase Git SHA." >&2
    exit 2
  }
fi

if [[ $EUID -ne 0 ]]; then
  echo "Run this updater as root (for example: sudo $0)." >&2
  exit 1
fi
[[ -d "$PROJECT_DIR" ]] || { echo "Project directory not found: $PROJECT_DIR" >&2; exit 1; }
if [[ -z "$PROJECT_USER" || "$PROJECT_USER" == root ]]; then
  PROJECT_USER="$(stat -c '%U' "$PROJECT_DIR" 2>/dev/null || true)"
fi
[[ -n "$PROJECT_USER" && "$PROJECT_USER" != root ]] || {
  echo "Set LUMI_SERVICE_USER to a non-root account for this update." >&2
  exit 1
}
[[ "$PROJECT_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || {
  echo "Invalid Linux service username: $PROJECT_USER" >&2
  exit 1
}
id "$PROJECT_USER" >/dev/null 2>&1 || {
  echo "Service user does not exist: $PROJECT_USER" >&2
  exit 1
}
PROJECT_HOME="$(getent passwd "$PROJECT_USER" | cut -d: -f6)"
PROJECT_GROUP="$(id -gn "$PROJECT_USER")"
SERVICE_USER="$PROJECT_USER"
SERVICE_HOME="$PROJECT_HOME"
SERVICE_GROUP="$PROJECT_GROUP"
UPDATE_STATE_FILE="${LUMI_UPDATE_STATE_FILE:-/var/lib/lumistripe/update/status.json}"
WEB_ENV_FILE=/etc/lumistripe/lumistripe-web.env
BUN_BIN="$PROJECT_HOME/.bun/bin/bun"
WIREPLUMBER_DIR="$PROJECT_HOME/.config/wireplumber/wireplumber.conf.d"
WIREPLUMBER_FILE="$WIREPLUMBER_DIR/90-lumistripe-bluetooth.conf"
PIPEWIRE_DIR="$PROJECT_HOME/.config/pipewire/pipewire.conf.d"
SPOTIFY_PIPEWIRE_FILE="$PIPEWIRE_DIR/90-lumistripe-spotify.conf"
SERVICE_RUNTIME_DIR="/run/user/$(id -u "$PROJECT_USER")"
NGINX_AVAILABLE=/etc/nginx/sites-available/led-controller
[[ -e "$PROJECT_DIR/.git" ]] || { echo "Not a Git checkout: $PROJECT_DIR" >&2; exit 1; }
command -v runuser >/dev/null || { echo "runuser is required" >&2; exit 1; }
command -v flock >/dev/null || { echo "flock is required (install util-linux)" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "Python 3 is required for safe release validation." >&2; exit 1; }
resolve_web_backend "" "$WEB_ENV_FILE" >/dev/null || exit 1

install -d -o root -g root -m 755 "$LOCK_DIR"
exec 9>"$LOCK_FILE"
chmod 644 "$LOCK_FILE"
flock -n 9 || { echo "Another LumiStripe update is already running." >&2; exit 1; }

as_user() {
  runuser -u "$PROJECT_USER" -- env \
    HOME="$PROJECT_HOME" \
    PATH="$PROJECT_HOME/.bun/bin:$PROJECT_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$@"
}
as_user_with_env() {
  runuser -u "$PROJECT_USER" -- env \
    HOME="$PROJECT_HOME" \
    PATH="$PROJECT_HOME/.bun/bin:$PROJECT_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$@"
}

install_audio_bridge_packages() {
  local missing=()
  local package
  for package in pipewire-alsa libasound2-plugins libatomic1; do
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      missing+=("$package")
    fi
  done
  if ((${#missing[@]} == 0)); then
    return
  fi

  if [[ -n "$OFFLINE_COMMIT" ]]; then
    echo "Offline update requires these runtime packages to be installed already: ${missing[*]}" >&2
    return 1
  fi

  echo "Installing missing PipeWire/ALSA capture packages..."
  apt-get update
  apt-get install --yes --no-install-recommends "${missing[@]}"
}

create_spotify_environment_file() {
  install -d -m 755 /etc/lumistripe
  if [[ ! -e "$SPOTIFY_ENV_FILE" ]]; then
    local api_key="${LUMI_SPOTIFY_API_KEY:-}"
    if [[ -z "$api_key" ]]; then
      echo "Spotify secret is not configured; leaving Spotify service disabled." >&2
      return 0
    fi
    [[ "$api_key" != *$'\n'* && "$api_key" != *$'\r'* ]] || {
      echo "LUMI_SPOTIFY_API_KEY must be a single-line value." >&2
      return 1
    }
    local api_key_replacement
    local data_dir_replacement
    local cache_dir_replacement
    api_key_replacement="$(printf '%s' "$api_key" | sed 's/[&|\\]/\\&/g')"
    data_dir_replacement="$(printf '%s' "$PROJECT_HOME/.local/share/soloist" | sed 's/[&|\\]/\\&/g')"
    cache_dir_replacement="$(printf '%s' "$PROJECT_HOME/.cache/soloist" | sed 's/[&|\\]/\\&/g')"
    install -o "$PROJECT_USER" -g "$PROJECT_GROUP" -m 600 \
      "$SCRIPT_DIR/lumistripe-spotify.env.example" "$SPOTIFY_ENV_FILE"
    sed -i \
      -e "s|^LUMI_SPOTIFY_API_KEY=.*|LUMI_SPOTIFY_API_KEY=$api_key_replacement|" \
      -e "s|^LUMI_SPOTIFY_DATA_DIR=.*|LUMI_SPOTIFY_DATA_DIR=$data_dir_replacement|" \
      -e "s|^LUMI_SPOTIFY_CACHE_DIR=.*|LUMI_SPOTIFY_CACHE_DIR=$cache_dir_replacement|" \
      "$SPOTIFY_ENV_FILE"
  fi
  chown "$PROJECT_USER:$PROJECT_GROUP" "$SPOTIFY_ENV_FILE"
  chmod 600 "$SPOTIFY_ENV_FILE"
}

spotify_environment_ready() {
  [[ -f "$SPOTIFY_ENV_FILE" ]] || return 1
  local api_key
  api_key="$(awk -F= '$1 == "LUMI_SPOTIFY_API_KEY" { print substr($0, index($0, "=") + 1); exit }' "$SPOTIFY_ENV_FILE")"
  [[ -n "$api_key" && "$api_key" != REPLACE_WITH_* ]]
}

git_user() { as_user git -C "$PROJECT_DIR" "$@"; }

binary_target_supported() {
  local architecture distribution codename
  architecture="$(uname -m)"
  distribution="$(. /etc/os-release && printf '%s' "${ID:-}")"
  codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
  [[ "$architecture" == aarch64 && "$codename" == trixie &&
      ( "$distribution" == debian || "$distribution" == raspbian ) ]]
}

install_release_verifier() {
  "$SCRIPT_DIR/install-github-cli.sh"
}

install_source_build_packages() {
  echo "Installing native build dependencies for the selected source update..."
  apt-get update
  apt-get install --yes --no-install-recommends \
    build-essential pkg-config libasound2-dev libclang-dev libdbus-1-dev \
    libpipewire-0.3-dev libpulse-dev libgpiod-dev
}

require_source_toolchains() {
  [[ -x "$BUN_BIN" ]] || {
    echo "Bun is required for a source update; run deploy/install.sh with LUMI_DEPLOY_MODE=source." >&2
    return 1
  }
  [[ -x "$PROJECT_HOME/.cargo/bin/cargo" ]] || {
    echo "Cargo is required for a source update; run deploy/install.sh with LUMI_DEPLOY_MODE=source." >&2
    return 1
  }
}

stage_prebuilt_release() {
  local commit="${1:-}"
  local tool
  local uid gid
  local -a install_args
  tool="$(release_artifact_tool)" || return 1
  uid="$(id -u "$PROJECT_USER")"
  gid="$(id -g "$PROJECT_USER")"
  install_args=(
    install
    --commit "$commit"
    --release-root "$PROJECT_DIR/target/rust-deploy/releases"
    --cache-root "$RELEASE_CACHE_DIR"
    --uid "$uid"
    --gid "$gid"
  )
  if [[ "${2:-}" == --cached-only ]]; then
    install_args+=(--cached-only)
  fi
  python3 "$tool" "${install_args[@]}"
}

build_rust_backend() {
  [[ -x "$PROJECT_DIR/deploy/build-rust-backend.sh" ]] || {
    echo "Rust deployment builder is missing from $PROJECT_DIR/deploy." >&2
    return 1
  }
  # Build every application component from the same source commit.
  as_user_with_env \
    RUSTUP_HOME="$PROJECT_HOME/.rustup" \
    CARGO_HOME="$PROJECT_HOME/.cargo" \
    "$PROJECT_DIR/deploy/build-rust-backend.sh" "$PROJECT_DIR" || return 1
  install_rust_cli_command "$PROJECT_HOME"
}

prune_rust_backend_releases() {
  local active_commit release_commit release_dir
  local releases_dir="$PROJECT_DIR/target/rust-deploy/releases"
  [[ -d "$releases_dir" ]] || return 0
  active_commit="$(git_user rev-parse --verify HEAD)" || return 1

  for release_dir in "$releases_dir"/*; do
    [[ -d "$release_dir" && ! -L "$release_dir" ]] || continue
    release_commit="${release_dir##*/}"
    [[ "$release_commit" =~ ^[0-9a-f]{40}$ ]] || continue
    [[ "$release_commit" == "$active_commit" || "$release_commit" == "$PREVIOUS_REF" ]] && continue
    [[ -d "$release_dir" && ! -L "$release_dir" ]] || return 1
    as_user rm -rf -- "$release_dir" || return 1
  done
}

prune_release_cache() {
  local active_commit release_commit cache_dir
  [[ -d "$RELEASE_CACHE_DIR" && ! -L "$RELEASE_CACHE_DIR" ]] || return 0
  active_commit="$(git_user rev-parse --verify HEAD)" || return 1
  for cache_dir in "$RELEASE_CACHE_DIR"/*; do
    [[ -e "$cache_dir" || -L "$cache_dir" ]] || continue
    release_commit="${cache_dir##*/}"
    [[ "$release_commit" =~ ^[0-9a-f]{40}$ ]] || continue
    [[ "$release_commit" == "$active_commit" || "$release_commit" == "$PREVIOUS_REF" ]] && continue
    [[ -d "$cache_dir" && ! -L "$cache_dir" ]] || return 1
    rm -rf -- "$cache_dir"
  done
}

build_frontend() (
  cd "$PROJECT_DIR/apps/lumistripe-web/frontend" || return 1
  as_user "$BUN_BIN" install --frozen-lockfile || return 1
  as_user "$BUN_BIN" run build || return 1
)

install_wireplumber_config() {
  as_user install -d -m 755 "$WIREPLUMBER_DIR" || return 1
  as_user install -m 644 "$SCRIPT_DIR/90-lumistripe-bluetooth.conf" "$WIREPLUMBER_FILE" || return 1
  as_user install -d -m 755 "$PIPEWIRE_DIR" || return 1
  as_user install -m 644 "$SCRIPT_DIR/90-lumistripe-spotify.conf" "$SPOTIFY_PIPEWIRE_FILE" || return 1
}

render_service_unit() {
  local rendered_file
  local project_replacement
  local home_replacement
  local user_replacement
  local group_replacement
  local uid_replacement
  rendered_file="$(mktemp /tmp/lumistripe-service-update.XXXXXX)" || return 1
  project_replacement="$(printf '%s' "$PROJECT_DIR" | sed 's/[&|\\]/\\&/g')"
  home_replacement="$(printf '%s' "$PROJECT_HOME" | sed 's/[&|\\]/\\&/g')"
  user_replacement="$(printf '%s' "$PROJECT_USER" | sed 's/[&|\\]/\\&/g')"
  group_replacement="$(printf '%s' "$PROJECT_GROUP" | sed 's/[&|\\]/\\&/g')"
  uid_replacement="$(printf '%s' "$(id -u "$PROJECT_USER")" | sed 's/[&|\\]/\\&/g')"

  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    -e "s|@LUMI_SERVICE_GROUP@|$group_replacement|g" \
    -e "s|@LUMI_PROJECT_DIR@|$project_replacement|g" \
    -e "s|@LUMI_HOME@|$home_replacement|g" \
    -e "s|@LUMI_SERVICE_UID@|$uid_replacement|g" \
    "$SCRIPT_DIR/lumistripe-web.service" > "$rendered_file" || {
      rm -f -- "$rendered_file"
      return 1
    }
  if grep -q '@LUMI_[A-Z_]*@' "$rendered_file"; then
    rm -f -- "$rendered_file"
    echo "The systemd service template contains unresolved placeholders." >&2
    return 1
  fi
  install -o root -g root -m 644 "$rendered_file" "$SERVICE_FILE" || {
    rm -f -- "$rendered_file"
    return 1
  }
  rm -f -- "$rendered_file"
}

render_spotify_service_unit() {
  local rendered_file
  local project_replacement
  local home_replacement
  local user_replacement
  local group_replacement
  local uid_replacement
  rendered_file="$(mktemp /tmp/lumistripe-spotify-service-update.XXXXXX)" || return 1
  project_replacement="$(printf '%s' "$PROJECT_DIR" | sed 's/[&|\\]/\\&/g')"
  home_replacement="$(printf '%s' "$PROJECT_HOME" | sed 's/[&|\\]/\\&/g')"
  user_replacement="$(printf '%s' "$PROJECT_USER" | sed 's/[&|\\]/\\&/g')"
  group_replacement="$(printf '%s' "$PROJECT_GROUP" | sed 's/[&|\\]/\\&/g')"
  uid_replacement="$(printf '%s' "$(id -u "$PROJECT_USER")" | sed 's/[&|\\]/\\&/g')"

  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    -e "s|@LUMI_SERVICE_GROUP@|$group_replacement|g" \
    -e "s|@LUMI_PROJECT_DIR@|$project_replacement|g" \
    -e "s|@LUMI_HOME@|$home_replacement|g" \
    -e "s|@LUMI_SERVICE_UID@|$uid_replacement|g" \
    "$SCRIPT_DIR/lumistripe-spotify.service" > "$rendered_file" || {
      rm -f -- "$rendered_file"
      return 1
    }
  if grep -q '@LUMI_[A-Z_]*@' "$rendered_file"; then
    rm -f -- "$rendered_file"
    echo "The Spotify systemd service template contains unresolved placeholders." >&2
    return 1
  fi
  install -o root -g root -m 644 "$rendered_file" "$SPOTIFY_SERVICE_FILE" || {
    rm -f -- "$rendered_file"
    return 1
  }
  rm -f -- "$rendered_file"
}

refresh_deployment_files() {
  # Creating a certificate involves files writable by the service account.
  # Require an administrator to repair it through the installer instead of
  # importing those files as root during an unprivileged update request.
  if [[ ! -s "$TLS_CERT" || ! -s "$TLS_KEY" ]]; then
    echo "TLS certificate or key missing; rerun deploy/install.sh as administrator." >&2
    return 1
  fi

  install_privileged_network_helper || return 1
  install_update_support || return 1
  install_wireplumber_config || return 1
  render_service_unit || return 1
  render_spotify_service_unit || return 1
  systemctl daemon-reload || return 1
  systemctl enable --now lumistripe-update-check.timer || return 1

  install -d -m 755 /etc/nginx/sites-available /etc/nginx/sites-enabled || return 1
  install -o root -g root -m 644 \
    "$SCRIPT_DIR/nginx/led-controller.conf" "$NGINX_AVAILABLE" || return 1
  ln -sfn "$NGINX_AVAILABLE" /etc/nginx/sites-enabled/led-controller || return 1
  nginx -t || return 1
  systemctl reload nginx.service || return 1

  if [[ -S "$SERVICE_RUNTIME_DIR/bus" ]]; then
    as_user_with_env \
      XDG_RUNTIME_DIR="$SERVICE_RUNTIME_DIR" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=$SERVICE_RUNTIME_DIR/bus" \
      systemctl --user restart pipewire pipewire-pulse wireplumber || return 1
  fi
}

wait_for_web_service_health() {
  local attempt
  for attempt in {1..60}; do
    if curl --disable --fail --silent --show-error --max-time 0.25 \
      http://127.0.0.1:8000/api/health >/dev/null; then
      return 0
    fi
    if (( attempt < 60 )); then
      sleep 0.25
    fi
  done
  return 1
}

rollback() {
  [[ -n "$PREVIOUS_REF" && "$ROLLING_BACK" -eq 0 ]] || return 0
  if (( UPDATE_SWITCHED == 0 && RELEASE_ACTIVATED == 0 )); then
    return 0
  fi
  ROLLING_BACK=1
  echo "Update failed; rolling back to $PREVIOUS_REF..." >&2
  local failed=0
  if (( UPDATE_SWITCHED )); then
    if ! git_user switch --detach "$PREVIOUS_REF"; then
      echo "Rollback could not restore the previous revision; manual recovery is required." >&2
      return 1
    fi
  fi
  source "$SCRIPT_DIR/update-support.sh"
  activate_rust_release "$PREVIOUS_REF" || failed=1
  RELEASE_ACTIVATED=0
  install_rust_cli_command "$PROJECT_HOME" || failed=1
  refresh_deployment_files || failed=1
  if systemctl restart lumistripe-web.service; then
    if systemctl is-active --quiet lumistripe-web.service; then
      if ! wait_for_web_service_health; then
        echo "Rolled-back web service did not become healthy in time." >&2
        failed=1
      fi
    else
      echo "Rolled-back web service is not active." >&2
      failed=1
    fi
  else
    echo "Could not restart the rolled-back web service." >&2
    failed=1
  fi
  if spotify_environment_ready; then
    systemctl restart lumistripe-spotify.service || failed=1
  fi
  if (( failed )); then
    echo "Rollback failed; manual recovery is required." >&2
    return 1
  fi
}

record_update_state() {
  as_user "$PROJECT_DIR/target/rust-deploy/current/lumistripe-deploy-helper" \
    update-state "$1" \
    --project-dir "$PROJECT_DIR" \
    --state-file "$UPDATE_STATE_FILE" || true
}

handle_update_error() {
  local exit_status=$?
  trap - ERR
  rollback || echo "Inspect the updater and service logs before retrying." >&2
  record_update_state install-failed
  exit "$exit_status"
}
trap handle_update_error ERR

STATUS=$(git_user status --porcelain)
[[ -z "$STATUS" ]] || { echo "Working tree is dirty; commit or stash changes before updating." >&2; exit 1; }
record_update_state install-start
PREVIOUS_REF=$(git_user rev-parse HEAD)
migrate_legacy_rust_deploy_root
ensure_current_release_static
validate_rust_release "$PROJECT_DIR/target/rust-deploy/releases" "$PREVIOUS_REF"
ACTIVE_RELEASE="$(readlink -f -- "$PROJECT_DIR/target/rust-deploy/current" 2>/dev/null || true)"
[[ "$ACTIVE_RELEASE" == "$PROJECT_DIR/target/rust-deploy/releases/$PREVIOUS_REF" ]] || {
  echo "The active release does not match the project checkout; refusing to update." >&2
  exit 1
}

if [[ "$DEPLOY_MODE" != source ]] && ! binary_target_supported; then
  if [[ "$DEPLOY_MODE" == binary ]]; then
    echo "Prebuilt releases require aarch64 Debian or Raspberry Pi OS Trixie. Set LUMI_DEPLOY_MODE=source for a local build." >&2
    exit 1
  fi
  EFFECTIVE_DEPLOY_MODE=source
  echo "No LumiStripe prebuilt bundle targets this platform; auto mode will compile from source."
fi

if [[ "$EFFECTIVE_DEPLOY_MODE" != source ]]; then
  if [[ -z "$OFFLINE_COMMIT" ]]; then
    install_release_verifier
  else
    echo "Offline update will use only the previously verified local release cache."
  fi
fi
if [[ -n "$OFFLINE_COMMIT" ]]; then
  TARGET_COMMIT="$(git_user rev-parse --verify "${OFFLINE_COMMIT}^{commit}")"
  [[ "$TARGET_COMMIT" == "$OFFLINE_COMMIT" ]] || {
    echo "The offline commit is not present in the local checkout." >&2
    exit 1
  }
else
  git_user -c core.hooksPath=/dev/null fetch --prune \
    https://github.com/woobble/LumiStripe.git \
    +refs/heads/main:refs/remotes/lumistripe-upstream/main
  TARGET_COMMIT="$(git_user rev-parse --verify "${TARGET_REF}^{commit}")"
fi

if [[ "$EFFECTIVE_DEPLOY_MODE" != source ]]; then
  if [[ -n "$OFFLINE_COMMIT" ]]; then
    if stage_prebuilt_release "$TARGET_COMMIT" --cached-only; then
      EFFECTIVE_DEPLOY_MODE=binary
    else
      echo "The offline update requires an intact, previously verified cached release." >&2
      exit 1
    fi
  elif stage_prebuilt_release "$TARGET_COMMIT"; then
    EFFECTIVE_DEPLOY_MODE=binary
  else
    stage_status=$?
    if may_fallback_to_source "$DEPLOY_MODE" "$stage_status"; then
      echo "No compatible published release is available; auto mode is falling back to a local source build."
      EFFECTIVE_DEPLOY_MODE=source
    else
      echo "Prebuilt release installation failed; the project checkout and active release were not switched." >&2
      exit "$stage_status"
    fi
  fi
fi

install_audio_bridge_packages
if [[ "$EFFECTIVE_DEPLOY_MODE" == source ]]; then
  install_source_build_packages
  require_source_toolchains
fi

git_user -c core.hooksPath=/dev/null switch --detach "$TARGET_COMMIT"
UPDATE_SWITCHED=1
source "$SCRIPT_DIR/update-support.sh"

create_spotify_environment_file
if ! spotify_environment_ready; then
  systemctl disable --now lumistripe-spotify.service >/dev/null 2>&1 || true
fi
if [[ "$EFFECTIVE_DEPLOY_MODE" == source ]]; then
  build_frontend
  build_rust_backend
  RELEASE_ACTIVATED=1
else
  activate_rust_release "$TARGET_COMMIT"
  RELEASE_ACTIVATED=1
  install_rust_cli_command "$PROJECT_HOME"
fi
if spotify_environment_ready; then
  if [[ -z "$OFFLINE_COMMIT" ]]; then
    install_spotify_soloist "$PROJECT_HOME"
  else
    echo "Offline update leaves the existing Spotify Soloist installation unchanged."
  fi
fi
refresh_deployment_files
if spotify_environment_ready; then
  systemctl enable lumistripe-spotify.service
  systemctl restart lumistripe-spotify.service
fi
systemctl restart lumistripe-web.service
systemctl is-active --quiet lumistripe-web.service

if wait_for_web_service_health; then
  echo "LumiStripe updated to $(git_user rev-parse --short HEAD)."
  ACTIVE_COMMIT="$(git_user rev-parse --verify HEAD)"
  prune_rust_backend_releases || echo "Could not prune old LumiStripe releases." >&2
  prune_release_cache || echo "Could not prune old verified release downloads." >&2
  retire_legacy_rust_deploy_root "$ACTIVE_COMMIT" "$PREVIOUS_REF" || \
    echo "Could not retire legacy Rust release state; it was left in place." >&2
  record_update_state install-succeeded
  trap - ERR
  exit 0
fi
echo "Service did not become healthy in time." >&2
false
