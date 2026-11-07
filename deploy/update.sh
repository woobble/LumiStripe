#!/usr/bin/env bash
set -Eeuo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=deploy/tls.sh
source "$SCRIPT_DIR/tls.sh"
# shellcheck source=deploy/update-support.sh
source "$SCRIPT_DIR/update-support.sh"
# shellcheck source=deploy/package-provisioning.sh
source "$SCRIPT_DIR/package-provisioning.sh"
# shellcheck source=deploy/timing.sh
source "$SCRIPT_DIR/timing.sh"
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
APP_RELEASE_CHANGED=0
WEB_ENV_CHANGED=0
SPOTIFY_ENV_CHANGED=0
WEB_SERVICE_FILE_CHANGED=0
SPOTIFY_SERVICE_FILE_CHANGED=0
WIREPLUMBER_CONFIG_CHANGED=0
NGINX_CONFIG_CHANGED=0
SOLOIST_BINARY_CHANGED=0

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
SELECTED_SPOTIFY_BACKEND=
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
SELECTED_SPOTIFY_BACKEND="$(resolve_spotify_backend "" "$WEB_ENV_FILE")" || exit 1

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
  install_missing_apt_packages "PipeWire/ALSA capture packages" \
    pipewire-alsa libasound2-plugins libatomic1 libpulse0
}

create_spotify_environment_file() {
  if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]]; then
    echo "Spotify backend is $SELECTED_SPOTIFY_BACKEND; preserving Soloist credentials without installing or starting Soloist."
    return 0
  fi
  install -d -m 755 /etc/lumistripe
  [[ ! -L "$SPOTIFY_ENV_FILE" &&
     ( ! -e "$SPOTIFY_ENV_FILE" || -f "$SPOTIFY_ENV_FILE" ) ]] || {
    echo "Spotify environment path must be a regular file: $SPOTIFY_ENV_FILE" >&2
    return 1
  }
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
    SPOTIFY_ENV_CHANGED=1
  fi
  if [[ "$(stat -c '%u:%g:%a' -- "$SPOTIFY_ENV_FILE")" != "$(id -u "$PROJECT_USER"):$(id -g "$PROJECT_USER"):600" ]]; then
    chown --no-dereference "$PROJECT_USER:$PROJECT_GROUP" -- "$SPOTIFY_ENV_FILE"
    chmod 600 -- "$SPOTIFY_ENV_FILE"
  fi
}

spotify_environment_ready() {
  [[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]] || return 1
  [[ -f "$SPOTIFY_ENV_FILE" ]] || return 1
  local api_key
  api_key="$(awk -F= '$1 == "LUMI_SPOTIFY_API_KEY" { print substr($0, index($0, "=") + 1); exit }' "$SPOTIFY_ENV_FILE")"
  [[ -n "$api_key" && "$api_key" != REPLACE_WITH_* ]]
}

git_user() { as_user git -C "$PROJECT_DIR" "$@"; }

install_release_verifier() {
  "$SCRIPT_DIR/install-github-cli.sh"
}

install_source_build_packages() {
  install_missing_apt_packages "Rust source-build dependencies" \
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

lumi_run_update_step() {
  local label="$1"
  local status
  shift
  if "$@"; then
    return 0
  else
    status=$?
    echo "Updater step failed: $label (exit status $status)." >&2
    return "$status"
  fi
}

install_wireplumber_config() {
  as_user install -d -m 755 "$WIREPLUMBER_DIR" || return 1
  install_user_file_if_changed \
    "$SCRIPT_DIR/90-lumistripe-bluetooth.conf" "$WIREPLUMBER_FILE" \
    "$PROJECT_USER" "$PROJECT_GROUP" 644 || return 1
  WIREPLUMBER_CONFIG_CHANGED="$FILE_CHANGED"
  as_user install -d -m 755 "$PIPEWIRE_DIR" || return 1
  install_user_file_if_changed \
    "$SCRIPT_DIR/90-lumistripe-spotify.conf" "$SPOTIFY_PIPEWIRE_FILE" \
    "$PROJECT_USER" "$PROJECT_GROUP" 644 || return 1
  if [[ "$FILE_CHANGED" == 1 ]]; then
    WIREPLUMBER_CONFIG_CHANGED=1
  fi
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
  install_file_if_changed "$rendered_file" "$SERVICE_FILE" root root 644 || {
    rm -f -- "$rendered_file"
    return 1
  }
  WEB_SERVICE_FILE_CHANGED="$FILE_CHANGED"
  rm -f -- "$rendered_file"
}

render_spotify_service_unit() {
  SPOTIFY_SERVICE_FILE_CHANGED=0
  if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]]; then
    echo "Spotify backend is $SELECTED_SPOTIFY_BACKEND; retaining the legacy Soloist unit without updating it."
    return 0
  fi
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
  install_file_if_changed "$rendered_file" "$SPOTIFY_SERVICE_FILE" root root 644 || {
    rm -f -- "$rendered_file"
    return 1
  }
  SPOTIFY_SERVICE_FILE_CHANGED="$FILE_CHANGED"
  rm -f -- "$rendered_file"
}

refresh_deployment_files() {
  local step_context="${1:-Configuration}"
  local nginx_was_active=0
  # Creating a certificate involves files writable by the service account.
  # Require an administrator to repair it through the installer instead of
  # importing those files as root during an unprivileged update request.
  if [[ ! -s "$TLS_CERT" || ! -s "$TLS_KEY" ]]; then
    echo "TLS certificate or key missing; rerun deploy/install.sh as administrator." >&2
    return 1
  fi

  lumi_run_update_step "$step_context: install privileged network helper" \
    install_privileged_network_helper || return 1
  lumi_run_update_step "$step_context: install updater support files" \
    install_update_support || return 1
  lumi_run_update_step "$step_context: install PipeWire configuration" \
    install_wireplumber_config || return 1
  lumi_run_update_step "$step_context: render web service unit" \
    render_service_unit || return 1
  lumi_run_update_step "$step_context: render Spotify service unit" \
    render_spotify_service_unit || return 1
  if [[ "${SYSTEMD_UNITS_CHANGED:-0}" == 1 ||
        "$WEB_SERVICE_FILE_CHANGED" == 1 || "$SPOTIFY_SERVICE_FILE_CHANGED" == 1 ]]; then
    lumi_run_update_step "$step_context: reload systemd units" \
      systemctl daemon-reload || return 1
  fi
  lumi_run_update_step "$step_context: enable update-check timer" \
    systemctl enable --now lumistripe-update-check.timer || return 1

  lumi_run_update_step "$step_context: prepare Nginx site directories" \
    install -d -m 755 /etc/nginx/sites-available /etc/nginx/sites-enabled || return 1
  lumi_run_update_step "$step_context: install Nginx site configuration" \
    install_file_if_changed "$SCRIPT_DIR/nginx/led-controller.conf" \
    "$NGINX_AVAILABLE" root root 644 || return 1
  NGINX_CONFIG_CHANGED="$FILE_CHANGED"
  if [[ -L /etc/nginx/sites-enabled/led-controller ]]; then
    if [[ "$(readlink -f -- /etc/nginx/sites-enabled/led-controller 2>/dev/null || true)" != "$NGINX_AVAILABLE" ]]; then
      lumi_run_update_step "$step_context: update Nginx site symlink" \
        ln -sfn "$NGINX_AVAILABLE" /etc/nginx/sites-enabled/led-controller || return 1
      NGINX_CONFIG_CHANGED=1
    fi
  elif [[ -e /etc/nginx/sites-enabled/led-controller ]]; then
    echo "Refusing to replace a user-managed Nginx site: /etc/nginx/sites-enabled/led-controller" >&2
    return 1
  else
    lumi_run_update_step "$step_context: create Nginx site symlink" \
      ln -s "$NGINX_AVAILABLE" /etc/nginx/sites-enabled/led-controller || return 1
    NGINX_CONFIG_CHANGED=1
  fi
  lumi_run_update_step "$step_context: validate Nginx configuration" nginx -t || return 1
  if systemctl is-active --quiet nginx.service; then
    nginx_was_active=1
  fi
  lumi_run_update_step "$step_context: enable Nginx service" \
    systemctl enable nginx.service || return 1
  if (( nginx_was_active )); then
    if [[ "$NGINX_CONFIG_CHANGED" == 1 ]]; then
      lumi_run_update_step "$step_context: reload Nginx service" \
        systemctl reload nginx.service || return 1
    fi
  else
    lumi_run_update_step "$step_context: start Nginx service" \
      systemctl start nginx.service || return 1
  fi

  if [[ -S "$SERVICE_RUNTIME_DIR/bus" && "$WIREPLUMBER_CONFIG_CHANGED" == 1 ]]; then
    lumi_run_update_step "$step_context: restart PipeWire user services" as_user_with_env \
      XDG_RUNTIME_DIR="$SERVICE_RUNTIME_DIR" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=$SERVICE_RUNTIME_DIR/bus" \
      systemctl --user restart pipewire pipewire-pulse wireplumber || return 1
  fi
}

wait_for_web_service_health() {
  local attempt
  for attempt in {1..60}; do
    if curl --disable --fail --silent --max-time 0.25 \
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
    if ! lumi_run_update_step "Rollback: restore previous checkout" \
      git_user switch --detach "$PREVIOUS_REF"; then
      echo "Rollback could not restore the previous revision; manual recovery is required." >&2
      return 1
    fi
  fi
  source "$SCRIPT_DIR/update-support.sh"
  lumi_run_update_step "Rollback: activate previous Rust release" \
    activate_rust_release "$PREVIOUS_REF" || failed=1
  RELEASE_ACTIVATED=0
  lumi_run_update_step "Rollback: install previous CLI command" \
    install_rust_cli_command "$PROJECT_HOME" || failed=1
  refresh_deployment_files "Rollback configuration" || failed=1
  if lumi_run_update_step "Rollback: restart web service" \
    systemctl restart lumistripe-web.service; then
    if systemctl is-active --quiet lumistripe-web.service; then
      if ! lumi_run_update_step "Rollback: wait for web health" wait_for_web_service_health; then
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
    local rollback_spotify_restart=0
    if [[ "$SOLOIST_BINARY_CHANGED" == 1 || "$SPOTIFY_SERVICE_FILE_CHANGED" == 1 ||
          "$SPOTIFY_ENV_CHANGED" == 1 || "$WIREPLUMBER_CONFIG_CHANGED" == 1 ]]; then
      rollback_spotify_restart=1
    fi
    lumi_run_update_step "Rollback: restore Spotify service" \
      restart_spotify_service_if_needed "$rollback_spotify_restart" || failed=1
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
  local exit_status="${LUMI_ERROR_STATUS:-$?}"
  local rollback_status=0
  local active_checkout=
  local active_release=
  unset LUMI_ERROR_STATUS
  trap - ERR INT TERM HUP
  if [[ -n "${LUMI_INTERRUPT_SIGNAL:-}" ]]; then
    echo "Update interrupted by $LUMI_INTERRUPT_SIGNAL." >&2
    unset LUMI_INTERRUPT_SIGNAL
  fi
  echo "Update failed during $LUMI_ACTIVE_PHASE ($(lumi_phase_component); exit status $exit_status)." >&2
  rollback || rollback_status=$?
  if (( rollback_status == 0 )); then
    active_checkout="$(git_user rev-parse --verify HEAD 2>/dev/null || true)"
    active_release="$(readlink -f -- "$PROJECT_DIR/target/rust-deploy/current" 2>/dev/null || true)"
    if [[ "$active_checkout" == "$PREVIOUS_REF" &&
          "$active_release" == "$PROJECT_DIR/target/rust-deploy/releases/$PREVIOUS_REF" ]]; then
      echo "The previous release $PREVIOUS_REF is active; automatic rollback completed or no release switch was needed." >&2
    else
      echo "Automatic rollback returned without confirming the previous checkout and release; manual recovery may be required." >&2
    fi
  else
    echo "Rollback failed; manual recovery is required. Inspect the active release pointer and checkout before retrying." >&2
  fi
  echo "Service logs: sudo journalctl -u lumistripe-web.service -b --no-pager" >&2
  if [[ "$SELECTED_SPOTIFY_BACKEND" == soloist ]]; then
    echo "Spotify service logs: sudo journalctl -u lumistripe-spotify.service -b --no-pager" >&2
  fi
  lumi_phase_report >&2 || true
  record_update_state install-failed
  exit "$exit_status"
}
handle_update_signal() {
  local signal_name="$1"
  case "$signal_name" in
    INT) LUMI_ERROR_STATUS=130 ;;
    TERM) LUMI_ERROR_STATUS=143 ;;
    HUP) LUMI_ERROR_STATUS=129 ;;
    *) LUMI_ERROR_STATUS=1 ;;
  esac
  LUMI_INTERRUPT_SIGNAL="$signal_name"
  handle_update_error
}
PREVIOUS_REF="$(git_user rev-parse HEAD)"
trap handle_update_error ERR
trap 'handle_update_signal INT' INT
trap 'handle_update_signal TERM' TERM
trap 'handle_update_signal HUP' HUP

STATUS=$(git_user status --porcelain)
[[ -z "$STATUS" ]] || { echo "Working tree is dirty; commit or stash changes before updating." >&2; exit 1; }
record_update_state install-start
migrate_legacy_rust_deploy_root
ensure_current_release_static
validate_rust_release "$PROJECT_DIR/target/rust-deploy/releases" "$PREVIOUS_REF"
ACTIVE_RELEASE="$(readlink -f -- "$PROJECT_DIR/target/rust-deploy/current" 2>/dev/null || true)"
[[ "$ACTIVE_RELEASE" == "$PROJECT_DIR/target/rust-deploy/releases/$PREVIOUS_REF" ]] || {
  echo "The active release does not match the project checkout; refusing to update." >&2
  exit 1
}
if [[ "$SELECTED_SPOTIFY_BACKEND" == librespot ]] && \
  ! spotify_web_binary_supports_backend "$ACTIVE_RELEASE/lumistripe-web" librespot "$PROJECT_USER"; then
  echo "The active rollback release does not support librespot; refusing to update before changing the checkout or active release." >&2
  echo "Use the legacy provider for this update, then update to a release that supports native Spotify before switching providers." >&2
  exit 1
fi
if [[ "$DEPLOY_MODE" != source ]] && ! binary_target_supported; then
  if [[ "$DEPLOY_MODE" == binary ]]; then
    echo "Prebuilt releases require aarch64 Debian or Raspberry Pi OS Trixie, or aarch64 Ubuntu 26.04 LTS. Set LUMI_DEPLOY_MODE=source for a local build." >&2
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
printf 'Resolved target: %s\n' "${TARGET_COMMIT:0:12}"

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
if [[ "$SELECTED_SPOTIFY_BACKEND" == librespot && "$EFFECTIVE_DEPLOY_MODE" != source ]] && \
  ! spotify_web_binary_supports_backend \
    "$PROJECT_DIR/target/rust-deploy/releases/$TARGET_COMMIT/lumistripe-web" librespot "$PROJECT_USER"; then
  echo "The verified target release does not support the configured librespot provider; the current release remains active." >&2
  exit 1
fi

lumi_phase_start "Package setup"
install_audio_bridge_packages
if [[ "$EFFECTIVE_DEPLOY_MODE" == source ]]; then
  install_source_build_packages
  require_source_toolchains
fi
lumi_phase_end "Package setup"

if [[ "$SELECTED_SPOTIFY_BACKEND" != soloist ]] && \
  { systemctl is-enabled --quiet lumistripe-spotify.service || \
    systemctl is-active --quiet lumistripe-spotify.service; }; then
  systemctl disable --now lumistripe-spotify.service || {
    echo "Could not stop and disable the unselected Spotify Soloist service; update was not started." >&2
    exit 1
  }
fi

git_user -c core.hooksPath=/dev/null switch --detach "$TARGET_COMMIT"
UPDATE_SWITCHED=1
source "$SCRIPT_DIR/update-support.sh"

lumi_phase_start "Configuration"
create_spotify_environment_file
lumi_phase_end "Configuration"
if [[ "$EFFECTIVE_DEPLOY_MODE" == source ]]; then
  if [[ "$TARGET_COMMIT" == "$PREVIOUS_REF" ]]; then
    echo "The checkout is unchanged; reusing the active source-built release."
  else
    lumi_phase_start "Source build"
    build_frontend
    build_rust_backend
    lumi_phase_end "Source build"
    if [[ "$SELECTED_SPOTIFY_BACKEND" == librespot ]] && \
      ! spotify_web_binary_supports_backend \
        "$PROJECT_DIR/target/rust-deploy/releases/$TARGET_COMMIT/lumistripe-web" librespot "$PROJECT_USER"; then
      echo "The source-built target does not support the configured librespot provider." >&2
      false
    fi
    RELEASE_ACTIVATED=1
    APP_RELEASE_CHANGED=1
  fi
else
  lumi_phase_start "Release activation"
  activate_rust_release "$TARGET_COMMIT"
  RELEASE_ACTIVATED=1
  if [[ "$TARGET_COMMIT" != "$PREVIOUS_REF" ]]; then
    APP_RELEASE_CHANGED=1
  fi
  install_rust_cli_command "$PROJECT_HOME"
  lumi_phase_end "Release activation"
fi
lumi_phase_start "Soloist"
if spotify_environment_ready; then
  if [[ -n "$OFFLINE_COMMIT" ]]; then
    if LUMI_SOLOIST_OFFLINE=1 install_spotify_soloist \
      "$PROJECT_HOME" "$PROJECT_USER" "$PROJECT_GROUP"; then
      SOLOIST_BINARY_CHANGED=1
    else
      soloist_status=$?
      if (( soloist_status != 10 )); then
        lumi_phase_end "Soloist"
        echo "Offline update requires a valid existing Spotify Soloist executable." >&2
        false
      fi
    fi
    echo "Offline update reused the existing Spotify Soloist executable."
  else
    if install_spotify_soloist "$PROJECT_HOME" "$PROJECT_USER" "$PROJECT_GROUP"; then
      SOLOIST_BINARY_CHANGED=1
    else
      soloist_status=$?
      if (( soloist_status != 10 )); then
        if [[ "${LUMI_SOLOIST_REFRESH:-0}" == 1 ]] && \
          spotify_soloist_installation_is_valid \
            "$PROJECT_HOME" "$PROJECT_USER" "$PROJECT_GROUP"; then
          echo "Warning: Spotify Soloist refresh failed; the valid installed executable was retained." >&2
        else
          lumi_phase_end "Soloist"
          echo "Spotify Soloist could not be installed or refreshed; the LumiStripe update is being rolled back." >&2
          false
        fi
      fi
    fi
  fi
else
  echo "Spotify backend is $SELECTED_SPOTIFY_BACKEND; skipping Soloist setup."
fi
lumi_phase_end "Soloist"
lumi_phase_start "Configuration"
refresh_deployment_files
lumi_phase_end "Configuration"
lumi_phase_start "Service health"
if spotify_environment_ready; then
  spotify_restart_required=0
  if [[ "$SOLOIST_BINARY_CHANGED" == 1 || "$SPOTIFY_SERVICE_FILE_CHANGED" == 1 ||
        "$SPOTIFY_ENV_CHANGED" == 1 || "$WIREPLUMBER_CONFIG_CHANGED" == 1 ]]; then
    spotify_restart_required=1
  fi
  lumi_run_update_step "Service health: restart Spotify service" \
    restart_spotify_service_if_needed "$spotify_restart_required"
fi
lumi_run_update_step "Service health: enable web service" \
  systemctl enable lumistripe-web.service
if [[ "$APP_RELEASE_CHANGED" == 1 || "$WEB_SERVICE_FILE_CHANGED" == 1 ||
      "$WEB_ENV_CHANGED" == 1 ]] || \
  ! systemctl is-active --quiet lumistripe-web.service; then
  lumi_run_update_step "Service health: restart web service" \
    systemctl restart lumistripe-web.service
else
  echo "LumiStripe binaries and service configuration are unchanged; skipping service restart."
fi
lumi_run_update_step "Service health: verify web service is active" \
  systemctl is-active --quiet lumistripe-web.service

if lumi_run_update_step "Service health: wait for dashboard health" \
  wait_for_web_service_health; then
  lumi_run_update_step "Service health: record installation state" \
    write_lumistripe_install_state "$PROJECT_USER" "$PROJECT_DIR" "$EFFECTIVE_DEPLOY_MODE"
  lumi_phase_end "Service health"
  lumi_phase_report
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
lumi_phase_end "Service health"
echo "Service did not become healthy in time." >&2
false
