#!/usr/bin/env bash
set -Eeuo pipefail

# Remove LumiStripe's appliance artifacts while leaving shared operating-system
# packages, services, toolchains, and the project checkout in place.

umask 022

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
SERVICE_USER="${LUMI_SERVICE_USER:-${SUDO_USER:-}}"
ASSUME_YES=0

ENV_DIR=/etc/lumistripe
ENV_FILE="$ENV_DIR/lumistripe-web.env"
SPOTIFY_ENV_FILE="$ENV_DIR/lumistripe-spotify.env"
SERVICE_FILE=/etc/systemd/system/lumistripe-web.service
SPOTIFY_SERVICE_FILE=/etc/systemd/system/lumistripe-spotify.service
SOLOIST_BIN=/usr/local/bin/soloist
STATE_DIR=/var/lib/lumistripe
STATE_FILE="$STATE_DIR/install-state"
NGINX_AVAILABLE=/etc/nginx/sites-available/led-controller
NGINX_ENABLED=/etc/nginx/sites-enabled/led-controller
NGINX_DEFAULT=/etc/nginx/sites-enabled/default
NGINX_DEFAULT_TARGET=/etc/nginx/sites-available/default
TLS_DIR=/etc/nginx/certs
TLS_CERT="$TLS_DIR/led.controller.pem"
TLS_KEY="$TLS_DIR/led.controller-key.pem"

usage() {
  cat <<'EOF'
Usage: sudo ./deploy/uninstall.sh [--yes]

Removes LumiStripe services, configuration, generated dependencies, Soloist,
and LumiStripe's Nginx/TLS artifacts. Shared OS packages, the service account,
uv, Bun, mkcert, its CA, and the project checkout are preserved.
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

for argument in "$@"; do
  case "$argument" in
    --yes|-y)
      ASSUME_YES=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $argument"
      ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  die "run this uninstaller as root (for example: sudo $0)"
fi
[[ -d "$PROJECT_DIR" ]] || die "project directory not found: $PROJECT_DIR"
[[ "$PROJECT_DIR" != / ]] || die "refusing to use / as the project directory"
command -v systemctl >/dev/null 2>&1 || die "systemd is required on the target Pi"
command -v runuser >/dev/null 2>&1 || die "runuser is required"

state_service_user=""
nginx_default_removed=0
if [[ -f "$STATE_FILE" ]]; then
  state_service_user="$(awk -F= '$1 == "service_user" { print $2; exit }' "$STATE_FILE")"
  state_nginx_default_removed="$(awk -F= '$1 == "nginx_default_removed" { print $2; exit }' "$STATE_FILE")"
  [[ "$state_nginx_default_removed" == 1 ]] && nginx_default_removed=1
fi

if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == root ]]; then
  SERVICE_USER="$state_service_user"
fi
if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == root ]]; then
  SERVICE_USER="$(stat -c '%U' "$PROJECT_DIR" 2>/dev/null || true)"
fi
[[ -n "$SERVICE_USER" && "$SERVICE_USER" != root ]] || {
  die "could not determine the non-root LumiStripe service user; set LUMI_SERVICE_USER"
}
[[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || die "invalid Linux service username: $SERVICE_USER"

if id "$SERVICE_USER" >/dev/null 2>&1; then
  SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
  SERVICE_UID="$(id -u "$SERVICE_USER")"
else
  die "service user does not exist: $SERVICE_USER"
fi
[[ -n "$SERVICE_HOME" && "$SERVICE_HOME" != / ]] || die "invalid service home for $SERVICE_USER"

WIREPLUMBER_FILE="$SERVICE_HOME/.config/wireplumber/wireplumber.conf.d/90-lumistripe-bluetooth.conf"
PIPEWIRE_FILE="$SERVICE_HOME/.config/pipewire/pipewire.conf.d/90-lumistripe-spotify.conf"
USER_RUNTIME_DIR="/run/user/$SERVICE_UID"
PROJECT_STATIC_DIR="$PROJECT_DIR/apps/lumistripe-web/src/lumistripe_web/static"
PROJECT_FRONTEND_DIR="$PROJECT_DIR/apps/lumistripe-web/frontend"

spotify_data_dir=""
spotify_cache_dir=""
if [[ -f "$SPOTIFY_ENV_FILE" ]]; then
  spotify_data_dir="$(awk -F= '$1 == "LUMI_SPOTIFY_DATA_DIR" { print substr($0, index($0, "=") + 1); exit }' "$SPOTIFY_ENV_FILE")"
  spotify_cache_dir="$(awk -F= '$1 == "LUMI_SPOTIFY_CACHE_DIR" { print substr($0, index($0, "=") + 1); exit }' "$SPOTIFY_ENV_FILE")"
fi

if ((ASSUME_YES == 0)); then
  cat <<EOF
This will remove LumiStripe's installed services, configuration, Soloist,
generated project dependencies/build output, Nginx site, and TLS certificate.

It will preserve the project checkout, shared OS packages/services, the
$SERVICE_USER account, uv, Bun, mkcert and its CA, and existing backups.

EOF
  read -r -p "Continue with LumiStripe uninstall? [y/N] " confirmation
  [[ "$confirmation" =~ ^[Yy]([Ee][Ss])?$ ]] || {
    echo "Uninstall cancelled."
    exit 0
  }
fi

stop_service() {
  local service="$1"
  if systemctl is-enabled "$service" >/dev/null 2>&1 || systemctl is-active "$service" >/dev/null 2>&1; then
    echo "Stopping $service..."
    systemctl disable --now "$service" || true
  fi
  systemctl reset-failed "$service" >/dev/null 2>&1 || true
}

remove_file() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    echo "Removing $path"
    rm -f -- "$path"
  fi
}

remove_directory() {
  local path="$1"
  [[ "$path" != / && "$path" != "$PROJECT_DIR" && "$path" != "$SERVICE_HOME" ]] || {
    die "refusing to remove broad directory: $path"
  }
  if [[ -e "$path" || -L "$path" ]]; then
    echo "Removing $path"
    rm -rf -- "$path"
  fi
}

remove_empty_directory() {
  local path="$1"
  rmdir -- "$path" 2>/dev/null || true
}

remove_soloist_directory() {
  local path="$1"
  [[ -n "$path" ]] || return 0
  case "$path" in
    "$SERVICE_HOME/.local/share/soloist"|"$SERVICE_HOME/.cache/soloist")
      remove_directory "$path"
      ;;
    *)
      echo "Preserving custom Soloist path: $path" >&2
      ;;
  esac
}

restart_user_audio() {
  [[ -S "$USER_RUNTIME_DIR/bus" ]] || return 0
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus" \
    systemctl --user restart pipewire pipewire-pulse wireplumber || \
    echo "Warning: could not restart the $SERVICE_USER PipeWire session." >&2
}

stop_service lumistripe-web.service
stop_service lumistripe-spotify.service

remove_file "$SERVICE_FILE"
remove_file "$SPOTIFY_SERVICE_FILE"
systemctl daemon-reload

remove_file "$WIREPLUMBER_FILE"
remove_file "$PIPEWIRE_FILE"
remove_empty_directory "$(dirname -- "$WIREPLUMBER_FILE")"
remove_empty_directory "$(dirname -- "$(dirname -- "$WIREPLUMBER_FILE")")"
remove_empty_directory "$(dirname -- "$PIPEWIRE_FILE")"
remove_empty_directory "$(dirname -- "$(dirname -- "$PIPEWIRE_FILE")")"
restart_user_audio

remove_file "$SOLOIST_BIN"
remove_soloist_directory "$spotify_data_dir"
remove_soloist_directory "$spotify_cache_dir"

remove_file "$ENV_FILE"
remove_file "$SPOTIFY_ENV_FILE"
remove_empty_directory "$ENV_DIR"

if [[ -L "$NGINX_ENABLED" && "$(readlink -f "$NGINX_ENABLED")" == "$NGINX_AVAILABLE" ]]; then
  remove_file "$NGINX_ENABLED"
elif [[ -e "$NGINX_ENABLED" ]]; then
  echo "Preserving non-LumiStripe Nginx entry at $NGINX_ENABLED" >&2
fi
remove_file "$NGINX_AVAILABLE"
if ((nginx_default_removed == 1)) && [[ ! -e "$NGINX_DEFAULT" && ! -L "$NGINX_DEFAULT" && -e "$NGINX_DEFAULT_TARGET" ]]; then
  echo "Restoring the Debian Nginx default site link."
  ln -s "$NGINX_DEFAULT_TARGET" "$NGINX_DEFAULT"
fi
if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
  systemctl reload nginx.service >/dev/null 2>&1 || true
fi

remove_file "$TLS_CERT"
remove_file "$TLS_KEY"
remove_empty_directory "$TLS_DIR"

remove_directory "$PROJECT_DIR/.venv"
remove_directory "$PROJECT_FRONTEND_DIR/node_modules"
remove_directory "$PROJECT_FRONTEND_DIR/dist"
remove_directory "$PROJECT_STATIC_DIR"

remove_file "$STATE_FILE"
remove_empty_directory "$STATE_DIR"
remove_empty_directory /var/backups/lumistripe

echo
echo "LumiStripe uninstall completed."
echo "Shared packages, services, toolchains, user account, mkcert CA, and checkout were preserved."
