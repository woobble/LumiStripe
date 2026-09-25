#!/usr/bin/env bash
set -Eeuo pipefail

umask 022

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${LUMI_PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
SERVICE_USER="${LUMI_SERVICE_USER:-${SUDO_USER:-}}"

# shellcheck source=deploy/docker/common.sh
source "$SCRIPT_DIR/docker/common.sh"

require_root
load_install_state
refresh_compose_paths
require_project
[[ -f "$DOCKER_ENV_FILE" ]] || \
  die "Docker deployment is not installed; run deploy/docker-install.sh first"
check_runtime_commands
resolve_service_identity 0
load_saved_values
command -v docker >/dev/null 2>&1 || die "Docker is not installed"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable"
command -v flock >/dev/null 2>&1 || die "flock is required (install util-linux)"
command -v curl >/dev/null 2>&1 || die "curl is required"

exec 9>"$DOCKER_LOCK_FILE"
flock -n 9 || die "another LumiStripe Docker update is already running"

# Refresh only LumiStripe-owned host fragments. The backup files recorded by
# the installer remain untouched so uninstall can restore the previous state.
BLUETOOTH_CONFIG_BACKUP="$(awk -F= '$1 == "bluetooth_config_backup" { print $2; exit }' "$DOCKER_STATE_FILE" 2>/dev/null || true)"
SPOTIFY_CONFIG_BACKUP="$(awk -F= '$1 == "spotify_config_backup" { print $2; exit }' "$DOCKER_STATE_FILE" 2>/dev/null || true)"
refresh_host_audio_config
restart_host_audio

echo "Validating Docker Compose configuration..."
compose config --quiet
echo "Rebuilding LumiStripe containers..."
compose build --pull
compose up --detach --remove-orphans

for _ in {1..60}; do
  if curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null; then
    echo "LumiStripe Docker deployment updated successfully."
    compose ps
    exit 0
  fi
  sleep 1
done

compose ps
die "LumiStripe web container did not become healthy in time"
