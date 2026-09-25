#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
PROJECT_USER="${LUMI_SERVICE_USER:-${SUDO_USER:-}}"
PYTHON_VERSION="${LUMI_PYTHON_VERSION:-3.12}"
TARGET_REF="${1:-origin/main}"
LOCK_FILE=/run/lock/lumistripe-update.lock
SERVICE_FILE=/etc/systemd/system/lumistripe-web.service
SPOTIFY_SERVICE_FILE=/etc/systemd/system/lumistripe-spotify.service
SPOTIFY_ENV_FILE=/etc/lumistripe/lumistripe-spotify.env
SOLOIST_BIN=/usr/local/bin/soloist
PREVIOUS_REF=
ROLLING_BACK=0

if [[ $EUID -ne 0 ]]; then
  echo "Run this updater as root (for example: sudo $0 origin/main)." >&2
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
UV_BIN="$PROJECT_HOME/.local/bin/uv"
BUN_BIN="$PROJECT_HOME/.bun/bin/bun"
WIREPLUMBER_DIR="$PROJECT_HOME/.config/wireplumber/wireplumber.conf.d"
WIREPLUMBER_FILE="$WIREPLUMBER_DIR/90-lumistripe-bluetooth.conf"
PIPEWIRE_DIR="$PROJECT_HOME/.config/pipewire/pipewire.conf.d"
SPOTIFY_PIPEWIRE_FILE="$PIPEWIRE_DIR/90-lumistripe-spotify.conf"
SERVICE_RUNTIME_DIR="/run/user/$(id -u "$PROJECT_USER")"
NGINX_AVAILABLE=/etc/nginx/sites-available/led-controller
[[ -d "$PROJECT_DIR/.git" ]] || { echo "Not a Git checkout: $PROJECT_DIR" >&2; exit 1; }
command -v runuser >/dev/null || { echo "runuser is required" >&2; exit 1; }
command -v flock >/dev/null || { echo "flock is required (install util-linux)" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
[[ -x "$UV_BIN" ]] || { echo "uv is missing at $UV_BIN; run deploy/install.sh first." >&2; exit 1; }
[[ -x "$BUN_BIN" ]] || { echo "Bun is missing at $BUN_BIN; run deploy/install.sh first." >&2; exit 1; }

exec 9>"$LOCK_FILE"
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

install_spotify_soloist() {
  local architecture
  local archive_arch
  local archive_url
  local archive_file
  local extract_dir
  architecture="$(uname -m)"
  case "$architecture" in
    aarch64) archive_arch=arm64 ;;
    armv7l) archive_arch=arm32 ;;
    x86_64) archive_arch=x86_64 ;;
    *)
      echo "Spotify Soloist has no configured build for $architecture." >&2
      return 1
      ;;
  esac
  archive_url="https://soloist-builds.spotifycdn.com/soloist_release_${archive_arch}.tar.gz"
  archive_file="$(mktemp /tmp/lumistripe-soloist-update.XXXXXX.tar.gz)"
  extract_dir="$(mktemp -d /tmp/lumistripe-soloist-update.XXXXXX)"
  curl --fail --silent --show-error --location --retry 3 --retry-delay 2 \
    "$archive_url" -o "$archive_file"
  tar -xzf "$archive_file" -C "$extract_dir"
  [[ -x "$extract_dir/soloist" ]] || {
    echo "Spotify Soloist archive did not contain an executable soloist file." >&2
    rm -f -- "$archive_file"
    rm -rf -- "$extract_dir"
    return 1
  }
  install -o root -g root -m 755 "$extract_dir/soloist" "$SOLOIST_BIN"
  rm -f -- "$archive_file"
  rm -rf -- "$extract_dir"
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
      "$PROJECT_DIR/deploy/lumistripe-spotify.env.example" "$SPOTIFY_ENV_FILE"
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

sync_dependencies() {
  pushd "$PROJECT_DIR" >/dev/null
  as_user "$UV_BIN" python install "$PYTHON_VERSION"
  as_user_with_env \
    UV_PYTHON_PREFERENCE=only-managed \
    "$UV_BIN" sync --locked --python "$PYTHON_VERSION" --no-dev --extra hardware-gain
  popd >/dev/null
}

build_frontend() {
  pushd "$PROJECT_DIR/apps/lumistripe-web/frontend" >/dev/null
  as_user "$BUN_BIN" install --frozen-lockfile
  as_user "$BUN_BIN" run build
  popd >/dev/null
}

install_wireplumber_config() {
  install -d -o "$PROJECT_USER" -g "$PROJECT_GROUP" -m 755 "$WIREPLUMBER_DIR"
  install -o "$PROJECT_USER" -g "$PROJECT_GROUP" -m 644 \
    "$PROJECT_DIR/deploy/90-lumistripe-bluetooth.conf" "$WIREPLUMBER_FILE"
  install -d -o "$PROJECT_USER" -g "$PROJECT_GROUP" -m 755 "$PIPEWIRE_DIR"
  install -o "$PROJECT_USER" -g "$PROJECT_GROUP" -m 644 \
    "$PROJECT_DIR/deploy/90-lumistripe-spotify.conf" "$SPOTIFY_PIPEWIRE_FILE"
}

render_service_unit() {
  local rendered_file
  local project_replacement
  local home_replacement
  local user_replacement
  local group_replacement
  local uid_replacement
  rendered_file="$(mktemp /tmp/lumistripe-service-update.XXXXXX)"
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
    "$PROJECT_DIR/deploy/lumistripe-web.service" > "$rendered_file"
  if grep -q '@LUMI_[A-Z_]*@' "$rendered_file"; then
    rm -f -- "$rendered_file"
    echo "The systemd service template contains unresolved placeholders." >&2
    exit 1
  fi
  install -o root -g root -m 644 "$rendered_file" "$SERVICE_FILE"
  rm -f -- "$rendered_file"
}

render_spotify_service_unit() {
  local rendered_file
  local project_replacement
  local home_replacement
  local user_replacement
  local group_replacement
  local uid_replacement
  rendered_file="$(mktemp /tmp/lumistripe-spotify-service-update.XXXXXX)"
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
    "$PROJECT_DIR/deploy/lumistripe-spotify.service" > "$rendered_file"
  if grep -q '@LUMI_[A-Z_]*@' "$rendered_file"; then
    rm -f -- "$rendered_file"
    echo "The Spotify systemd service template contains unresolved placeholders." >&2
    return 1
  fi
  install -o root -g root -m 644 "$rendered_file" "$SPOTIFY_SERVICE_FILE"
  rm -f -- "$rendered_file"
}

refresh_deployment_files() {
  install_wireplumber_config
  render_service_unit
  render_spotify_service_unit
  systemctl daemon-reload

  install -d -m 755 /etc/nginx/sites-available /etc/nginx/sites-enabled
  install -o root -g root -m 644 \
    "$PROJECT_DIR/deploy/nginx/led-controller.conf" "$NGINX_AVAILABLE"
  ln -sfn "$NGINX_AVAILABLE" /etc/nginx/sites-enabled/led-controller
  nginx -t
  systemctl reload nginx.service

  if [[ -S "$SERVICE_RUNTIME_DIR/bus" ]]; then
    as_user_with_env \
      XDG_RUNTIME_DIR="$SERVICE_RUNTIME_DIR" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=$SERVICE_RUNTIME_DIR/bus" \
      systemctl --user restart pipewire pipewire-pulse wireplumber
  fi
}

rollback() {
  [[ -n "$PREVIOUS_REF" && "$ROLLING_BACK" -eq 0 ]] || return 0
  ROLLING_BACK=1
  echo "Update failed; rolling back to $PREVIOUS_REF..." >&2
  git_user switch --detach "$PREVIOUS_REF" || true
  sync_dependencies || true
  build_frontend || true
  refresh_deployment_files || true
  systemctl restart lumistripe-web.service || true
  systemctl restart lumistripe-spotify.service || true
}
trap rollback ERR

STATUS=$(git_user status --porcelain)
[[ -z "$STATUS" ]] || { echo "Working tree is dirty; commit or stash changes before updating." >&2; exit 1; }
PREVIOUS_REF=$(git_user rev-parse HEAD)
git_user fetch --prune --tags origin
TARGET_COMMIT=$(git_user rev-parse --verify "${TARGET_REF}^{commit}")
git_user switch --detach "$TARGET_COMMIT"

install_audio_bridge_packages
install_spotify_soloist
create_spotify_environment_file
sync_dependencies
build_frontend
refresh_deployment_files
if spotify_environment_ready; then
  systemctl enable --now lumistripe-spotify.service
fi
systemctl restart lumistripe-web.service
systemctl is-active --quiet lumistripe-web.service

for _ in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:8000/api/health >/dev/null; then
    echo "LumiStripe updated to $(git_user rev-parse --short HEAD)."
    trap - ERR
    exit 0
  fi
  sleep 1
done
echo "Service did not become healthy in time." >&2
exit 1
