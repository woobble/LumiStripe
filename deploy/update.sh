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
PYTHON_VERSION="${LUMI_PYTHON_VERSION:-3.12}"
UV_VERSION="${LUMI_UV_VERSION:-0.13.0}"
# Preserve newer installs; UV_VERSION is the minimum supported version.
TARGET_REF="${1:-refs/remotes/lumistripe-upstream/main}"
LOCK_DIR=/run/lumistripe-update
LOCK_FILE="$LOCK_DIR/update.lock"
SERVICE_FILE=/etc/systemd/system/lumistripe-web.service
SPOTIFY_SERVICE_FILE=/etc/systemd/system/lumistripe-spotify.service
SPOTIFY_ENV_FILE=/etc/lumistripe/lumistripe-spotify.env
TLS_DIR=/etc/nginx/certs
TLS_CERT="$TLS_DIR/led.controller.pem"
TLS_KEY="$TLS_DIR/led.controller-key.pem"
PREVIOUS_REF=
ROLLING_BACK=0

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
UV_BIN="$PROJECT_HOME/.local/bin/uv"
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
[[ -x "$UV_BIN" ]] || { echo "uv is missing at $UV_BIN; run deploy/install.sh first." >&2; exit 1; }
[[ -x "$BUN_BIN" ]] || { echo "Bun is missing at $BUN_BIN; run deploy/install.sh first." >&2; exit 1; }

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

ensure_uv_version() {
  local installed_uv_version
  installed_uv_version="$(as_user "$UV_BIN" --version 2>/dev/null | awk '{ print $2 }' || true)"
  if ! uv_version_at_least "$installed_uv_version" "$UV_VERSION"; then
    echo "Updating uv to at least $UV_VERSION for $PROJECT_USER..."
    as_user "$UV_BIN" self update "$UV_VERSION"
    installed_uv_version="$(as_user "$UV_BIN" --version 2>/dev/null | awk '{ print $2 }' || true)"
  fi
  uv_version_at_least "$installed_uv_version" "$UV_VERSION" || {
    echo "Expected uv $UV_VERSION or newer at $UV_BIN; found ${installed_uv_version:-no usable uv binary}." >&2
    return 1
  }
}

install_audio_bridge_packages() {
  local missing=()
  local package
  for package in pipewire-alsa libasound2-plugins; do
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      missing+=("$package")
    fi
  done
  if ((${#missing[@]} == 0)); then
    return
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

sync_dependencies() (
  cd "$PROJECT_DIR" || return 1
  as_user "$UV_BIN" python install "$PYTHON_VERSION" || return 1
  as_user_with_env \
    LUMISTRIPE_BUILD_EXTENSIONS=all \
    UV_PYTHON_PREFERENCE=only-managed \
    "$UV_BIN" sync --locked --python "$PYTHON_VERSION" --no-dev \
    --extra hardware-gain --reinstall-package lumistripe-core || return 1
  as_user "$PROJECT_DIR/.venv/bin/python" -c \
    'import lumistripe.audio._audio, lumistripe.gpio._gpiomem, lumistripe.gpio._sm16716' || return 1
)

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
  local python_replacement
  local user_replacement
  local group_replacement
  local uid_replacement
  rendered_file="$(mktemp /tmp/lumistripe-service-update.XXXXXX)" || return 1
  project_replacement="$(printf '%s' "$PROJECT_DIR" | sed 's/[&|\\]/\\&/g')"
  home_replacement="$(printf '%s' "$PROJECT_HOME" | sed 's/[&|\\]/\\&/g')"
  python_replacement="$(printf '%s' "$PYTHON_VERSION" | sed 's/[&|\\]/\\&/g')"
  user_replacement="$(printf '%s' "$PROJECT_USER" | sed 's/[&|\\]/\\&/g')"
  group_replacement="$(printf '%s' "$PROJECT_GROUP" | sed 's/[&|\\]/\\&/g')"
  uid_replacement="$(printf '%s' "$(id -u "$PROJECT_USER")" | sed 's/[&|\\]/\\&/g')"

  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    -e "s|@LUMI_SERVICE_GROUP@|$group_replacement|g" \
    -e "s|@LUMI_PROJECT_DIR@|$project_replacement|g" \
    -e "s|@LUMI_HOME@|$home_replacement|g" \
    -e "s|@LUMI_PYTHON_VERSION@|$python_replacement|g" \
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

rollback() {
  [[ -n "$PREVIOUS_REF" && "$ROLLING_BACK" -eq 0 ]] || return 0
  ROLLING_BACK=1
  echo "Update failed; rolling back to $PREVIOUS_REF..." >&2
  local failed=0
  if ! git_user switch --detach "$PREVIOUS_REF"; then
    echo "Rollback could not restore the previous revision; manual recovery is required." >&2
    return 1
  fi
  if ! git_user submodule update --init --recursive; then
    echo "Rollback could not restore submodules; manual recovery is required." >&2
    return 1
  fi
  sync_dependencies || failed=1
  build_frontend || failed=1
  refresh_deployment_files || failed=1
  systemctl restart lumistripe-web.service || failed=1
  if spotify_environment_ready; then
    systemctl restart lumistripe-spotify.service || failed=1
  fi
  if (( failed )); then
    echo "Rollback failed; manual recovery is required." >&2
    return 1
  fi
}

record_update_state() {
  as_user /usr/bin/python3 "$SCRIPT_DIR/update_state.py" "$1" \
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
ensure_uv_version
PREVIOUS_REF=$(git_user rev-parse HEAD)
git_user -c core.hooksPath=/dev/null fetch --prune \
  https://github.com/woobble/LumiStripe.git \
  +refs/heads/main:refs/remotes/lumistripe-upstream/main
TARGET_COMMIT=$(git_user rev-parse --verify "${TARGET_REF}^{commit}")
git_user -c core.hooksPath=/dev/null switch --detach "$TARGET_COMMIT"
git_user submodule update --init --recursive

install_audio_bridge_packages
create_spotify_environment_file
if spotify_environment_ready; then
  install_spotify_soloist "$PROJECT_HOME"
else
  systemctl disable --now lumistripe-spotify.service >/dev/null 2>&1 || true
fi
sync_dependencies
build_frontend
refresh_deployment_files
if spotify_environment_ready; then
  systemctl enable lumistripe-spotify.service
  systemctl restart lumistripe-spotify.service
fi
systemctl restart lumistripe-web.service
systemctl is-active --quiet lumistripe-web.service

for _ in {1..30}; do
  if curl --disable --fail --silent --show-error http://127.0.0.1:8000/api/health >/dev/null; then
    echo "LumiStripe updated to $(git_user rev-parse --short HEAD)."
    record_update_state install-succeeded
    trap - ERR
    exit 0
  fi
  sleep 1
done
echo "Service did not become healthy in time." >&2
false
