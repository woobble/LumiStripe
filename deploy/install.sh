#!/usr/bin/env bash
set -Eeuo pipefail

# This script is intentionally self-contained. It is the only command needed
# to turn a Raspberry Pi OS Trixie checkout into a LumiStripe appliance.

umask 022

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
SERVICE_USER="${LUMI_SERVICE_USER:-${SUDO_USER:-}}"
DEPLOY_SNAPSHOT_DIR=/usr/local/libexec/lumistripe/deploy
DEPLOY_SOURCE_KIND=

ENV_DIR=/etc/lumistripe
ENV_FILE="$ENV_DIR/lumistripe-web.env"
SERVICE_FILE=/etc/systemd/system/lumistripe-web.service
SPOTIFY_ENV_FILE="$ENV_DIR/lumistripe-spotify.env"
SPOTIFY_SERVICE_FILE=/etc/systemd/system/lumistripe-spotify.service
STATE_DIR=/var/lib/lumistripe
STATE_FILE="$STATE_DIR/install-state"
WIREPLUMBER_DIR=
WIREPLUMBER_FILE=
NGINX_AVAILABLE=/etc/nginx/sites-available/led-controller
NGINX_ENABLED=/etc/nginx/sites-enabled/led-controller
NGINX_DEFAULT=/etc/nginx/sites-enabled/default
TLS_DIR=/etc/nginx/certs
TLS_CERT="$TLS_DIR/led.controller.pem"
TLS_KEY="$TLS_DIR/led.controller-key.pem"
MKCERT_CA_ROOT=
NGINX_DEFAULT_REMOVED=0
DEPLOY_MODE="${LUMI_DEPLOY_MODE:-binary}"
EFFECTIVE_DEPLOY_MODE="$DEPLOY_MODE"
DEPLOY_RELEASE_COMMIT=
OFFLINE_COMMIT="${LUMI_OFFLINE_COMMIT:-}"
WEB_ENV_CHANGED=0
SPOTIFY_ENV_CHANGED=0
WEB_SERVICE_FILE_CHANGED=0
SPOTIFY_SERVICE_FILE_CHANGED=0
WIREPLUMBER_CONFIG_CHANGED=0
APP_RELEASE_CHANGED=0
SOLOIST_BINARY_CHANGED=0
NGINX_CONFIG_CHANGED=0

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
  echo "Run this installer as root (for example: sudo $0)." >&2
  exit 1
fi

[[ -d "$PROJECT_DIR" ]] || {
  echo "Project directory does not exist: $PROJECT_DIR" >&2
  exit 1
}
PROJECT_DIR="$(cd -- "$PROJECT_DIR" && pwd -P)"
[[ -d "$PROJECT_DIR/deploy" ]] || {
  echo "Deployment source directory is missing: $PROJECT_DIR/deploy" >&2
  exit 1
}
if [[ "$SCRIPT_DIR" == "$PROJECT_DIR/deploy" ]]; then
  DEPLOY_SOURCE_KIND=checkout
elif [[ "$SCRIPT_DIR" == "$DEPLOY_SNAPSHOT_DIR" ]]; then
  DEPLOY_SOURCE_KIND=snapshot
else
  echo "Run the installer from its selected trusted checkout or root-owned deployment snapshot." >&2
  exit 1
fi
[[ "$PROJECT_DIR" != *$'\n'* && "$PROJECT_DIR" != *$'\r'* ]] || {
  echo "Project directory must be a single-line path." >&2
  exit 1
}

if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == root ]]; then
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
command -v runuser >/dev/null 2>&1 || {
  echo "runuser is required to verify the installer source permissions." >&2
  exit 1
}

if [[ "$DEPLOY_SOURCE_KIND" == checkout ]]; then
  # Root copies several scripts and configuration files into privileged
  # locations. Refuse writable checkouts and parents as before.
  trusted_source_users=()
  for candidate in "${SUDO_USER:-}" "$SERVICE_USER" "$(stat -c '%U' "$PROJECT_DIR" 2>/dev/null || true)"; do
    [[ -n "$candidate" && "$candidate" != root ]] || continue
    id "$candidate" >/dev/null 2>&1 || continue
    already_checked=0
    for checked_user in "${trusted_source_users[@]}"; do
      if [[ "$checked_user" == "$candidate" ]]; then
        already_checked=1
        break
      fi
    done
    if [[ "$already_checked" -eq 0 ]]; then
      trusted_source_users+=("$candidate")
    fi
  done
  ((${#trusted_source_users[@]} > 0)) || {
    echo "Run through sudo from an existing non-root account or create the service account first." >&2
    exit 1
  }
  for trusted_user in "${trusted_source_users[@]}"; do
    source_path="$PROJECT_DIR"
    while :; do
      source_mode="$(stat -c '%a' -- "$source_path")" || exit 1
      if (( (8#$source_mode & 0022) != 0 )); then
        echo "Installer source path is group- or world-writable: $source_path" >&2
        exit 1
      fi
      if runuser -u "$trusted_user" -- test -w "$source_path"; then
        echo "Installer source path is writable by $trusted_user: $source_path" >&2
        echo "Copy a reviewed checkout under an administrator-controlled directory and retry." >&2
        exit 1
      fi
      [[ "$source_path" == / ]] && break
      source_path="$(dirname -- "$source_path")"
    done
    while IFS= read -r -d '' source_path; do
      [[ ! -L "$source_path" ]] || {
        echo "Installer deployment source must not contain symlinks: $source_path" >&2
        exit 1
      }
      source_mode="$(stat -c '%a' -- "$source_path")" || exit 1
      if (( (8#$source_mode & 0022) != 0 )); then
        echo "Installer deployment source is group- or world-writable: $source_path" >&2
        exit 1
      fi
      if runuser -u "$trusted_user" -- test -w "$source_path"; then
        echo "Installer deployment source is writable by $trusted_user: $source_path" >&2
        echo "Copy a reviewed checkout under an administrator-controlled directory and retry." >&2
        exit 1
      fi
    done < <(find -P "$PROJECT_DIR/deploy" -print0)
  done
else
  for source_path in /usr /usr/local /usr/local/libexec \
    /usr/local/libexec/lumistripe "$DEPLOY_SNAPSHOT_DIR"; do
    [[ -d "$source_path" && ! -L "$source_path" ]] || {
      echo "Trusted deployment snapshot directory is missing or unsafe: $source_path" >&2
      exit 1
    }
    [[ "$(stat -c '%u' -- "$source_path")" == 0 ]] &&
      (( (8#$(stat -c '%a' -- "$source_path") & 0022) == 0 )) || {
      echo "Trusted deployment snapshot directory is not protected: $source_path" >&2
      exit 1
    }
  done
  while IFS= read -r -d '' source_path; do
    [[ ! -L "$source_path" ]] || {
      echo "Trusted deployment snapshot must not contain symlinks: $source_path" >&2
      exit 1
    }
    source_metadata="$(stat -c '%u:%a' -- "$source_path")" || exit 1
    [[ "${source_metadata%%:*}" == 0 ]] &&
      (( (8#${source_metadata#*:} & 0022) == 0 )) || {
      echo "Trusted deployment snapshot file is not protected: $source_path" >&2
      exit 1
    }
  done < <(find -P "$SCRIPT_DIR" -print0)
fi

# shellcheck source=deploy/update-support.sh
source "$SCRIPT_DIR/update-support.sh"
# shellcheck source=deploy/package-provisioning.sh
source "$SCRIPT_DIR/package-provisioning.sh"
# shellcheck source=deploy/timing.sh
source "$SCRIPT_DIR/timing.sh"
PROJECT_DIR="$(canonical_project_directory "$PROJECT_DIR")" || exit 1
resolve_web_backend "${LUMI_WEB_BACKEND:-}" "$ENV_FILE" >/dev/null || exit 1
migrate_legacy_rust_deploy_root || exit 1
ensure_current_release_static || exit 1

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Creating service user $SERVICE_USER..."
  useradd --create-home --shell /bin/bash "$SERVICE_USER"
fi

SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
SERVICE_UID="$(id -u "$SERVICE_USER")"
SERVICE_GID="$(id -g "$SERVICE_USER")"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
[[ -n "$SERVICE_HOME" && -d "$SERVICE_HOME" ]] || {
  echo "Could not resolve the home directory for $SERVICE_USER." >&2
  exit 1
}

BUN_BIN="$SERVICE_HOME/.bun/bin/bun"
WIREPLUMBER_DIR="$SERVICE_HOME/.config/wireplumber/wireplumber.conf.d"
WIREPLUMBER_FILE="$WIREPLUMBER_DIR/90-lumistripe-bluetooth.conf"
PIPEWIRE_DIR="$SERVICE_HOME/.config/pipewire/pipewire.conf.d"
SPOTIFY_PIPEWIRE_FILE="$PIPEWIRE_DIR/90-lumistripe-spotify.conf"
USER_RUNTIME_DIR="/run/user/$SERVICE_UID"

for required_file in \
  "$SCRIPT_DIR/lumistripe-web.service" \
  "$PROJECT_DIR/deploy/lumistripe-web-launcher.sh" \
  "$PROJECT_DIR/deploy/build-rust-backend.sh" \
  "$SCRIPT_DIR/release_artifact.py" \
  "$SCRIPT_DIR/install-github-cli.sh" \
  "$SCRIPT_DIR/rust-toolchain.sh" \
  "$SCRIPT_DIR/lumistripe-spotify.service" \
  "$SCRIPT_DIR/90-lumistripe-bluetooth.conf" \
  "$SCRIPT_DIR/90-lumistripe-spotify.conf" \
  "$SCRIPT_DIR/lumistripe-web.env.example" \
  "$SCRIPT_DIR/lumistripe-spotify.env.example" \
  "$SCRIPT_DIR/lumistripe-network" \
  "$SCRIPT_DIR/lumistripe-network.sudoers" \
  "$SCRIPT_DIR/lumistripe-spotify-service.sudoers" \
  "$SCRIPT_DIR/nginx/led-controller.conf" \
  "$SCRIPT_DIR/tls.sh" \
  "$SCRIPT_DIR/soloist.sh" \
  "$SCRIPT_DIR/package-provisioning.sh" \
  "$SCRIPT_DIR/timing.sh"; do
  [[ -f "$required_file" ]] || {
    echo "Required deployment file is missing: $required_file" >&2
    exit 1
  }
done

# shellcheck source=deploy/tls.sh
source "$SCRIPT_DIR/tls.sh"
# shellcheck source=deploy/soloist.sh
source "$SCRIPT_DIR/soloist.sh"
# shellcheck source=deploy/rust-toolchain.sh
source "$SCRIPT_DIR/rust-toolchain.sh"

command -v apt-get >/dev/null 2>&1 || {
  echo "This installer requires apt-get (Debian, Raspberry Pi OS, or Ubuntu)." >&2
  exit 1
}
command -v systemctl >/dev/null 2>&1 || {
  echo "systemd is required on the target Pi." >&2
  exit 1
}

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
  local packages=(
    bluez bluetooth pipewire pipewire-pulse pulseaudio-utils wireplumber
    libspa-0.2-bluetooth pipewire-alsa libasound2-plugins libportaudio2
    libatomic1 libdbus-1-3 libpipewire-0.3-0 libpulse0 libspa-0.2-modules
    network-manager iw nginx libnss3-tools curl ca-certificates git unzip
    dbus-user-session util-linux python3-minimal
  )
  local -a missing=()
  local package candidate
  local alsa_runtime_package=
  local gpiod_runtime_package=
  local needs_install=0

  for package in "${packages[@]}"; do
    if ! package_is_satisfied "$package"; then
      missing+=("$package")
      needs_install=1
    fi
  done
  for candidate in libasound2t64 libasound2; do
    if package_is_satisfied "$candidate"; then
      alsa_runtime_package="$candidate"
      break
    fi
  done
  for candidate in libgpiod3 libgpiod2; do
    if package_is_satisfied "$candidate"; then
      gpiod_runtime_package="$candidate"
      break
    fi
  done

  if [[ -n "$OFFLINE_COMMIT" ]]; then
    [[ -n "$alsa_runtime_package" ]] || missing+=("libasound2t64 or libasound2")
    [[ -n "$gpiod_runtime_package" ]] || missing+=("libgpiod3 or libgpiod2")
    command -v mkcert >/dev/null 2>&1 || missing+=(mkcert)
    if ((${#missing[@]} > 0)); then
      echo "Offline installation requires these preinstalled packages/tools: ${missing[*]}" >&2
      return 1
    fi
    echo "Offline mode: required system packages are already installed; skipping APT."
    return 0
  fi

  if [[ -z "$alsa_runtime_package" || -z "$gpiod_runtime_package" ]]; then
    needs_install=1
  fi
  if (( needs_install )); then
    echo "Refreshing apt package lists because runtime packages are missing..."
    apt_update_for_install
    if [[ -z "$alsa_runtime_package" ]]; then
      for candidate in libasound2t64 libasound2; do
        if apt-cache show "$candidate" >/dev/null 2>&1; then
          alsa_runtime_package="$candidate"
          break
        fi
      done
    fi
    if [[ -z "$gpiod_runtime_package" ]]; then
      for candidate in libgpiod3 libgpiod2; do
        if apt-cache show "$candidate" >/dev/null 2>&1; then
          gpiod_runtime_package="$candidate"
          break
        fi
      done
    fi
  fi
  [[ -n "$alsa_runtime_package" ]] || {
    echo "Could not find a supported ALSA runtime package." >&2
    return 1
  }
  [[ -n "$gpiod_runtime_package" ]] || {
    echo "Could not find a supported libgpiod runtime package." >&2
    return 1
  }
  packages+=("$alsa_runtime_package" "$gpiod_runtime_package")

  # raspi-config and the distro mkcert package remain optional on minimal
  # Debian and Ubuntu images. Do not refresh apt only to look for either one.
  if (( LUMI_APT_LISTS_UPDATED )); then
    if apt-cache show raspi-config >/dev/null 2>&1; then
      packages+=(raspi-config)
    fi
    if apt-cache show mkcert >/dev/null 2>&1; then
      packages+=(mkcert)
    fi
  fi

  missing=()
  for package in "${packages[@]}"; do
    if ! package_is_satisfied "$package"; then
      missing+=("$package")
    fi
  done
  if ((${#missing[@]} == 0)); then
    echo "All required runtime packages are installed; skipping APT."
    return 0
  fi
  echo "Installing missing runtime packages: ${missing[*]}"
  apt-get install --yes --no-install-recommends "${missing[@]}"
}

install_source_build_packages() {
  install_missing_apt_packages "Rust source-build dependencies" \
    build-essential pkg-config libasound2-dev libclang-dev libdbus-1-dev \
    libpipewire-0.3-dev libpulse-dev libgpiod-dev
}

install_release_verifier() {
  "$SCRIPT_DIR/install-github-cli.sh"
}

install_mkcert() {
  local existing_version=
  local version="${LUMI_MKCERT_VERSION:-v1.4.4}"
  local minimum_version="${version#v}"
  if command -v mkcert >/dev/null 2>&1; then
    existing_version="$(mkcert -version 2>/dev/null | sed -nE 's/^[^0-9]*([0-9]+\.[0-9]+(\.[0-9]+)?).*$/\1/p' | head -n 1)"
    if version_at_least "$existing_version" "$minimum_version"; then
      echo "Reusing mkcert $existing_version."
      return 0
    fi
    echo "The installed mkcert version is unsupported; installing $version."
  fi
  if [[ -n "$OFFLINE_COMMIT" ]]; then
    echo "Offline installation requires mkcert $minimum_version or newer to be installed already." >&2
    return 1
  fi

  local architecture
  local asset
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
  curl --disable --proto '=https' --proto-redir '=https' \
    --fail --silent --show-error --location --retry 3 --retry-delay 2 \
    "https://github.com/FiloSottile/mkcert/releases/download/$version/mkcert-$version-$asset" \
    -o "$installer_file"
  install -o root -g root -m 755 "$installer_file" /usr/local/bin/mkcert
  rm -f -- "$installer_file"
}

ensure_groups() {
  local group
  local current_groups
  local missing_groups=()
  for group in gpio spi audio; do
    if ! getent group "$group" >/dev/null 2>&1; then
      echo "Creating missing system group $group..."
      groupadd --system "$group"
    fi
  done
  current_groups=" $(id -nG "$SERVICE_USER") "
  for group in gpio spi audio; do
    if [[ "$current_groups" != *" $group "* ]]; then
      missing_groups+=("$group")
    fi
  done
  if ((${#missing_groups[@]} > 0)); then
    local IFS=,
    usermod --append --groups "${missing_groups[*]}" "$SERVICE_USER"
  fi
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
  shift 2
  local installer_file
  installer_file="$(mktemp /tmp/lumistripe-tool-installer.XXXXXX)"

  curl --disable --proto '=https' --proto-redir '=https' \
    --fail --silent --show-error --location --retry 3 --retry-delay 2 \
    "$url" -o "$installer_file"
  chown "$SERVICE_USER:$SERVICE_GROUP" "$installer_file"
  chmod 700 "$installer_file"

  if ! as_user_with_env "$shell_name" "$installer_file" "$@"; then
    rm -f -- "$installer_file"
    return 1
  fi
  rm -f -- "$installer_file"
}

install_toolchains() {
  local required_bun_version
  local installed_bun_version
  required_bun_version="$(tr -d '\r\n' < "$PROJECT_DIR/.bun-version")"
  [[ "$required_bun_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo "The repository .bun-version does not contain a valid stable version." >&2
    return 1
  }
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 \
    "$SERVICE_HOME/.local/bin" "$SERVICE_HOME/.bun"

  installed_bun_version="$(as_user "$BUN_BIN" --version 2>/dev/null || true)"
  if [[ "$installed_bun_version" != "$required_bun_version" ]]; then
    echo "Installing Bun $required_bun_version for $SERVICE_USER..."
    download_and_run_as_user \
      "https://bun.sh/install" /bin/bash "bun-v$required_bun_version"
  fi
  [[ -x "$BUN_BIN" ]] || {
    echo "Bun installation did not produce $BUN_BIN. " \
      "This may be an unsupported 32-bit Pi image." >&2
    exit 1
  }
  installed_bun_version="$(as_user "$BUN_BIN" --version 2>/dev/null || true)"
  [[ "$installed_bun_version" == "$required_bun_version" ]] || {
    echo "Expected Bun $required_bun_version; found ${installed_bun_version:-an unusable Bun install}." >&2
    return 1
  }

  install_rust_toolchain
}

install_project_dependencies() {
  echo "Installing and building the frontend with Bun..."
  pushd "$PROJECT_DIR/apps/lumistripe-web/frontend" >/dev/null
  as_user "$BUN_BIN" install --frozen-lockfile
  as_user "$BUN_BIN" run build
  popd >/dev/null
}

build_rust_release() {
  echo "Building commit-matched Rust web, engine, CLI, and deployment binaries..."
  as_user_with_env \
    RUSTUP_HOME="$SERVICE_HOME/.rustup" \
    CARGO_HOME="$SERVICE_HOME/.cargo" \
    "$PROJECT_DIR/deploy/build-rust-backend.sh" "$PROJECT_DIR"
}

stage_prebuilt_release() {
  local tool
  local -a install_args
  DEPLOY_RELEASE_COMMIT="$(as_user git -C "$PROJECT_DIR" rev-parse --verify HEAD)" || return 1
  [[ "$DEPLOY_RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo "Could not resolve the deployment source commit." >&2
    return 1
  }
  if [[ -n "$OFFLINE_COMMIT" ]]; then
    [[ "$DEPLOY_RELEASE_COMMIT" == "$OFFLINE_COMMIT" ]] || {
      echo "Offline release commit does not match the checked out source commit." >&2
      return 1
    }
  fi
  tool="$(release_artifact_tool)" || return 1
  install_args=(install --commit "$DEPLOY_RELEASE_COMMIT" --release-root \
    "$PROJECT_DIR/target/rust-deploy/releases" --cache-root "$RELEASE_CACHE_DIR" \
    --uid "$SERVICE_UID" --gid "$SERVICE_GID")
  if [[ -n "$OFFLINE_COMMIT" ]]; then
    install_args+=(--cached-only)
  fi
  python3 "$tool" "${install_args[@]}"
}

create_environment_file() {
  install -d -m 755 "$ENV_DIR" /var/backups/lumistripe
  [[ ! -L "$ENV_FILE" && ( ! -e "$ENV_FILE" || -f "$ENV_FILE" ) ]] || {
    echo "LumiStripe web environment path must be a regular file: $ENV_FILE" >&2
    return 1
  }

  if [[ ! -e "$ENV_FILE" ]]; then
    local pairing_code="${LUMI_PAIRING_CODE:-}"
    if [[ -z "$pairing_code" && -t 0 ]]; then
      IFS= read -r -s -p \
        "LumiStripe pairing code (4 digits, leave blank to generate): " pairing_code || pairing_code=
      printf '\n' >&2
    fi
    if [[ -z "$pairing_code" ]]; then
      pairing_code="$(od -An -N2 -tu2 /dev/urandom | awk '{ printf "%04d", 1000 + ($1 % 9000) }')"
      echo "Generated a LumiStripe pairing code: $pairing_code"
    fi
    [[ "$pairing_code" =~ ^[0-9]{4}$ ]] || {
      echo "LUMI_PAIRING_CODE must be exactly four digits." >&2
      exit 1
    }
    install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 600 \
      "$SCRIPT_DIR/lumistripe-web.env.example" "$ENV_FILE"
    sed -i "s/^LUMI_PAIRING_CODE=.*/LUMI_PAIRING_CODE=$pairing_code/" "$ENV_FILE"
    WEB_ENV_CHANGED=1
  fi

  if [[ "$(stat -c '%u:%g:%a' -- "$ENV_FILE")" != "$SERVICE_UID:$SERVICE_GID:600" ]]; then
    chown --no-dereference "$SERVICE_USER:$SERVICE_GROUP" -- "$ENV_FILE"
    chmod 600 -- "$ENV_FILE"
  fi
  local pairing_code
  pairing_code="$(awk -F= '$1 == "LUMI_PAIRING_CODE" { print $2; exit }' "$ENV_FILE")"
  [[ "$pairing_code" =~ ^[0-9]{4}$ ]] || {
    echo "LUMI_PAIRING_CODE must be exactly four digits in $ENV_FILE." >&2
    exit 1
  }
}

create_spotify_environment_file() {
  if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]]; then
    echo "Spotify backend is $SELECTED_SPOTIFY_BACKEND; preserving Soloist credentials without installing or starting Soloist."
    return 0
  fi
  install -d -m 755 "$ENV_DIR"
  [[ ! -L "$SPOTIFY_ENV_FILE" &&
     ( ! -e "$SPOTIFY_ENV_FILE" || -f "$SPOTIFY_ENV_FILE" ) ]] || {
    echo "Spotify environment path must be a regular file: $SPOTIFY_ENV_FILE" >&2
    return 1
  }
  if [[ ! -e "$SPOTIFY_ENV_FILE" ]]; then
    local api_key="${LUMI_SPOTIFY_API_KEY:-}"
    local line
    local spotify_env_tmp
    if [[ -z "$api_key" && -t 0 ]]; then
      IFS= read -r -s -p \
        "Spotify Soloist API key (leave blank to skip Spotify): " api_key || api_key=
      printf '\n' >&2
    fi
    if [[ -z "$api_key" ]]; then
      echo "Spotify key not supplied; Spotify will remain disabled."
      return 0
    fi
    [[ "$api_key" != REPLACE_WITH_* ]] || {
      echo "LUMI_SPOTIFY_API_KEY must be a real Spotify Soloist API key." >&2
      exit 1
    }
    [[ "$api_key" != *$'\n'* && "$api_key" != *$'\r'* ]] || {
      echo "LUMI_SPOTIFY_API_KEY must be a single-line value." >&2
      exit 1
    }
    spotify_env_tmp="$(mktemp /tmp/lumistripe-spotify-env.XXXXXX)"
    if ! while IFS= read -r line || [[ -n "$line" ]]; do
      case "$line" in
        LUMI_SPOTIFY_API_KEY=*) printf 'LUMI_SPOTIFY_API_KEY=%s\n' "$api_key" ;;
        LUMI_SPOTIFY_DATA_DIR=*) printf 'LUMI_SPOTIFY_DATA_DIR=%s\n' "$SERVICE_HOME/.local/share/soloist" ;;
        LUMI_SPOTIFY_CACHE_DIR=*) printf 'LUMI_SPOTIFY_CACHE_DIR=%s\n' "$SERVICE_HOME/.cache/soloist" ;;
        *) printf '%s\n' "$line" ;;
      esac
    done < "$SCRIPT_DIR/lumistripe-spotify.env.example" > "$spotify_env_tmp"; then
      rm -f -- "$spotify_env_tmp"
      return 1
    fi
    if ! install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 600 \
      "$spotify_env_tmp" "$SPOTIFY_ENV_FILE"; then
      rm -f -- "$spotify_env_tmp"
      return 1
    fi
    rm -f -- "$spotify_env_tmp"
    SPOTIFY_ENV_CHANGED=1
  fi

  if [[ "$(stat -c '%u:%g:%a' -- "$SPOTIFY_ENV_FILE")" != "$SERVICE_UID:$SERVICE_GID:600" ]]; then
    chown --no-dereference "$SERVICE_USER:$SERVICE_GROUP" -- "$SPOTIFY_ENV_FILE"
    chmod 600 -- "$SPOTIFY_ENV_FILE"
  fi
  local api_key_from_file
  api_key_from_file="$(awk -F= '$1 == "LUMI_SPOTIFY_API_KEY" { print substr($0, index($0, "=") + 1); exit }' "$SPOTIFY_ENV_FILE")"
  [[ -n "$api_key_from_file" && "$api_key_from_file" != REPLACE_WITH_* ]] || {
    echo "LUMI_SPOTIFY_API_KEY must be configured in $SPOTIFY_ENV_FILE." >&2
    exit 1
  }
}

spotify_environment_ready() {
  [[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]] || return 1
  [[ -f "$SPOTIFY_ENV_FILE" ]] || return 1
  local api_key
  api_key="$(awk -F= '$1 == "LUMI_SPOTIFY_API_KEY" { print substr($0, index($0, "=") + 1); exit }' "$SPOTIFY_ENV_FILE")"
  [[ -n "$api_key" && "$api_key" != REPLACE_WITH_* ]]
}

start_deployment_services() {
  local unit
  local spotify_restart_required=0
  local web_restart_required=0
  for unit in lumistripe-web.service lumistripe-spotify.service; do
    # Freshly installed unit files may not be loaded by systemd yet.
    systemctl reset-failed "$unit" >/dev/null 2>&1 || true
  done

  if spotify_environment_ready; then
    systemctl enable lumistripe-spotify.service
    if [[ "$SPOTIFY_ENV_CHANGED" == 1 || "$SPOTIFY_SERVICE_FILE_CHANGED" == 1 ||
          "$WIREPLUMBER_CONFIG_CHANGED" == 1 || "$SOLOIST_BINARY_CHANGED" == 1 ]]; then
      spotify_restart_required=1
    fi
    if [[ "$spotify_restart_required" == 1 ]] || \
      ! systemctl is-active --quiet lumistripe-spotify.service; then
      if ! systemctl restart lumistripe-spotify.service; then
        echo "Spotify Soloist did not start. Inspect it with: systemctl status lumistripe-spotify.service" >&2
        return 1
      fi
    else
      echo "Spotify configuration and executable are unchanged; skipping service restart."
    fi
    if ! systemctl is-active --quiet lumistripe-spotify.service; then
      echo "Spotify Soloist is not active. Inspect it with: systemctl status lumistripe-spotify.service" >&2
      return 1
    fi
  else
    if systemctl is-enabled --quiet lumistripe-spotify.service || \
      systemctl is-active --quiet lumistripe-spotify.service; then
      systemctl disable --now lumistripe-spotify.service >/dev/null 2>&1 || true
    fi
  fi

  systemctl enable lumistripe-web.service
  if [[ "$APP_RELEASE_CHANGED" == 1 || "$WEB_SERVICE_FILE_CHANGED" == 1 ||
        "$WEB_ENV_CHANGED" == 1 ]]; then
    web_restart_required=1
  fi
  if [[ "$web_restart_required" == 1 ]] || \
    ! systemctl is-active --quiet lumistripe-web.service; then
    if ! systemctl restart lumistripe-web.service; then
      echo "LumiStripe web did not start. Inspect it with: systemctl status lumistripe-web.service" >&2
      return 1
    fi
  else
    echo "LumiStripe binaries and service configuration are unchanged; skipping service restart."
  fi
  if ! systemctl is-active --quiet lumistripe-web.service; then
    echo "LumiStripe web is not active. Inspect it with: systemctl status lumistripe-web.service" >&2
    return 1
  fi

  systemctl enable --now lumistripe-update-check.timer

  for _ in {1..30}; do
    if curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null; then
      echo "Web service health check passed."
      return 0
    fi
    if ! systemctl is-active --quiet lumistripe-web.service; then
      break
    fi
    sleep 1
  done

  echo "LumiStripe web did not become healthy. Inspect it with: systemctl status lumistripe-web.service" >&2
  return 1
}

create_tls_certificate() {
  ensure_lumistripe_tls_certificate \
    "$TLS_DIR" "$TLS_CERT" "$TLS_KEY" "$SERVICE_USER" "$SERVICE_GROUP" "$SERVICE_HOME"
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
    "$SCRIPT_DIR/lumistripe-web.service" > "$rendered_file"
  if grep -q '@LUMI_[A-Z_]*@' "$rendered_file"; then
    rm -f -- "$rendered_file"
    echo "The systemd service template contains unresolved placeholders." >&2
    exit 1
  fi
  install_file_if_changed "$rendered_file" "$SERVICE_FILE" root root 644 || {
    rm -f -- "$rendered_file"
    return 1
  }
  WEB_SERVICE_FILE_CHANGED="$FILE_CHANGED"
  rm -f -- "$rendered_file"
}

render_spotify_service_unit() {
  local rendered_file
  local project_replacement
  local home_replacement
  local user_replacement
  local group_replacement
  local uid_replacement
  rendered_file="$(mktemp /tmp/lumistripe-spotify-service.XXXXXX)"
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
    "$SCRIPT_DIR/lumistripe-spotify.service" > "$rendered_file"
  if grep -q '@LUMI_[A-Z_]*@' "$rendered_file"; then
    rm -f -- "$rendered_file"
    echo "The Spotify systemd service template contains unresolved placeholders." >&2
    exit 1
  fi
  install_file_if_changed "$rendered_file" "$SPOTIFY_SERVICE_FILE" root root 644 || {
    rm -f -- "$rendered_file"
    return 1
  }
  SPOTIFY_SERVICE_FILE_CHANGED="$FILE_CHANGED"
  rm -f -- "$rendered_file"
}

install_wireplumber_config() {
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 "$WIREPLUMBER_DIR"
  install_user_file_if_changed \
    "$SCRIPT_DIR/90-lumistripe-bluetooth.conf" "$WIREPLUMBER_FILE" \
    "$SERVICE_USER" "$SERVICE_GROUP" 644 || return 1
  WIREPLUMBER_CONFIG_CHANGED="$FILE_CHANGED"
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 "$PIPEWIRE_DIR"
  install_user_file_if_changed \
    "$SCRIPT_DIR/90-lumistripe-spotify.conf" "$SPOTIFY_PIPEWIRE_FILE" \
    "$SERVICE_USER" "$SERVICE_GROUP" 644 || return 1
  if [[ "$FILE_CHANGED" == 1 ]]; then
    WIREPLUMBER_CONFIG_CHANGED=1
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

start_user_audio() {
  local audio_config_changed="$WIREPLUMBER_CONFIG_CHANGED"
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

  if [[ "$audio_config_changed" == 1 ]]; then
    as_user_with_env \
      XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus" \
      systemctl --user restart pipewire pipewire-pulse wireplumber
  else
    as_user_with_env \
      XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus" \
      systemctl --user enable --now pipewire pipewire-pulse wireplumber
  fi
}

install_nginx() {
  local nginx_was_active=0
  install -d -m 755 /etc/nginx/sites-available /etc/nginx/sites-enabled
  install_file_if_changed "$SCRIPT_DIR/nginx/led-controller.conf" \
    "$NGINX_AVAILABLE" root root 644 || return 1
  if [[ "$FILE_CHANGED" == 1 ]]; then
    NGINX_CONFIG_CHANGED=1
  fi
  if [[ -L "$NGINX_ENABLED" ]]; then
    if [[ "$(readlink -f -- "$NGINX_ENABLED" 2>/dev/null || true)" != "$NGINX_AVAILABLE" ]]; then
      ln -sfn "$NGINX_AVAILABLE" "$NGINX_ENABLED"
      NGINX_CONFIG_CHANGED=1
    fi
  elif [[ -e "$NGINX_ENABLED" ]]; then
    echo "Refusing to replace a user-managed Nginx site: $NGINX_ENABLED" >&2
    return 1
  else
    ln -s "$NGINX_AVAILABLE" "$NGINX_ENABLED"
    NGINX_CONFIG_CHANGED=1
  fi

  # The bundled configuration owns port 80 as the captive-portal entrypoint.
  # Remove only Debian's stock symlink; never remove a user-created site.
  if [[ -L "$NGINX_DEFAULT" && "$(readlink -f "$NGINX_DEFAULT")" == "/etc/nginx/sites-available/default" ]]; then
    rm -f -- "$NGINX_DEFAULT"
    NGINX_DEFAULT_REMOVED=1
    NGINX_CONFIG_CHANGED=1
  fi

  nginx -t
  if systemctl is-active --quiet nginx.service; then
    nginx_was_active=1
  fi
  systemctl enable nginx.service
  if (( nginx_was_active )); then
    if [[ "$NGINX_CONFIG_CHANGED" == 1 || "$LUMI_TLS_CHANGED" == 1 ]]; then
      systemctl reload nginx.service
    fi
  else
    systemctl start nginx.service
  fi
}

write_install_state() {
  write_lumistripe_install_state "$SERVICE_USER" "$PROJECT_DIR" "$DEPLOY_MODE" "$NGINX_DEFAULT_REMOVED"
  NGINX_DEFAULT_REMOVED="$(awk -F= '$1 == "nginx_default_removed" { print $2; exit }' "$STATE_FILE")"
}

if [[ "$DEPLOY_MODE" != source ]]; then
  if ! binary_target_supported; then
    if [[ "$DEPLOY_MODE" == binary ]]; then
      echo "Prebuilt releases require aarch64 Debian or Raspberry Pi OS Trixie, or aarch64 Ubuntu. Set LUMI_DEPLOY_MODE=source for a local build." >&2
      exit 1
    fi
    EFFECTIVE_DEPLOY_MODE=source
    echo "No LumiStripe prebuilt bundle targets this platform; auto mode will compile from source."
  fi
fi

lumi_phase_start "Package setup"
install_system_packages
if [[ "$EFFECTIVE_DEPLOY_MODE" == source ]]; then
  install_source_build_packages
elif [[ "$DEPLOY_MODE" != source ]]; then
  if [[ -z "$OFFLINE_COMMIT" ]]; then
    install_release_verifier
  else
    echo "Offline installation will use only the previously verified local release cache."
  fi
fi
install_mkcert
lumi_phase_end "Package setup"
ensure_groups
enable_spi
lumi_phase_start "Configuration"
create_environment_file
  SELECTED_SPOTIFY_BACKEND="$(resolve_spotify_backend "${LUMI_SPOTIFY_BACKEND:-}" "$ENV_FILE")"
  write_spotify_backend_setting \
    "$ENV_FILE" "$SELECTED_SPOTIFY_BACKEND" "$SERVICE_USER" "$SERVICE_GROUP"
create_spotify_environment_file
if [[ -n "$OFFLINE_COMMIT" ]] && spotify_environment_ready &&
   [[ ! -x "$SERVICE_HOME/.local/bin/soloist" ]]; then
  echo "Offline installation with Spotify enabled requires the existing Soloist executable at $SERVICE_HOME/.local/bin/soloist." >&2
  exit 1
fi
create_tls_certificate
render_service_unit
render_spotify_service_unit
install_update_support
install_nginx
install_wireplumber_config
write_install_state
lumi_phase_end "Configuration"

# The service account gets a writable runtime checkout only after all files
# copied into privileged system locations have been installed from the trusted
# source above.
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$PROJECT_DIR"
ACTIVE_RELEASE_BEFORE="$(readlink -f -- "$PROJECT_DIR/target/rust-deploy/current" 2>/dev/null || true)"
ACTIVE_RELEASE_BEFORE_COMMIT="${ACTIVE_RELEASE_BEFORE##*/}"
if [[ "$EFFECTIVE_DEPLOY_MODE" == source ]]; then
  lumi_phase_start "Source build"
  install_toolchains
  install_project_dependencies
  build_rust_release
  lumi_phase_end "Source build"
  APP_RELEASE_CHANGED=1
else
  if stage_prebuilt_release; then
    activate_rust_release "$DEPLOY_RELEASE_COMMIT"
    echo "Activated verified prebuilt release $DEPLOY_RELEASE_COMMIT."
    if [[ "$ACTIVE_RELEASE_BEFORE_COMMIT" != "$DEPLOY_RELEASE_COMMIT" ]]; then
      APP_RELEASE_CHANGED=1
    fi
  else
    stage_status=$?
    if may_fallback_to_source "$DEPLOY_MODE" "$stage_status"; then
      echo "No compatible published release is available; auto mode is falling back to a local source build."
      EFFECTIVE_DEPLOY_MODE=source
      lumi_phase_start "Package setup"
      install_source_build_packages
      lumi_phase_end "Package setup"
      lumi_phase_start "Source build"
      install_toolchains
      install_project_dependencies
      build_rust_release
      lumi_phase_end "Source build"
      APP_RELEASE_CHANGED=1
    else
      echo "Prebuilt release installation failed; the active Rust release was not switched." >&2
      exit "$stage_status"
    fi
  fi
fi
install_rust_cli_command "$SERVICE_HOME"
install_privileged_network_helper
if [[ ! -e /var/lib/lumistripe/update/status.json ]]; then
  as_user "$PROJECT_DIR/target/rust-deploy/current/lumistripe-deploy-helper" \
    update-state initialize \
    --project-dir "$PROJECT_DIR" \
    --state-file /var/lib/lumistripe/update/status.json
fi
lumi_phase_start "Soloist"
if spotify_environment_ready; then
  if [[ -n "$OFFLINE_COMMIT" ]]; then
    if LUMI_SOLOIST_OFFLINE=1 install_spotify_soloist \
      "$SERVICE_HOME" "$SERVICE_USER" "$SERVICE_GROUP"; then
      SOLOIST_BINARY_CHANGED=1
    else
      soloist_status=$?
      if (( soloist_status != 10 )); then
        lumi_phase_end "Soloist"
        echo "Offline installation with Spotify enabled requires a valid existing Soloist executable." >&2
        exit 1
      fi
    fi
    echo "Offline installation reused the existing Spotify Soloist executable."
  else
    if install_spotify_soloist "$SERVICE_HOME" "$SERVICE_USER" "$SERVICE_GROUP"; then
      SOLOIST_BINARY_CHANGED=1
    else
      soloist_status=$?
      if (( soloist_status != 10 )); then
        if [[ "${LUMI_SOLOIST_REFRESH:-0}" == 1 ]] && \
          spotify_soloist_installation_is_valid \
            "$SERVICE_HOME" "$SERVICE_USER" "$SERVICE_GROUP"; then
          echo "Warning: Spotify Soloist refresh failed; the valid installed executable was retained." >&2
        else
          lumi_phase_end "Soloist"
          echo "Spotify Soloist could not be installed or refreshed." >&2
          exit 1
        fi
      fi
    fi
  fi
else
  echo "Spotify is disabled; skipping optional Soloist setup."
fi
lumi_phase_end "Soloist"
start_bluetooth
start_user_audio

if [[ "${SYSTEMD_UNITS_CHANGED:-0}" == 1 ||
      "$WEB_SERVICE_FILE_CHANGED" == 1 || "$SPOTIFY_SERVICE_FILE_CHANGED" == 1 ]]; then
  systemctl daemon-reload
fi
lumi_phase_start "Service health"
start_deployment_services
lumi_phase_end "Service health"

write_lumistripe_install_state "$SERVICE_USER" "$PROJECT_DIR" "$EFFECTIVE_DEPLOY_MODE"

lumi_phase_report

echo
echo "LumiStripe deployment installed successfully."
echo "Dashboard: https://led.controller/"
echo "If SPI was just enabled, reboot the Pi before connecting the LED hardware."
echo "Trust this mkcert CA on clients: $MKCERT_CA_ROOT/rootCA.pem"
