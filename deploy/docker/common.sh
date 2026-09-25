#!/usr/bin/env bash

# Shared helpers for the Docker deployment scripts. The scripts deliberately
# keep the host audio session outside Docker and only bridge its runtime
# sockets into the LumiStripe containers.

DOCKER_SCRIPT_DIR="${DOCKER_SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)}"
PROJECT_DIR="${PROJECT_DIR:-${LUMI_PROJECT_DIR:-$(cd -- "$DOCKER_SCRIPT_DIR/../.." && pwd -P)}}"
SERVICE_USER="${SERVICE_USER:-${LUMI_SERVICE_USER:-${SUDO_USER:-}}}"
DOCKER_COMPOSE_FILE="$PROJECT_DIR/deploy/docker-compose.yml"
DOCKER_COMPOSE_SPI2_FILE="$PROJECT_DIR/deploy/docker-compose.spi2.yml"
DOCKER_COMPOSE_GPIOMEM_FILE="$PROJECT_DIR/deploy/docker-compose.gpiomem.yml"
DOCKER_ENV_FILE="${LUMI_DOCKER_ENV_FILE:-/etc/lumistripe/lumistripe-docker.env}"
DOCKER_STATE_DIR="${LUMI_DOCKER_STATE_DIR:-/var/lib/lumistripe-docker}"
DOCKER_DATA_DIR="$DOCKER_STATE_DIR/data"
DOCKER_TLS_DIR="$DOCKER_STATE_DIR/certs"
DOCKER_API_KEY_FILE="$DOCKER_STATE_DIR/spotify-api-key"
DOCKER_STATE_FILE="$DOCKER_STATE_DIR/install-state"
DOCKER_LOCK_FILE=/run/lock/lumistripe-docker.lock
DOCKER_COMPOSE_PROJECT=lumistripe

refresh_compose_paths() {
  DOCKER_COMPOSE_FILE="$PROJECT_DIR/deploy/docker-compose.yml"
  DOCKER_COMPOSE_SPI2_FILE="$PROJECT_DIR/deploy/docker-compose.spi2.yml"
  DOCKER_COMPOSE_GPIOMEM_FILE="$PROJECT_DIR/deploy/docker-compose.gpiomem.yml"
}

die() {
  echo "Error: $*" >&2
  exit 1
}

require_root() {
  [[ $EUID -eq 0 ]] || die "run this script as root (for example: sudo $0)"
}

require_project() {
  [[ -d "$PROJECT_DIR" ]] || die "project directory not found: $PROJECT_DIR"
  [[ "$PROJECT_DIR" != / ]] || die "refusing to use / as the project directory"
  [[ -f "$PROJECT_DIR/deploy/docker-compose.yml" ]] || \
    die "Docker Compose file is missing from $PROJECT_DIR"
}

resolve_service_identity() {
  local create_missing="${1:-1}"
  if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == root ]]; then
    SERVICE_USER="$(stat -c '%U' "$PROJECT_DIR" 2>/dev/null || true)"
  fi

  [[ -n "$SERVICE_USER" && "$SERVICE_USER" != root ]] || \
    die "set LUMI_SERVICE_USER to a non-root account for this installation"
  [[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || \
    die "invalid Linux service username: $SERVICE_USER"

  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    ((create_missing == 1)) || die "service user does not exist: $SERVICE_USER"
    echo "Creating service user $SERVICE_USER..."
    useradd --create-home --shell /bin/bash "$SERVICE_USER"
  fi

  SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
  SERVICE_UID="$(id -u "$SERVICE_USER")"
  SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
  [[ -n "$SERVICE_HOME" && -d "$SERVICE_HOME" ]] || \
    die "could not resolve the home directory for $SERVICE_USER"
  USER_RUNTIME_DIR="/run/user/$SERVICE_UID"
}

group_id() {
  local group="$1"
  getent group "$group" | cut -d: -f3
}

ensure_groups() {
  local group
  for group in gpio spi audio; do
    if ! getent group "$group" >/dev/null 2>&1; then
      echo "Creating missing system group $group..."
      groupadd --system "$group"
    fi
  done
  usermod --append --groups gpio,spi,audio "$SERVICE_USER"
  GPIO_GID="$(group_id gpio)"
  SPI_GID="$(group_id spi)"
  AUDIO_GID="$(group_id audio)"
}

as_user() {
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" \
    PATH="$SERVICE_HOME/.local/bin:$SERVICE_HOME/.bun/bin:/usr/local/bin:/usr/bin:/bin" \
    "$@"
}

compose_files() {
  COMPOSE_FILES=("-f" "$DOCKER_COMPOSE_FILE")
  if [[ -n "${LUMI_SPI_DEVICE_2:-}" ]]; then
    COMPOSE_FILES+=("-f" "$DOCKER_COMPOSE_SPI2_FILE")
  fi
  if [[ -n "${LUMI_GPIOMEM_DEVICE:-}" ]]; then
    COMPOSE_FILES+=("-f" "$DOCKER_COMPOSE_GPIOMEM_FILE")
  fi
  return 0
}

compose() {
  compose_files
  docker compose --project-name "$DOCKER_COMPOSE_PROJECT" \
    --env-file "$DOCKER_ENV_FILE" "${COMPOSE_FILES[@]}" "$@"
}

validate_compose() {
  local output
  if output="$(compose config 2>&1)"; then
    return 0
  fi
  echo "Docker Compose validation failed:" >&2
  printf '%s\n' "$output" >&2
  return 1
}

build_compose_image() {
  local status
  local progress="${LUMI_BUILDKIT_PROGRESS:-plain}"

  echo "Building the LumiStripe image (BuildKit progress: $progress)..."
  if BUILDKIT_PROGRESS="$progress" compose build --pull; then
    echo "LumiStripe image build completed."
    return 0
  else
    status=$?
  fi
  echo "Docker Compose image build failed (exit status $status)." >&2
  return "$status"
}

start_compose() {
  local status

  echo "Starting LumiStripe containers..."
  if compose up --detach --remove-orphans; then
    echo "LumiStripe containers started."
    return 0
  else
    status=$?
  fi
  echo "Docker Compose failed to start LumiStripe (exit status $status)." >&2
  echo "Current container status:" >&2
  compose ps >&2 || true
  return "$status"
}

read_env_value() {
  local name="$1"
  [[ -f "$DOCKER_ENV_FILE" ]] || return 1
  awk -F= -v name="$name" '$1 == name { sub(/^[^=]*=/, ""); print; exit }' \
    "$DOCKER_ENV_FILE"
}

load_saved_values() {
  if [[ -f "$DOCKER_ENV_FILE" ]]; then
    LUMI_PAIRING_CODE="${LUMI_PAIRING_CODE:-$(read_env_value LUMI_PAIRING_CODE || true)}"
    LUMI_SPI_DEVICE="${LUMI_SPI_DEVICE:-$(read_env_value LUMI_SPI_DEVICE || true)}"
    LUMI_GPIO_CHIP="${LUMI_GPIO_CHIP:-$(read_env_value LUMI_GPIO_CHIP || true)}"
    LUMI_GPIOMEM_DEVICE="${LUMI_GPIOMEM_DEVICE:-$(read_env_value LUMI_GPIOMEM_DEVICE || true)}"
    LUMI_SPI_DEVICE_2="${LUMI_SPI_DEVICE_2:-$(read_env_value LUMI_SPI_DEVICE_2 || true)}"
    LUMI_SPOTIFY_DEVICE_NAME="${LUMI_SPOTIFY_DEVICE_NAME:-$(read_env_value LUMI_SPOTIFY_DEVICE_NAME || true)}"
    LUMI_IMAGE="${LUMI_IMAGE:-$(read_env_value LUMI_IMAGE || true)}"
    LUMI_NGINX_IMAGE="${LUMI_NGINX_IMAGE:-$(read_env_value LUMI_NGINX_IMAGE || true)}"
  fi
  LUMI_SPI_DEVICE="${LUMI_SPI_DEVICE:-/dev/spidev0.0}"
  LUMI_GPIO_CHIP="${LUMI_GPIO_CHIP:-/dev/gpiochip0}"
  LUMI_SPOTIFY_DEVICE_NAME="${LUMI_SPOTIFY_DEVICE_NAME:-LumiStripe}"
  LUMI_IMAGE="${LUMI_IMAGE:-lumistripe:local}"
  LUMI_NGINX_IMAGE="${LUMI_NGINX_IMAGE:-nginx:1.29-alpine}"
}

check_runtime_commands() {
  command -v runuser >/dev/null 2>&1 || die "runuser is required"
  command -v systemctl >/dev/null 2>&1 || die "systemd is required on the target Pi"
  command -v apt-get >/dev/null 2>&1 || die "this deployment requires apt-get"
}

refresh_host_audio_config() {
  local wireplumber_dir="$SERVICE_HOME/.config/wireplumber/wireplumber.conf.d"
  local pipewire_dir="$SERVICE_HOME/.config/pipewire/pipewire.conf.d"
  local wireplumber_file="$wireplumber_dir/90-lumistripe-bluetooth.conf"
  local spotify_file="$pipewire_dir/90-lumistripe-spotify.conf"
  local backup_dir="$DOCKER_STATE_DIR/backups"

  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 \
    "$wireplumber_dir" "$pipewire_dir"
  install -d -o root -g root -m 700 "$backup_dir"

  if [[ -e "$wireplumber_file" && ! -e "$backup_dir/bluetooth.conf" ]]; then
    cp -a -- "$wireplumber_file" "$backup_dir/bluetooth.conf"
    BLUETOOTH_CONFIG_BACKUP=1
  fi
  if [[ -e "$spotify_file" && ! -e "$backup_dir/spotify.conf" ]]; then
    cp -a -- "$spotify_file" "$backup_dir/spotify.conf"
    SPOTIFY_CONFIG_BACKUP=1
  fi

  install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 644 \
    "$PROJECT_DIR/deploy/90-lumistripe-bluetooth.conf" "$wireplumber_file"
  install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 644 \
    "$PROJECT_DIR/deploy/90-lumistripe-spotify.conf" "$spotify_file"

  WIREPLUMBER_FILE="$wireplumber_file"
  SPOTIFY_PIPEWIRE_FILE="$spotify_file"
}

restart_host_audio() {
  loginctl enable-linger "$SERVICE_USER"
  systemctl start "user-runtime-dir@$SERVICE_UID.service" || true
  systemctl start "user@$SERVICE_UID.service" || true

  for _ in {1..20}; do
    [[ -S "$USER_RUNTIME_DIR/bus" ]] && break
    sleep 1
  done
  [[ -S "$USER_RUNTIME_DIR/bus" ]] || {
    echo "Warning: user D-Bus did not start at $USER_RUNTIME_DIR/bus." >&2
    echo "Start the user session for $SERVICE_USER before using audio." >&2
    return 0
  }

  as_user env \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus" \
    systemctl --user enable --now pipewire pipewire-pulse wireplumber
  as_user env \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus" \
    systemctl --user restart pipewire pipewire-pulse wireplumber
}

write_install_state() {
  local temporary_file
  temporary_file="$(mktemp /tmp/lumistripe-docker-state.XXXXXX)"
  {
    printf 'project_dir=%s\n' "$PROJECT_DIR"
    printf 'service_user=%s\n' "$SERVICE_USER"
    printf 'wireplumber_file=%s\n' "$WIREPLUMBER_FILE"
    printf 'spotify_pipewire_file=%s\n' "$SPOTIFY_PIPEWIRE_FILE"
    printf 'bluetooth_config_backup=%s\n' "${BLUETOOTH_CONFIG_BACKUP:-0}"
    printf 'spotify_config_backup=%s\n' "${SPOTIFY_CONFIG_BACKUP:-0}"
  } > "$temporary_file"
  install -o root -g root -m 600 "$temporary_file" "$DOCKER_STATE_FILE"
  rm -f -- "$temporary_file"
}

load_install_state() {
  [[ -f "$DOCKER_STATE_FILE" ]] || return 0
  local saved_project saved_user
  saved_project="$(awk -F= '$1 == "project_dir" { sub(/^[^=]*=/, ""); print; exit }' "$DOCKER_STATE_FILE")"
  saved_user="$(awk -F= '$1 == "service_user" { sub(/^[^=]*=/, ""); print; exit }' "$DOCKER_STATE_FILE")"
  [[ -n "${LUMI_PROJECT_DIR:-}" && -n "$saved_project" ]] || {
    [[ -n "$saved_project" ]] && PROJECT_DIR="$saved_project"
  }
  [[ -n "${LUMI_SERVICE_USER:-}" && -n "$saved_user" ]] || {
    [[ -n "$saved_user" ]] && SERVICE_USER="$saved_user"
  }
  refresh_compose_paths
  WIREPLUMBER_FILE="$(awk -F= '$1 == "wireplumber_file" { sub(/^[^=]*=/, ""); print; exit }' "$DOCKER_STATE_FILE")"
  SPOTIFY_PIPEWIRE_FILE="$(awk -F= '$1 == "spotify_pipewire_file" { sub(/^[^=]*=/, ""); print; exit }' "$DOCKER_STATE_FILE")"
  BLUETOOTH_CONFIG_BACKUP="$(awk -F= '$1 == "bluetooth_config_backup" { print $2; exit }' "$DOCKER_STATE_FILE")"
  SPOTIFY_CONFIG_BACKUP="$(awk -F= '$1 == "spotify_config_backup" { print $2; exit }' "$DOCKER_STATE_FILE")"
}
