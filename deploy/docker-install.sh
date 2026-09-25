#!/usr/bin/env bash
set -Eeuo pipefail

umask 022

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
SERVICE_USER="${LUMI_SERVICE_USER:-${SUDO_USER:-}}"
TAKEOVER_SYSTEMD=0
CERT_TMP_DIR=
BLUETOOTH_CONFIG_BACKUP=0
SPOTIFY_CONFIG_BACKUP=0

# shellcheck source=deploy/docker/common.sh
source "$SCRIPT_DIR/docker/common.sh"

usage() {
  cat <<'EOF'
Usage: sudo ./deploy/docker-install.sh [--takeover-systemd]

Builds and starts the Docker Compose LumiStripe appliance. The host keeps
BlueZ, PipeWire/WirePlumber, GPIO/SPI device nodes, and the user audio
session. Existing LumiStripe systemd services are rejected unless the
explicit --takeover-systemd option is supplied.

Configuration is supplied through environment variables, including:
  LUMI_PAIRING_CODE=0427
  LUMI_SPOTIFY_API_KEY=YOUR_SOLOIST_API_KEY
  LUMI_SPI_DEVICE=/dev/spidev0.0
  LUMI_SPI_DEVICE_2=/dev/spidev1.0
  LUMI_SERVICE_USER=pi
  LUMI_PROJECT_DIR=/home/pi/lumistripe
EOF
}

cleanup() {
  if [[ -n "$CERT_TMP_DIR" && -d "$CERT_TMP_DIR" ]]; then
    rm -rf -- "$CERT_TMP_DIR"
  fi
}
trap cleanup EXIT

for argument in "$@"; do
  case "$argument" in
    --takeover-systemd)
      TAKEOVER_SYSTEMD=1
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

require_root
require_project
check_runtime_commands
resolve_service_identity
load_saved_values

check_existing_installations() {
  local systemd_found=0
  local unit
  for unit in lumistripe-web.service lumistripe-spotify.service; do
    if [[ -e "/etc/systemd/system/$unit" ]] || \
      systemctl is-enabled "$unit" >/dev/null 2>&1 || \
      systemctl is-active "$unit" >/dev/null 2>&1; then
      systemd_found=1
    fi
  done

  if ((systemd_found == 1)); then
    if ((TAKEOVER_SYSTEMD == 0)); then
      die "the systemd LumiStripe installation is present; stop/uninstall it first, or rerun with --takeover-systemd"
    fi
    echo "Taking over from the LumiStripe systemd services..."
    systemctl disable --now lumistripe-web.service lumistripe-spotify.service || true
  fi

  if systemctl is-active nginx.service >/dev/null 2>&1; then
    local lumi_site=/etc/nginx/sites-enabled/led-controller
    if ((TAKEOVER_SYSTEMD == 1)) && [[ -L "$lumi_site" ]] && \
      [[ "$(readlink -f "$lumi_site")" == "/etc/nginx/sites-available/led-controller" ]]; then
      echo "Stopping the existing LumiStripe host Nginx service..."
      systemctl disable --now nginx.service
    else
      die "Nginx is already using the host HTTPS ports; stop the host service before starting Docker"
    fi
  fi
}

install_host_packages() {
  local packages=(
    bluez
    bluetooth
    pipewire
    pipewire-pulse
    wireplumber
    libspa-0.2-bluetooth
    pipewire-alsa
    libasound2-plugins
    libportaudio2
    libnss3-tools
    curl
    ca-certificates
    git
    dbus-user-session
    util-linux
  )

  echo "Refreshing Raspberry Pi OS package lists..."
  apt-get update

  if apt-cache show mkcert >/dev/null 2>&1; then
    packages+=(mkcert)
  fi

  echo "Installing host Bluetooth/PipeWire and Docker prerequisites..."
  apt-get install --yes --no-install-recommends "${packages[@]}"
}

install_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    apt-get install --yes --no-install-recommends docker.io
  fi

  if ! docker compose version >/dev/null 2>&1; then
    local compose_package=
    local candidate
    for candidate in docker-compose-v2 docker-compose-plugin; do
      if apt-cache show "$candidate" >/dev/null 2>&1; then
        compose_package="$candidate"
        break
      fi
    done
    if [[ -n "$compose_package" ]]; then
      apt-get install --yes --no-install-recommends "$compose_package"
    fi
  fi

  if ! docker compose version >/dev/null 2>&1; then
    local version="${LUMI_DOCKER_COMPOSE_VERSION:-v2.39.4}"
    local architecture
    local asset_arch
    local plugin_dir=/usr/local/lib/docker/cli-plugins
    architecture="$(dpkg --print-architecture)"
    case "$architecture" in
      arm64) asset_arch=aarch64 ;;
      amd64) asset_arch=x86_64 ;;
      armhf|armel) asset_arch=armv7 ;;
      *) die "Docker Compose has no configured release for $architecture" ;;
    esac
    install -d -o root -g root -m 755 "$plugin_dir"
    echo "Installing Docker Compose $version..."
    curl --fail --silent --show-error --location --retry 3 --retry-delay 2 \
      "https://github.com/docker/compose/releases/download/$version/docker-compose-linux-$asset_arch" \
      --output "$plugin_dir/docker-compose"
    chmod 755 "$plugin_dir/docker-compose"
  fi

  systemctl enable --now docker.service
  docker compose version >/dev/null 2>&1 || \
    die "Docker Compose v2 is unavailable after installation"
}

install_mkcert() {
  if command -v mkcert >/dev/null 2>&1; then
    return
  fi

  local architecture
  local asset
  local version="${LUMI_MKCERT_VERSION:-v1.4.4}"
  local installer_file
  architecture="$(dpkg --print-architecture)"
  case "$architecture" in
    arm64) asset=linux-arm64 ;;
    armhf|armel) asset=linux-arm ;;
    amd64) asset=linux-amd64 ;;
    i386) asset=linux-386 ;;
    ppc64el) asset=linux-ppc64le ;;
    s390x) asset=linux-s390x ;;
    *) die "mkcert has no configured release for $architecture" ;;
  esac
  installer_file="$(mktemp /tmp/lumistripe-mkcert.XXXXXX)"
  curl --fail --silent --show-error --location --retry 3 --retry-delay 2 \
    "https://github.com/FiloSottile/mkcert/releases/download/$version/mkcert-$version-$asset" \
    --output "$installer_file"
  install -o root -g root -m 755 "$installer_file" /usr/local/bin/mkcert
  rm -f -- "$installer_file"
}

enable_spi() {
  if command -v raspi-config >/dev/null 2>&1; then
    echo "Enabling Raspberry Pi SPI..."
    raspi-config nonint do_spi 0
  else
    echo "Warning: raspi-config is unavailable; enable SPI manually before using hardware output." >&2
  fi
}

start_bluetooth() {
  systemctl enable --now bluetooth.service
  if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock bluetooth || true
  fi
  bluetoothctl power on >/dev/null 2>&1 || \
    echo "Warning: Bluetooth controller could not be powered on yet." >&2
}

prepare_state() {
  install -d -o root -g root -m 755 "$DOCKER_STATE_DIR"
  install -d -o root -g root -m 755 "$(dirname -- "$DOCKER_ENV_FILE")"
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 750 \
    "$DOCKER_DATA_DIR" \
    "$DOCKER_DATA_DIR/home" \
    "$DOCKER_DATA_DIR/soloist" \
    "$DOCKER_DATA_DIR/soloist-cache"
  install -d -o root -g root -m 755 "$DOCKER_TLS_DIR"
}

create_spotify_secret() {
  if [[ -s "$DOCKER_API_KEY_FILE" ]]; then
    chmod 600 "$DOCKER_API_KEY_FILE"
    return
  fi

  local api_key="${LUMI_SPOTIFY_API_KEY:-}"
  if [[ -z "$api_key" && -t 0 ]]; then
    read -r -s -p "Spotify Soloist API key: " api_key
    echo
  fi
  [[ -n "$api_key" && "$api_key" != REPLACE_WITH_* ]] || {
    die "LUMI_SPOTIFY_API_KEY is required; provide it in the environment or enter it when prompted"
  }
  [[ "$api_key" != *$'\n'* && "$api_key" != *$'\r'* ]] || \
    die "LUMI_SPOTIFY_API_KEY must be a single-line value"

  local temporary_file
  temporary_file="$(mktemp /tmp/lumistripe-spotify-key.XXXXXX)"
  chmod 600 "$temporary_file"
  printf '%s\n' "$api_key" > "$temporary_file"
  install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 600 \
    "$temporary_file" "$DOCKER_API_KEY_FILE"
  rm -f -- "$temporary_file"
}

create_tls_certificate() {
  install -d -o root -g root -m 755 "$DOCKER_TLS_DIR"
  local certificate="$DOCKER_TLS_DIR/led.controller.pem"
  local key="$DOCKER_TLS_DIR/led.controller-key.pem"

  if [[ ! -e "$certificate" && ! -e "$key" ]]; then
    echo "Generating the local mkcert HTTPS certificate for led.controller..."
    CERT_TMP_DIR="$(mktemp -d /tmp/lumistripe-docker-certificate.XXXXXX)"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$CERT_TMP_DIR"
    local certificate_names=(led.controller localhost 127.0.0.1)
    local host_address
    local -a host_addresses=()
    read -r -a host_addresses <<< "$(hostname -I 2>/dev/null || true)"
    for host_address in "${host_addresses[@]:-}"; do
      [[ -n "$host_address" ]] && certificate_names+=("$host_address")
    done
    as_user mkcert -install
    as_user mkcert \
      -cert-file "$CERT_TMP_DIR/cert.pem" \
      -key-file "$CERT_TMP_DIR/key.pem" \
      "${certificate_names[@]}"
    install -o root -g root -m 644 "$CERT_TMP_DIR/cert.pem" "$certificate"
    install -o root -g root -m 600 "$CERT_TMP_DIR/key.pem" "$key"
  elif [[ ! -s "$certificate" || ! -s "$key" ]]; then
    die "both $certificate and $key must exist, or neither may exist"
  fi
  chmod 644 "$certificate"
  chmod 600 "$key"
}

create_compose_environment() {
  [[ "$LUMI_PAIRING_CODE" =~ ^[0-9]{4}$ ]] || {
    if [[ -z "$LUMI_PAIRING_CODE" ]]; then
      LUMI_PAIRING_CODE="$(od -An -N2 -tu2 /dev/urandom | awk '{ printf "%04d", 1000 + ($1 % 9000) }')"
      echo "Generated a LumiStripe pairing code: $LUMI_PAIRING_CODE"
    else
      die "LUMI_PAIRING_CODE must be exactly four digits"
    fi
  }

  local temporary_file
  temporary_file="$(mktemp /tmp/lumistripe-docker-env.XXXXXX)"
  {
    printf 'LUMI_PROJECT_DIR=%s\n' "$PROJECT_DIR"
    printf 'LUMI_CONTAINER_ENV_FILE=%s\n' "$DOCKER_ENV_FILE"
    printf 'LUMI_UID=%s\n' "$SERVICE_UID"
    printf 'LUMI_GID=%s\n' "$(id -g "$SERVICE_USER")"
    printf 'LUMI_GPIO_GID=%s\n' "$GPIO_GID"
    printf 'LUMI_SPI_GID=%s\n' "$SPI_GID"
    printf 'LUMI_AUDIO_GID=%s\n' "$AUDIO_GID"
    printf 'LUMI_RUNTIME_DIR=%s\n' "$USER_RUNTIME_DIR"
    printf 'LUMI_DBUS_SOCKET=/run/dbus/system_bus_socket\n'
    printf 'LUMI_HOST_DATA_DIR=%s\n' "$DOCKER_DATA_DIR"
    printf 'LUMI_HOST_TLS_DIR=%s\n' "$DOCKER_TLS_DIR"
    printf 'LUMI_HOST_API_KEY_FILE=%s\n' "$DOCKER_API_KEY_FILE"
    printf 'LUMI_PAIRING_CODE=%s\n' "$LUMI_PAIRING_CODE"
    printf 'LUMI_SETTINGS_FILE=/var/lib/lumistripe/settings.json\n'
    printf 'LUMI_SPI_DEVICE=%s\n' "$LUMI_SPI_DEVICE"
    printf 'LUMI_GPIO_CHIP=%s\n' "$LUMI_GPIO_CHIP"
    [[ -n "${LUMI_SPI_DEVICE_2:-}" ]] && printf 'LUMI_SPI_DEVICE_2=%s\n' "$LUMI_SPI_DEVICE_2"
    [[ -n "${LUMI_GPIOMEM_DEVICE:-}" ]] && printf 'LUMI_GPIOMEM_DEVICE=%s\n' "$LUMI_GPIOMEM_DEVICE"
    printf 'LUMI_SPOTIFY_API_KEY_FILE=/run/secrets/spotify_api_key\n'
    printf 'LUMI_SPOTIFY_DEVICE_NAME=%s\n' "$LUMI_SPOTIFY_DEVICE_NAME"
    printf 'LUMI_SPOTIFY_WS=127.0.0.1:9090\n'
    printf 'LUMI_SPOTIFY_PIPEWIRE_DEVICE=lumistripe_spotify\n'
    printf 'LUMI_SPOTIFY_DATA_DIR=/var/lib/lumistripe/soloist\n'
    printf 'LUMI_SPOTIFY_CACHE_DIR=/var/lib/lumistripe/soloist-cache\n'
    printf 'LUMI_IMAGE=%s\n' "$LUMI_IMAGE"
    printf 'LUMI_NGINX_IMAGE=%s\n' "$LUMI_NGINX_IMAGE"
  } > "$temporary_file"
  install -o root -g root -m 600 "$temporary_file" "$DOCKER_ENV_FILE"
  rm -f -- "$temporary_file"
}

build_and_start() {
  echo "Validating Docker Compose configuration..."
  compose config --quiet
  echo "Building the LumiStripe ARM64-capable image..."
  compose build --pull
  echo "Starting LumiStripe containers..."
  compose up --detach --remove-orphans

  for _ in {1..60}; do
    if curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null; then
      return 0
    fi
    sleep 1
  done
  compose ps
  die "LumiStripe web container did not become healthy in time"
}

check_existing_installations
install_host_packages
install_docker
install_mkcert
ensure_groups
enable_spi
prepare_state
create_spotify_secret
create_tls_certificate
refresh_host_audio_config
start_bluetooth
restart_host_audio
create_compose_environment
write_install_state
build_and_start

echo
echo "LumiStripe Docker deployment installed successfully."
echo "Dashboard: https://led.controller/"
echo "Trust this mkcert CA on clients: $(as_user mkcert -CAROOT)/rootCA.pem"
echo "Use 'sudo ./deploy/docker-update.sh' after changing the checkout."
