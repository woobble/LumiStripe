#!/usr/bin/env bash
set -Eeuo pipefail

# This script is intentionally self-contained. It is the only command needed
# to turn a Raspberry Pi OS Trixie checkout into a LumiStripe appliance.

umask 022

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
SERVICE_USER="${LUMI_SERVICE_USER:-${SUDO_USER:-}}"
PYTHON_VERSION="${LUMI_PYTHON_VERSION:-3.12}"

ENV_DIR=/etc/lumistripe
ENV_FILE="$ENV_DIR/lumistripe-web.env"
SERVICE_FILE=/etc/systemd/system/lumistripe-web.service
WIREPLUMBER_DIR=
WIREPLUMBER_FILE=
NGINX_AVAILABLE=/etc/nginx/sites-available/led-controller
NGINX_ENABLED=/etc/nginx/sites-enabled/led-controller
NGINX_DEFAULT=/etc/nginx/sites-enabled/default
TLS_DIR=/etc/nginx/certs
TLS_CERT="$TLS_DIR/led.controller.pem"
TLS_KEY="$TLS_DIR/led.controller-key.pem"
CERT_TMP_DIR=
MKCERT_CA_ROOT=

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root (for example: sudo $0)." >&2
  exit 1
fi
[[ -d "$PROJECT_DIR" ]] || {
  echo "Project directory not found: $PROJECT_DIR" >&2
  exit 1
}

if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == root ]]; then
  # When invoked by a root shell, use the existing owner of the checkout. A
  # root-owned checkout must provide LUMI_SERVICE_USER explicitly so the
  # installer never silently runs the appliance as root.
  SERVICE_USER="$(stat -c '%U' "$PROJECT_DIR" 2>/dev/null || true)"
fi

[[ -n "$SERVICE_USER" && "$SERVICE_USER" != root ]] || {
  echo "Set LUMI_SERVICE_USER to a non-root account for this installation." >&2
  exit 1
}
[[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || {
  echo "Invalid Linux service username: $SERVICE_USER" >&2
  exit 1
}

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Creating service user $SERVICE_USER..."
  useradd --create-home --shell /bin/bash "$SERVICE_USER"
fi

SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
SERVICE_UID="$(id -u "$SERVICE_USER")"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
[[ -n "$SERVICE_HOME" && -d "$SERVICE_HOME" ]] || {
  echo "Could not resolve the home directory for $SERVICE_USER." >&2
  exit 1
}

UV_BIN="$SERVICE_HOME/.local/bin/uv"
BUN_BIN="$SERVICE_HOME/.bun/bin/bun"
WIREPLUMBER_DIR="$SERVICE_HOME/.config/wireplumber/wireplumber.conf.d"
WIREPLUMBER_FILE="$WIREPLUMBER_DIR/90-lumistripe-bluetooth.conf"
USER_RUNTIME_DIR="/run/user/$SERVICE_UID"

for required_file in \
  "$PROJECT_DIR/deploy/lumistripe-web.service" \
  "$PROJECT_DIR/deploy/90-lumistripe-bluetooth.conf" \
  "$PROJECT_DIR/deploy/lumistripe-web.env.example" \
  "$PROJECT_DIR/deploy/nginx/led-controller.conf"; do
  [[ -f "$required_file" ]] || {
    echo "Required deployment file is missing: $required_file" >&2
    exit 1
  }
done

command -v apt-get >/dev/null 2>&1 || {
  echo "This installer requires apt-get (Raspberry Pi OS/Debian)." >&2
  exit 1
}
command -v systemctl >/dev/null 2>&1 || {
  echo "systemd is required on the target Pi." >&2
  exit 1
}

cleanup() {
  if [[ -n "$CERT_TMP_DIR" && -d "$CERT_TMP_DIR" ]]; then
    rm -rf -- "$CERT_TMP_DIR"
  fi
}
trap cleanup EXIT

as_user() {
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" \
    PATH="$SERVICE_HOME/.bun/bin:$SERVICE_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$@"
}

as_user_with_env() {
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" \
    PATH="$SERVICE_HOME/.bun/bin:$SERVICE_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$@"
}

install_system_packages() {
  # Python build tools are included because the native LumiStripe extensions
  # and hardware-gain dependency may not have an ARM wheel for every Pi image.
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
    libasound2-dev
    libgpiod-dev
    nginx
    libnss3-tools
    curl
    ca-certificates
    git
    unzip
    build-essential
    pkg-config
    python3
    python3-dev
    python3-venv
    dbus-user-session
    util-linux
  )

  echo "Refreshing Raspberry Pi OS package lists..."
  apt-get update

  local gpiod_runtime_package=
  for candidate in libgpiod3 libgpiod2; do
    if apt-cache show "$candidate" >/dev/null 2>&1; then
      gpiod_runtime_package="$candidate"
      break
    fi
  done
  [[ -n "$gpiod_runtime_package" ]] || {
    echo "Could not find a supported libgpiod runtime package." >&2
    exit 1
  }
  packages+=("$gpiod_runtime_package")

  # Raspberry Pi OS images normally ship raspi-config, but keeping this
  # conditional also supports minimal Debian-based Pi images.
  if apt-cache show raspi-config >/dev/null 2>&1; then
    packages+=(raspi-config)
  fi

  if apt-cache show mkcert >/dev/null 2>&1; then
    packages+=(mkcert)
  fi

  echo "Installing Raspberry Pi OS packages..."
  apt-get install --yes --no-install-recommends "${packages[@]}"
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
    *)
      echo "No mkcert release is configured for dpkg architecture $architecture." >&2
      exit 1
      ;;
  esac

  echo "Installing mkcert ($version) from the official release..."
  installer_file="$(mktemp /tmp/lumistripe-mkcert.XXXXXX)"
  curl --fail --silent --show-error --location --retry 3 --retry-delay 2 \
    "https://github.com/FiloSottile/mkcert/releases/download/$version/mkcert-$version-$asset" \
    -o "$installer_file"
  install -o root -g root -m 755 "$installer_file" /usr/local/bin/mkcert
  rm -f -- "$installer_file"
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
}

enable_spi() {
  if command -v raspi-config >/dev/null 2>&1; then
    echo "Enabling Raspberry Pi SPI..."
    raspi-config nonint do_spi 0
  else
    echo "Warning: raspi-config is unavailable; enable SPI manually before using hardware output." >&2
  fi
}

download_and_run_as_user() {
  local url="$1"
  local shell_name="$2"
  local installer_file
  installer_file="$(mktemp /tmp/lumistripe-tool-installer.XXXXXX)"

  curl --fail --silent --show-error --location --retry 3 --retry-delay 2 \
    "$url" -o "$installer_file"
  chown "$SERVICE_USER:$SERVICE_GROUP" "$installer_file"
  chmod 700 "$installer_file"

  if ! as_user_with_env "$shell_name" "$installer_file"; then
    rm -f -- "$installer_file"
    return 1
  fi
  rm -f -- "$installer_file"
}

install_toolchains() {
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 \
    "$SERVICE_HOME/.local/bin" "$SERVICE_HOME/.bun"

  if [[ ! -x "$UV_BIN" ]]; then
    echo "Installing uv for $SERVICE_USER..."
    download_and_run_as_user "https://astral.sh/uv/install.sh" /bin/sh
  fi
  [[ -x "$UV_BIN" ]] || {
    echo "uv installation did not produce $UV_BIN." >&2
    exit 1
  }

  if [[ ! -x "$BUN_BIN" ]]; then
    echo "Installing Bun for $SERVICE_USER..."
    download_and_run_as_user "https://bun.sh/install" /bin/bash
  fi
  [[ -x "$BUN_BIN" ]] || {
    echo "Bun installation did not produce $BUN_BIN. " \
      "This may be an unsupported 32-bit Pi image." >&2
    exit 1
  }
}

install_project_dependencies() {
  echo "Installing Python dependencies with uv (Python $PYTHON_VERSION)..."
  pushd "$PROJECT_DIR" >/dev/null
  as_user "$UV_BIN" python install "$PYTHON_VERSION"
  as_user_with_env \
    UV_PYTHON_PREFERENCE=only-managed \
    "$UV_BIN" sync --locked --python "$PYTHON_VERSION" --no-dev --extra hardware-gain

  echo "Installing and building the frontend with Bun..."
  pushd "$PROJECT_DIR/apps/lumistripe-web/frontend" >/dev/null
  as_user "$BUN_BIN" install --frozen-lockfile
  as_user "$BUN_BIN" run build
  popd >/dev/null
  popd >/dev/null
}

create_environment_file() {
  install -d -m 755 "$ENV_DIR" /var/backups/lumistripe

  if [[ ! -e "$ENV_FILE" ]]; then
    local pairing_code="${LUMI_PAIRING_CODE:-}"
    if [[ -z "$pairing_code" ]]; then
      pairing_code="$(od -An -N2 -tu2 /dev/urandom | awk '{ printf "%04d", 1000 + ($1 % 9000) }')"
      echo "Generated a LumiStripe pairing code: $pairing_code"
    fi
    [[ "$pairing_code" =~ ^[0-9]{4}$ ]] || {
      echo "LUMI_PAIRING_CODE must be exactly four digits." >&2
      exit 1
    }
    install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 600 \
      "$PROJECT_DIR/deploy/lumistripe-web.env.example" "$ENV_FILE"
    sed -i "s/^LUMI_PAIRING_CODE=.*/LUMI_PAIRING_CODE=$pairing_code/" "$ENV_FILE"
  fi

  chown "$SERVICE_USER:$SERVICE_GROUP" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  local pairing_code
  pairing_code="$(awk -F= '$1 == "LUMI_PAIRING_CODE" { print $2; exit }' "$ENV_FILE")"
  [[ "$pairing_code" =~ ^[0-9]{4}$ ]] || {
    echo "LUMI_PAIRING_CODE must be exactly four digits in $ENV_FILE." >&2
    exit 1
  }
}

create_tls_certificate() {
  install -d -o root -g root -m 700 "$TLS_DIR"

  if [[ ! -e "$TLS_CERT" && ! -e "$TLS_KEY" ]]; then
    echo "Generating the local mkcert HTTPS certificate for led.controller..."
    CERT_TMP_DIR="$(mktemp -d /tmp/lumistripe-certificate.XXXXXX)"
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
    install -o root -g root -m 644 "$CERT_TMP_DIR/cert.pem" "$TLS_CERT"
    install -o root -g root -m 600 "$CERT_TMP_DIR/key.pem" "$TLS_KEY"
  elif [[ ! -s "$TLS_CERT" || ! -s "$TLS_KEY" ]]; then
    echo "Both $TLS_CERT and $TLS_KEY must exist, or neither may exist." >&2
    exit 1
  fi

  chown root:root "$TLS_CERT" "$TLS_KEY"
  chmod 644 "$TLS_CERT"
  chmod 600 "$TLS_KEY"
  MKCERT_CA_ROOT="$(as_user mkcert -CAROOT)"
}

render_service_unit() {
  local rendered_file
  local project_replacement
  local home_replacement
  local user_replacement
  local group_replacement
  local uid_replacement
  rendered_file="$(mktemp /tmp/lumistripe-service.XXXXXX)"
  project_replacement="$(printf '%s' "$PROJECT_DIR" | sed 's/[&|\\]/\\&/g')"
  home_replacement="$(printf '%s' "$SERVICE_HOME" | sed 's/[&|\\]/\\&/g')"
  user_replacement="$(printf '%s' "$SERVICE_USER" | sed 's/[&|\\]/\\&/g')"
  group_replacement="$(printf '%s' "$SERVICE_GROUP" | sed 's/[&|\\]/\\&/g')"
  uid_replacement="$(printf '%s' "$SERVICE_UID" | sed 's/[&|\\]/\\&/g')"

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

install_wireplumber_config() {
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 "$WIREPLUMBER_DIR"
  install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 644 \
    "$PROJECT_DIR/deploy/90-lumistripe-bluetooth.conf" "$WIREPLUMBER_FILE"
}

start_bluetooth() {
  systemctl enable --now bluetooth.service
  if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock bluetooth || true
  fi
  bluetoothctl power on >/dev/null 2>&1 || \
    echo "Warning: Bluetooth controller could not be powered on yet." >&2
}

start_user_audio() {
  echo "Starting the PipeWire/WirePlumber user session for $SERVICE_USER..."
  loginctl enable-linger "$SERVICE_USER"
  systemctl start "user-runtime-dir@$SERVICE_UID.service" || true
  systemctl start "user@$SERVICE_UID.service" || true

  for _ in {1..20}; do
    [[ -S "$USER_RUNTIME_DIR/bus" ]] && break
    sleep 1
  done
  [[ -S "$USER_RUNTIME_DIR/bus" ]] || {
    echo "The user D-Bus for $SERVICE_USER did not start at $USER_RUNTIME_DIR/bus." >&2
    echo "Run the installer from that user's login session and try again." >&2
    exit 1
  }

  as_user_with_env \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus" \
    systemctl --user enable --now pipewire pipewire-pulse wireplumber
  as_user_with_env \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus" \
    systemctl --user restart wireplumber
}

install_nginx() {
  install -d -m 755 /etc/nginx/sites-available /etc/nginx/sites-enabled
  install -o root -g root -m 644 \
    "$PROJECT_DIR/deploy/nginx/led-controller.conf" "$NGINX_AVAILABLE"
  ln -sfn "$NGINX_AVAILABLE" "$NGINX_ENABLED"

  # The bundled configuration owns port 80 as the captive-portal entrypoint.
  # Remove only Debian's stock symlink; never remove a user-created site.
  if [[ -L "$NGINX_DEFAULT" && "$(readlink -f "$NGINX_DEFAULT")" == "/etc/nginx/sites-available/default" ]]; then
    rm -f -- "$NGINX_DEFAULT"
  fi

  nginx -t
  systemctl enable --now nginx.service
}

install_system_packages
install_mkcert
ensure_groups
enable_spi

# The service user must be able to update its uv environment and the bundled
# frontend. This also makes deploy/update.sh work after a root-owned checkout.
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$PROJECT_DIR"
install_toolchains
install_project_dependencies
create_environment_file
create_tls_certificate
render_service_unit
install_wireplumber_config
start_bluetooth
start_user_audio
install_nginx

systemctl daemon-reload
systemctl enable --now lumistripe-web.service
systemctl --no-pager --full status lumistripe-web.service

echo
echo "LumiStripe deployment installed successfully."
echo "Dashboard: https://led.controller/"
echo "If SPI was just enabled, reboot the Pi before connecting the LED hardware."
echo "Trust this mkcert CA on clients: $MKCERT_CA_ROOT/rootCA.pem"
