#!/usr/bin/env bash
set -Eeuo pipefail

umask 022

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
SERVICE_USER="${LUMI_SERVICE_USER:-${SUDO_USER:-}}"
ASSUME_YES=0
KEEP_DATA=0

# shellcheck source=deploy/docker/common.sh
source "$SCRIPT_DIR/docker/common.sh"

usage() {
  cat <<'EOF'
Usage: sudo ./deploy/docker-uninstall.sh [--yes] [--keep-data]

Stops and removes the LumiStripe Docker Compose services, local LumiStripe
image, Docker deployment environment, certificates, Spotify secret, and
Docker-owned host audio configuration. Shared Docker, Bluetooth, PipeWire,
and service-user packages are preserved.

Use --keep-data to preserve the Docker deployment state and Spotify session
under /var/lib/lumistripe-docker for a later reinstall.
EOF
}

for argument in "$@"; do
  case "$argument" in
    --yes|-y)
      ASSUME_YES=1
      ;;
    --keep-data)
      KEEP_DATA=1
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
load_install_state
refresh_compose_paths
require_project
check_runtime_commands

if [[ -f "$DOCKER_ENV_FILE" ]]; then
  load_saved_values
fi

resolve_service_identity 0

if ((ASSUME_YES == 0)); then
  cat <<EOF
This will stop and remove the LumiStripe Docker Compose services and image,
the Docker deployment environment, TLS certificates, Spotify API key, and
Docker-created LumiStripe state.

It will preserve Docker itself, shared Bluetooth/PipeWire services, the
$SERVICE_USER account, and the project checkout.
EOF
  if ((KEEP_DATA == 1)); then
    cat <<'EOF'

--keep-data was supplied, so the Docker state directory will be preserved.
EOF
  fi
  echo
  read -r -p "Continue with LumiStripe Docker uninstall? [y/N] " confirmation
  [[ "$confirmation" =~ ^[Yy]([Ee][Ss])?$ ]] || {
    echo "Uninstall cancelled."
    exit 0
  }
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && \
  [[ -f "$DOCKER_ENV_FILE" ]]; then
  compose down --remove-orphans || \
    echo "Warning: Docker Compose could not remove every container." >&2
fi

if command -v docker >/dev/null 2>&1; then
  LUMI_IMAGE="${LUMI_IMAGE:-lumistripe:local}"
  docker image rm "$LUMI_IMAGE" >/dev/null 2>&1 || true
fi

restore_host_file() {
  local target="$1"
  local backup="$2"
  local source_file="$3"
  local had_backup="$4"

  if [[ "$had_backup" == 1 && -e "$backup" ]]; then
    install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 "$(dirname -- "$target")"
    cp -a -- "$backup" "$target"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$target"
    return
  fi
  if [[ -e "$target" && -f "$source_file" ]] && cmp -s "$source_file" "$target"; then
    rm -f -- "$target"
  else
    [[ -e "$target" ]] && echo "Preserving changed host file: $target" >&2
  fi
}

if [[ -n "${WIREPLUMBER_FILE:-}" && -n "${SPOTIFY_PIPEWIRE_FILE:-}" ]]; then
  backup_dir="$DOCKER_STATE_DIR/backups"
  bluetooth_backup="${BLUETOOTH_CONFIG_BACKUP:-0}"
  spotify_backup="${SPOTIFY_CONFIG_BACKUP:-0}"
  restore_host_file \
    "$WIREPLUMBER_FILE" "$backup_dir/bluetooth.conf" \
    "$PROJECT_DIR/deploy/90-lumistripe-bluetooth.conf" "$bluetooth_backup"
  restore_host_file \
    "$SPOTIFY_PIPEWIRE_FILE" "$backup_dir/spotify.conf" \
    "$PROJECT_DIR/deploy/90-lumistripe-spotify.conf" "$spotify_backup"
  rmdir -- "$(dirname -- "$WIREPLUMBER_FILE")" 2>/dev/null || true
  rmdir -- "$(dirname -- "$(dirname -- "$WIREPLUMBER_FILE")")" 2>/dev/null || true
  rmdir -- "$(dirname -- "$SPOTIFY_PIPEWIRE_FILE")" 2>/dev/null || true
  rmdir -- "$(dirname -- "$(dirname -- "$SPOTIFY_PIPEWIRE_FILE")")" 2>/dev/null || true
  restart_host_audio || true
fi

rm -f -- "$DOCKER_ENV_FILE"
rmdir -- "$(dirname -- "$DOCKER_ENV_FILE")" 2>/dev/null || true

if ((KEEP_DATA == 0)); then
  [[ "$DOCKER_STATE_DIR" == /var/lib/lumistripe-docker ]] || \
    die "refusing to remove unexpected Docker state directory: $DOCKER_STATE_DIR"
  rm -rf -- "$DOCKER_STATE_DIR"
else
  rm -f -- "$DOCKER_STATE_FILE"
fi

echo
echo "LumiStripe Docker uninstall completed."
if ((KEEP_DATA == 1)); then
  echo "Docker state was preserved at $DOCKER_STATE_DIR."
else
  echo "Docker-owned LumiStripe state and secrets were removed."
fi
echo "Docker and shared host audio/Bluetooth packages were preserved."
