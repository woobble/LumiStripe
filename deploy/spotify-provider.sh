#!/usr/bin/env bash
set -Eeuo pipefail

# Provider management is invoked only through the root-owned lumistripe-admin
# snapshot. Its public interface intentionally accepts provider names, never
# caller-selected paths or shell commands.

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=deploy/update-support.sh
source "$SCRIPT_DIR/update-support.sh"

STATE_DIR=/var/lib/lumistripe
STATE_FILE="$STATE_DIR/install-state"
WEB_ENV_DIR=/etc/lumistripe
WEB_ENV_FILE="$WEB_ENV_DIR/lumistripe-web.env"
SOLOIST_ENV_FILE="$WEB_ENV_DIR/lumistripe-spotify.env"
WEB_UNIT=/etc/systemd/system/lumistripe-web.service
SOLOIST_UNIT=/etc/systemd/system/lumistripe-spotify.service
SOLOIST_SUDOERS=/etc/sudoers.d/lumistripe-spotify-service
SYSTEMD_DIR=/etc/systemd/system
SUDOERS_DIR=/etc/sudoers.d
UPDATE_LOCK_DIR=/run/lumistripe-update
UPDATE_LOCK_FILE="$UPDATE_LOCK_DIR/update.lock"

SERVICE_USER=
SERVICE_GROUP=
SERVICE_HOME=
PROJECT_DIR=
ACTIVE_RELEASE=
CURRENT_BACKEND=
CURRENT_BACKEND_EXPLICIT=0
WEB_WAS_ACTIVE=0
WEB_WAS_ENABLED=0
SOLOIST_WAS_ACTIVE=0
SOLOIST_WAS_ENABLED=0
BACKUP_DIR=
UNIT_CHANGED=0

provider_error() {
  printf 'Error: %s\n' "$*" >&2
}

provider_root_directory() {
  local directory="$1" metadata owner mode
  [[ -d "$directory" && ! -L "$directory" ]] || return 1
  metadata="$(stat -c '%u:%a' -- "$directory" 2>/dev/null)" || return 1
  owner="${metadata%%:*}"
  mode="${metadata#*:}"
  [[ "$owner" == 0 ]] && (( (8#$mode & 0022) == 0 ))
}

provider_root_file() {
  local file="$1" metadata owner mode
  [[ -f "$file" && ! -L "$file" ]] || return 1
  metadata="$(stat -c '%u:%a' -- "$file" 2>/dev/null)" || return 1
  owner="${metadata%%:*}"
  mode="${metadata#*:}"
  [[ "$owner" == 0 ]] && (( (8#$mode & 0022) == 0 ))
}

provider_state_value() {
  local file="$1" key="$2"
  awk -F= -v wanted="$key" '
    $1 == wanted { count++; value = substr($0, index($0, "=") + 1) }
    END {
      if (count > 1) exit 2
      if (count == 0) exit 1
      print value
    }
  ' "$file"
}

provider_read_env_value() {
  local file="$1" key="$2"
  [[ -f "$file" && ! -L "$file" ]] || return 1
  awk -v wanted="$key" '
    {
      line = $0
      sub(/^[[:space:]]*/, "", line)
      if (line ~ /^#/ || line !~ ("^" wanted "[[:space:]]*=")) next
      sub(/^[^=]*=/, "", line)
      sub(/^[[:space:]]*/, "", line)
      sub(/[[:space:]]*$/, "", line)
      first = substr(line, 1, 1)
      last = substr(line, length(line), 1)
      if ((first == "\"" && last == "\"") ||
          (first == sprintf("%c", 39) && last == sprintf("%c", 39))) {
        line = substr(line, 2, length(line) - 2)
      }
      count++
      value = line
    }
    END {
      if (count > 1) exit 2
      if (count == 0) exit 1
      print value
    }
  ' "$file"
}

provider_validate_config_directory() {
  local directory
  for directory in /etc "$WEB_ENV_DIR"; do
    provider_root_directory "$directory" || {
      provider_error "Spotify configuration directory is missing or unsafe: $directory"
      return 1
    }
  done
  [[ ! -L "$WEB_ENV_FILE" && -f "$WEB_ENV_FILE" ]] || {
    provider_error "Spotify provider configuration is missing or is not a regular file: $WEB_ENV_FILE"
    return 1
  }
  local metadata expected
  metadata="$(stat -c '%u:%g:%a' -- "$WEB_ENV_FILE")" || return 1
  expected="$(id -u "$SERVICE_USER"):$(id -g "$SERVICE_USER"):600" || return 1
  [[ "$metadata" == "$expected" ]] || {
    provider_error "Spotify provider configuration must be owned by $SERVICE_USER with mode 600; rerun lumistripe-admin repair."
    return 1
  }
}

provider_load_context() {
  local state_user= state_project= unit_user= unit_project= value result
  local metadata mode size current_commit

  SERVICE_USER="${LUMI_SERVICE_USER:-}"
  PROJECT_DIR="${LUMI_PROJECT_DIR:-}"

  if [[ -e "$STATE_FILE" || -L "$STATE_FILE" ]]; then
    for directory in /var /var/lib "$STATE_DIR"; do
      provider_root_directory "$directory" || {
        provider_error "installation state directory is missing or unsafe: $directory"
        return 1
      }
    done
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] || {
      provider_error "installation state must be a regular, non-symlink file."
      return 1
    }
    metadata="$(stat -c '%u:%a:%s' -- "$STATE_FILE")" || return 1
    [[ "${metadata%%:*}" == 0 ]] || {
      provider_error "installation state is not root-owned."
      return 1
    }
    mode="$(cut -d: -f2 <<< "$metadata")"
    size="$(cut -d: -f3 <<< "$metadata")"
    (( (8#$mode & 0077) == 0 && size <= 16384 )) || {
      provider_error "installation state permissions or size are invalid."
      return 1
    }
    if state_user="$(provider_state_value "$STATE_FILE" service_user)"; then
      :
    else
      result=$?
      (( result == 1 )) || { provider_error "installation state has duplicate service-user entries."; return 1; }
    fi
    if state_project="$(provider_state_value "$STATE_FILE" project_dir)"; then
      :
    else
      result=$?
      (( result == 1 )) || { provider_error "installation state has duplicate project-directory entries."; return 1; }
    fi
  fi

  if [[ -e "$WEB_UNIT" || -L "$WEB_UNIT" ]]; then
    provider_root_file "$WEB_UNIT" || {
      provider_error "the installed web service configuration is missing or unsafe."
      return 1
    }
    unit_user="$(awk -F= '$1 == "User" { print substr($0, index($0, "=") + 1); exit }' "$WEB_UNIT")"
    unit_project="$(awk '
      /^Environment=LUMI_PROJECT_DIR=/ {
        print substr($0, length("Environment=LUMI_PROJECT_DIR=") + 1)
        found=1
        exit
      }
      /^WorkingDirectory=/ { fallback = substr($0, length("WorkingDirectory=") + 1) }
      END { if (!found && fallback != "") print fallback }
    ' "$WEB_UNIT" | head -n 1)"
  fi

  SERVICE_USER="${SERVICE_USER:-$state_user}"
  SERVICE_USER="${SERVICE_USER:-$unit_user}"
  PROJECT_DIR="${PROJECT_DIR:-$state_project}"
  PROJECT_DIR="${PROJECT_DIR:-$unit_project}"
  [[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ && "$SERVICE_USER" != root ]] || {
    provider_error "could not resolve a trusted non-root LumiStripe service account."
    return 1
  }
  [[ -z "$state_user" || "$state_user" == "$SERVICE_USER" ]] || {
    provider_error "service account does not match root-owned installation state."
    return 1
  }
  [[ -z "$unit_user" || "$unit_user" == "$SERVICE_USER" ]] || {
    provider_error "service account does not match the installed web service."
    return 1
  }
  [[ -n "$PROJECT_DIR" && "$PROJECT_DIR" == /* && "$PROJECT_DIR" != / &&
     "$PROJECT_DIR" != *$'\n'* && "$PROJECT_DIR" != *$'\r'* ]] || {
    provider_error "could not resolve a trusted LumiStripe project directory."
    return 1
  }
  [[ -z "$state_project" || "$state_project" == "$PROJECT_DIR" ]] || {
    provider_error "project directory does not match root-owned installation state."
    return 1
  }
  [[ -z "$unit_project" || "$unit_project" == "$PROJECT_DIR" ]] || {
    provider_error "project directory does not match the installed web service."
    return 1
  }
  PROJECT_DIR="$(canonical_project_directory "$PROJECT_DIR")" || return 1
  id "$SERVICE_USER" >/dev/null 2>&1 || {
    provider_error "LumiStripe service account does not exist: $SERVICE_USER"
    return 1
  }
  SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
  SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
  [[ -n "$SERVICE_HOME" && "$SERVICE_HOME" != / ]] || {
    provider_error "service account home directory is unavailable."
    return 1
  }

  current_commit="$(runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    GIT_OPTIONAL_LOCKS=0 git -c core.hooksPath=/dev/null \
    -c core.fsmonitor=false -c "safe.directory=$PROJECT_DIR" \
    -C "$PROJECT_DIR" rev-parse --verify HEAD 2>/dev/null)" || {
      provider_error 'could not safely read the installed checkout commit.'
      return 1
    }
  [[ "$current_commit" =~ ^[0-9a-f]{40}$ ]] || {
    provider_error 'the installed checkout has an invalid commit identifier.'
    return 1
  }
  validate_rust_release "$PROJECT_DIR/target/rust-deploy/releases" \
    "$current_commit" || return 1
  ACTIVE_RELEASE="$(readlink -f -- "$PROJECT_DIR/target/rust-deploy/current" 2>/dev/null || true)"
  [[ "$ACTIVE_RELEASE" == "$PROJECT_DIR/target/rust-deploy/releases/$current_commit" ]] || {
    provider_error "active Rust release does not match the installed checkout."
    return 1
  }
  provider_validate_config_directory || return 1
}

provider_resolve_backend() {
  local configured
  configured="$(read_spotify_backend "$WEB_ENV_FILE")" || {
    provider_error "Spotify provider setting is duplicated or unreadable."
    return 1
  }
  if [[ -n "$configured" ]]; then
    CURRENT_BACKEND_EXPLICIT=1
  else
    CURRENT_BACKEND_EXPLICIT=0
  fi
  CURRENT_BACKEND="$(resolve_spotify_backend "" "$WEB_ENV_FILE" soloist)" || return 1
}

provider_validate_existing_soloist_files() {
  local path
  for path in "$SOLOIST_UNIT" "$SOLOIST_SUDOERS"; do
    if [[ -e "$path" || -L "$path" ]]; then
      provider_root_file "$path" || {
        provider_error "existing Soloist integration file is missing or unsafe: $path"
        return 1
      }
    fi
  done
}

provider_service_active() {
  systemctl is-active --quiet "$1" >/dev/null 2>&1
}

provider_service_enabled() {
  systemctl is-enabled --quiet "$1" >/dev/null 2>&1
}

provider_service_state() {
  local service="$1" load_state
  if ! command -v systemctl >/dev/null 2>&1; then
    printf 'Unavailable (systemctl not found)\n'
  elif provider_service_active "$service"; then
    printf 'Active\n'
  elif ! load_state="$(systemctl show --property=LoadState --value "$service" 2>/dev/null)"; then
    printf 'Unavailable (systemd query failed)\n'
  elif [[ "$load_state" == not-found ]]; then
    printf 'Not installed\n'
  elif [[ -n "$load_state" ]]; then
    printf 'Inactive\n'
  else
    printf 'Unavailable (service state unknown)\n'
  fi
}

provider_native_data_dir() {
  local data_dir
  data_dir="$(provider_read_env_value "$WEB_ENV_FILE" LUMI_SPOTIFY_LIBRESPOT_DATA_DIR 2>/dev/null || true)"
  if [[ -z "$data_dir" ]]; then
    data_dir="$SERVICE_HOME/.local/share/lumistripe/spotify"
  fi
  [[ "$data_dir" == /* && "$data_dir" != / && "$data_dir" != *$'\n'* &&
     "$data_dir" != *$'\r'* ]] || return 1
  printf '%s\n' "$data_dir"
}

provider_native_data_writable() {
  local data_dir="$1" candidate metadata service_uid
  if [[ -e "$data_dir" || -L "$data_dir" ]]; then
    [[ -d "$data_dir" && ! -L "$data_dir" ]] || return 1
    metadata="$(stat -c '%u:%a' -- "$data_dir" 2>/dev/null)" || return 1
    service_uid="$(id -u "$SERVICE_USER")" || return 1
    [[ "${metadata%%:*}" == "$service_uid" ]] || return 1
    (( (8#${metadata#*:} & 0077) == 0 )) || return 1
    runuser -u "$SERVICE_USER" -- test -w "$data_dir"
    return $?
  fi
  candidate="$data_dir"
  while [[ ! -e "$candidate" && "$candidate" != / ]]; do
    candidate="$(dirname -- "$candidate")"
  done
  [[ -d "$candidate" && ! -L "$candidate" ]] || return 1
  runuser -u "$SERVICE_USER" -- test -w "$candidate"
}

provider_native_credentials_state() {
  local data_dir="$1" credential_file="$1/credentials.json" metadata
  if [[ -d "$data_dir" && ! -L "$data_dir" &&
        -f "$credential_file" && ! -L "$credential_file" ]]; then
    metadata="$(stat -c '%u:%a' -- "$credential_file" 2>/dev/null)" || {
      printf 'Unavailable (could not inspect local state)\n'
      return
    }
    if [[ "${metadata%%:*}" == "$(id -u "$SERVICE_USER")" ]] &&
       (( (8#${metadata#*:} & 0077) == 0 )); then
      printf 'Credentials present; live authentication not checked\n'
      return
    fi
    printf 'Unavailable (credential file permissions are unsafe)\n'
  else
    printf 'Pairing required (no cached credentials found)\n'
  fi
}

provider_soloist_key_ready() {
  local key metadata expected
  [[ -f "$SOLOIST_ENV_FILE" && ! -L "$SOLOIST_ENV_FILE" ]] || return 1
  metadata="$(stat -c '%u:%g:%a' -- "$SOLOIST_ENV_FILE" 2>/dev/null)" || return 1
  expected="$(id -u "$SERVICE_USER"):$(id -g "$SERVICE_USER"):600" || return 1
  [[ "$metadata" == "$expected" ]] || return 1
  key="$(provider_read_env_value "$SOLOIST_ENV_FILE" LUMI_SPOTIFY_API_KEY 2>/dev/null)" || return 1
  [[ -n "$key" && "$key" != REPLACE_WITH_* ]]
}

provider_soloist_executable_ready() {
  local executable="$SERVICE_HOME/.local/bin/soloist"
  local helper="$ACTIVE_RELEASE/lumistripe-deploy-helper"
  [[ -x "$executable" && ! -L "$executable" && -x "$helper" && ! -L "$helper" ]] || return 1
  runuser -u "$SERVICE_USER" -- env -u LUMI_SPOTIFY_API_KEY \
    "$helper" soloist-check --binary "$executable" >/dev/null 2>&1
}

provider_require_backend() {
  local backend="$1" data_dir web_binary
  case "$backend" in
    librespot)
      web_binary="$ACTIVE_RELEASE/lumistripe-web"
      spotify_web_binary_supports_backend "$web_binary" librespot "$SERVICE_USER" || {
        provider_error "the active web release does not support librespot; update to a compatible release before switching."
        return 1
      }
      data_dir="$(provider_native_data_dir)" || {
        provider_error "LUMI_SPOTIFY_LIBRESPOT_DATA_DIR must be an absolute, single-line path."
        return 1
      }
      provider_native_data_writable "$data_dir" || {
        provider_error "the LumiStripe service account cannot safely use its configured native Spotify state directory."
        return 1
      }
      ;;
    soloist)
      provider_soloist_key_ready || {
        provider_error "Soloist requires its existing protected API-key configuration in $SOLOIST_ENV_FILE; the key was not changed."
        return 1
      }
      provider_soloist_executable_ready || {
        provider_error "the existing Soloist executable is missing or failed its version/build validation; run the trusted Soloist setup before switching."
        return 1
      }
      ;;
    disabled) ;;
    *) provider_error "unsupported Spotify provider: $backend"; return 2 ;;
  esac
}

provider_backup_file() {
  local path="$1" name="$2"
  if [[ -e "$path" || -L "$path" ]]; then
    [[ -f "$path" && ! -L "$path" ]] || {
      provider_error "managed Spotify file is not a regular, non-symlink file: $path"
      return 1
    }
    cp -p -- "$path" "$BACKUP_DIR/$name" || return 1
    : > "$BACKUP_DIR/$name.present"
  else
    : > "$BACKUP_DIR/$name.absent"
  fi
}

provider_acquire_update_lock() {
  provider_root_directory /run || {
    provider_error 'the runtime lock parent is missing or unsafe.'
    return 1
  }
  if [[ -e "$UPDATE_LOCK_DIR" || -L "$UPDATE_LOCK_DIR" ]]; then
    provider_root_directory "$UPDATE_LOCK_DIR" || {
      provider_error 'the update lock directory is unsafe.'
      return 1
    }
  else
    install -d -o root -g root -m 755 "$UPDATE_LOCK_DIR" || return 1
  fi
  if [[ -e "$UPDATE_LOCK_FILE" || -L "$UPDATE_LOCK_FILE" ]]; then
    provider_root_file "$UPDATE_LOCK_FILE" || {
      provider_error 'the update lock file is unsafe.'
      return 1
    }
  fi
  exec 9>"$UPDATE_LOCK_FILE"
  chmod 644 "$UPDATE_LOCK_FILE" || return 1
  flock -n 9 || {
    provider_error 'an update is running; retry the provider change after it finishes.'
    return 1
  }
}

provider_create_backup_directory() {
  BACKUP_DIR="$(mktemp -d /run/lumistripe-provider-switch.XXXXXX)" || return 1
  chmod 700 "$BACKUP_DIR" || {
    rmdir -- "$BACKUP_DIR" 2>/dev/null || true
    BACKUP_DIR=
    return 1
  }
}

provider_cleanup_backup() {
  local name metadata
  [[ "$BACKUP_DIR" == /run/lumistripe-provider-switch.* ]] || {
    provider_error 'refusing to clean an unexpected provider-switch backup path.'
    return 1
  }
  provider_root_directory "$BACKUP_DIR" || {
    provider_error 'provider-switch backup directory is missing or unsafe.'
    return 1
  }
  metadata="$(stat -c '%u:%a' -- "$BACKUP_DIR" 2>/dev/null)" || return 1
  [[ "$metadata" == 0:700 ]] || {
    provider_error 'provider-switch backup directory must be root-owned with mode 700.'
    return 1
  }
  for name in web-env soloist-unit soloist-sudoers; do
    rm -f -- "$BACKUP_DIR/$name" "$BACKUP_DIR/$name.present" \
      "$BACKUP_DIR/$name.absent" || return 1
  done
  rmdir -- "$BACKUP_DIR" || return 1
  BACKUP_DIR=
  trap - INT TERM HUP
}

provider_restore_file() {
  local path="$1" name="$2" temporary
  if [[ -f "$BACKUP_DIR/$name.present" ]]; then
    temporary="$(mktemp "$(dirname -- "$path")/.$(basename -- "$path").restore.XXXXXX")" || return 1
    if ! cat -- "$BACKUP_DIR/$name" > "$temporary" ||
       ! chown --reference="$BACKUP_DIR/$name" -- "$temporary" ||
       ! chmod --reference="$BACKUP_DIR/$name" -- "$temporary" ||
       ! mv -fT -- "$temporary" "$path"; then
      rm -f -- "$temporary"
      return 1
    fi
  else
    [[ ! -L "$path" ]] || return 1
    rm -f -- "$path"
  fi
}

provider_render_soloist_unit() {
  local rendered temporary project_replacement home_replacement user_replacement
  local group_replacement uid_replacement unit_metadata sudoers_metadata
  provider_root_directory "$SYSTEMD_DIR" || return 1
  if [[ -e "$SOLOIST_UNIT" || -L "$SOLOIST_UNIT" ]]; then
    provider_root_file "$SOLOIST_UNIT" || return 1
  fi
  rendered="$(mktemp /tmp/lumistripe-spotify-provider-unit.XXXXXX)" || return 1
  project_replacement="$(printf '%s' "$PROJECT_DIR" | sed 's/[&|\\]/\\&/g')"
  home_replacement="$(printf '%s' "$SERVICE_HOME" | sed 's/[&|\\]/\\&/g')"
  user_replacement="$(printf '%s' "$SERVICE_USER" | sed 's/[&|\\]/\\&/g')"
  group_replacement="$(printf '%s' "$SERVICE_GROUP" | sed 's/[&|\\]/\\&/g')"
  uid_replacement="$(id -u "$SERVICE_USER")"
  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    -e "s|@LUMI_SERVICE_GROUP@|$group_replacement|g" \
    -e "s|@LUMI_PROJECT_DIR@|$project_replacement|g" \
    -e "s|@LUMI_HOME@|$home_replacement|g" \
    -e "s|@LUMI_SERVICE_UID@|$uid_replacement|g" \
    "$SCRIPT_DIR/lumistripe-spotify.service" > "$rendered" || {
      rm -f -- "$rendered"
      return 1
    }
  if grep -q '@LUMI_[A-Z_]*@' "$rendered"; then
    rm -f -- "$rendered"
    return 1
  fi
  temporary="$(mktemp "$SYSTEMD_DIR/.lumistripe-spotify.service.XXXXXX")" || {
    rm -f -- "$rendered"
    return 1
  }
  if ! cat -- "$rendered" > "$temporary" ||
     ! chown root:root -- "$temporary" || ! chmod 644 -- "$temporary"; then
    rm -f -- "$rendered" "$temporary"
    return 1
  fi
  rm -f -- "$rendered"
  if [[ -f "$SOLOIST_UNIT" ]] &&
     unit_metadata="$(stat -c '%u:%g:%a' -- "$SOLOIST_UNIT" 2>/dev/null)" &&
     [[ "$unit_metadata" == 0:0:644 ]] && cmp -s -- "$temporary" "$SOLOIST_UNIT"; then
    rm -f -- "$temporary"
  else
    mv -fT -- "$temporary" "$SOLOIST_UNIT" || {
      rm -f -- "$temporary"
      return 1
    }
    UNIT_CHANGED=1
  fi

  provider_root_directory "$SUDOERS_DIR" || return 1
  [[ -f "$SCRIPT_DIR/lumistripe-spotify-service.sudoers" &&
     ! -L "$SCRIPT_DIR/lumistripe-spotify-service.sudoers" ]] || return 1
  rendered="$(mktemp /etc/sudoers.d/.lumistripe-spotify-service.XXXXXX)" || return 1
  sed -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    "$SCRIPT_DIR/lumistripe-spotify-service.sudoers" > "$rendered" || {
      rm -f -- "$rendered"
      return 1
    }
  chmod 440 -- "$rendered" || { rm -f -- "$rendered"; return 1; }
  if ! visudo -cf "$rendered" >/dev/null; then
    rm -f -- "$rendered"
    provider_error "the Soloist restart permission did not pass visudo validation."
    return 1
  fi
  if [[ -f "$SOLOIST_SUDOERS" ]] &&
     sudoers_metadata="$(stat -c '%u:%g:%a' -- "$SOLOIST_SUDOERS" 2>/dev/null)" &&
     [[ "$sudoers_metadata" == 0:0:440 ]] && cmp -s -- "$rendered" "$SOLOIST_SUDOERS"; then
    rm -f -- "$rendered"
  else
    mv -fT -- "$rendered" "$SOLOIST_SUDOERS" || {
      rm -f -- "$rendered"
      return 1
    }
  fi
}

provider_web_health() {
  local attempt
  for attempt in {1..40}; do
    if curl --disable --fail --silent --show-error --max-time 0.5 \
      http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
      return 0
    fi
    (( attempt == 40 )) || sleep 0.25
  done
  return 1
}

provider_restore_service_state() {
  local failed=0
  systemctl stop lumistripe-web.service >/dev/null 2>&1 || true
  systemctl stop lumistripe-spotify.service >/dev/null 2>&1 || true
  if [[ "$SOLOIST_WAS_ENABLED" != 1 ]] && \
     provider_service_enabled lumistripe-spotify.service; then
    systemctl disable lumistripe-spotify.service || failed=1
  fi
  provider_restore_file "$WEB_ENV_FILE" web-env || failed=1
  provider_restore_file "$SOLOIST_UNIT" soloist-unit || failed=1
  provider_restore_file "$SOLOIST_SUDOERS" soloist-sudoers || failed=1
  if (( UNIT_CHANGED )); then
    systemctl daemon-reload || failed=1
  fi
  if [[ "$WEB_WAS_ENABLED" == 1 ]]; then
    systemctl enable lumistripe-web.service || failed=1
  else
    systemctl disable lumistripe-web.service >/dev/null 2>&1 || failed=1
  fi
  if [[ -f "$SOLOIST_UNIT" ]]; then
    if [[ "$SOLOIST_WAS_ENABLED" == 1 ]]; then
      systemctl enable lumistripe-spotify.service || failed=1
    else
      systemctl disable lumistripe-spotify.service >/dev/null 2>&1 || failed=1
    fi
  fi
  if [[ "$SOLOIST_WAS_ACTIVE" == 1 ]]; then
    systemctl start lumistripe-spotify.service || failed=1
  fi
  if [[ "$WEB_WAS_ACTIVE" == 1 ]]; then
    systemctl start lumistripe-web.service || failed=1
    provider_service_active lumistripe-web.service || failed=1
    provider_web_health || failed=1
  fi
  if [[ "$SOLOIST_WAS_ACTIVE" == 1 ]]; then
    provider_service_active lumistripe-spotify.service || failed=1
  fi
  if (( failed )); then
    provider_error "provider switch failed and the previous configuration or service state could not be fully restored; inspect both LumiStripe services."
    return 1
  fi
}

provider_handle_switch_signal() {
  local signal_name="$1" exit_status
  case "$signal_name" in
    INT) exit_status=130 ;;
    TERM) exit_status=143 ;;
    HUP) exit_status=129 ;;
    *) exit_status=1 ;;
  esac
  trap - INT TERM HUP
  provider_error "Spotify provider switch interrupted by $signal_name."
  if [[ -n "$BACKUP_DIR" ]]; then
    if provider_restore_service_state; then
      if provider_cleanup_backup; then
        provider_error "The previous Spotify provider configuration and service state were restored."
      else
        provider_error "Provider rollback completed, but its recovery backup could not be removed; inspect the LumiStripe services before retrying."
      fi
    else
      provider_error "Provider rollback failed; manual recovery is required. The protected backup remains at $BACKUP_DIR."
    fi
  fi
  exit "$exit_status"
}

provider_switch() {
  local target="$1" confirmation initial_backend
  case "$target" in
    librespot|soloist|disabled) ;;
    *) provider_error "expected librespot, soloist, or disabled."; return 2 ;;
  esac
  provider_load_context || return 1
  provider_resolve_backend || return 1
  provider_validate_existing_soloist_files || return 1
  initial_backend="$CURRENT_BACKEND"
  if [[ "$initial_backend" == "$target" ]]; then
    printf 'Spotify provider is already %s. No changes made.\n' "$target"
    return 0
  fi
  printf 'Current Spotify provider: %s%s\n' "$initial_backend" \
    "$([[ "$CURRENT_BACKEND_EXPLICIT" == 1 ]] || printf ' (legacy default)')"
  printf 'Target Spotify provider:  %s\n' "$target"
  case "$target" in
    librespot)
      printf 'Authentication: pair LumiStripe from Spotify Connect. Native identity and cached credentials will be retained.\n'
      ;;
    soloist)
      printf 'Authentication: Soloist uses its existing protected API key. The key is required and will not be copied or changed.\n'
      ;;
    disabled)
      printf 'Authentication: Spotify services will stop; both providers’ credentials and caches will be retained.\n'
      ;;
  esac
  provider_require_backend "$target" || return 1
  read -r -p 'Apply this provider change? [y/N] ' confirmation || confirmation=
  [[ "$confirmation" =~ ^[Yy]([Ee][Ss])?$ ]] || {
    printf 'Spotify provider change cancelled.\n'
    return 1
  }

  provider_acquire_update_lock || return 1

  provider_load_context || return 1
  provider_resolve_backend || return 1
  provider_validate_existing_soloist_files || return 1
  [[ "$CURRENT_BACKEND" == "$initial_backend" ]] || {
    provider_error 'the Spotify provider changed while confirmation was pending; run the command again.'
    return 1
  }
  provider_require_backend "$target" || return 1
  WEB_WAS_ACTIVE=0
  WEB_WAS_ENABLED=0
  SOLOIST_WAS_ACTIVE=0
  SOLOIST_WAS_ENABLED=0
  provider_service_active lumistripe-web.service && WEB_WAS_ACTIVE=1
  provider_service_enabled lumistripe-web.service && WEB_WAS_ENABLED=1
  provider_service_active lumistripe-spotify.service && SOLOIST_WAS_ACTIVE=1
  provider_service_enabled lumistripe-spotify.service && SOLOIST_WAS_ENABLED=1
  if [[ "$CURRENT_BACKEND" == librespot && "$WEB_WAS_ACTIVE" == 1 &&
        "$SOLOIST_WAS_ACTIVE" == 1 ]]; then
    provider_error 'both Spotify Connect providers are active; stop the Soloist service and retry.'
    return 1
  fi

  provider_create_backup_directory || return 1
  provider_backup_file "$WEB_ENV_FILE" web-env || {
    provider_cleanup_backup || true
    return 1
  }
  provider_backup_file "$SOLOIST_UNIT" soloist-unit || {
    provider_cleanup_backup || true
    return 1
  }
  provider_backup_file "$SOLOIST_SUDOERS" soloist-sudoers || {
    provider_cleanup_backup || true
    return 1
  }
  trap 'provider_handle_switch_signal INT' INT
  trap 'provider_handle_switch_signal TERM' TERM
  trap 'provider_handle_switch_signal HUP' HUP

  if [[ "$WEB_WAS_ACTIVE" == 1 ]] && ! systemctl stop lumistripe-web.service; then
    provider_error 'could not stop the LumiStripe web service before switching providers.'
    provider_restore_service_state || true
    provider_cleanup_backup || true
    return 1
  fi
  if [[ "$SOLOIST_WAS_ACTIVE" == 1 ]] && ! systemctl stop lumistripe-spotify.service; then
    provider_error 'could not stop the Soloist service before switching providers.'
    provider_restore_service_state || true
    provider_cleanup_backup || true
    return 1
  fi

  if ! write_spotify_backend_setting "$WEB_ENV_FILE" "$target" \
      "$SERVICE_USER" "$SERVICE_GROUP"; then
    provider_error 'could not atomically save the requested Spotify provider.'
    provider_restore_service_state || true
    provider_cleanup_backup || true
    return 1
  fi
  if [[ "$target" == soloist ]]; then
    if ! provider_render_soloist_unit || ! systemctl daemon-reload; then
      provider_error 'could not install the validated Soloist service integration.'
      provider_restore_service_state || true
      provider_cleanup_backup || true
      return 1
    fi
  else
    if provider_service_enabled lumistripe-spotify.service || \
       provider_service_active lumistripe-spotify.service; then
      if ! systemctl disable --now lumistripe-spotify.service; then
        provider_error 'could not stop and disable the previous Soloist provider.'
        provider_restore_service_state || true
        provider_cleanup_backup || true
        return 1
      fi
    fi
  fi

  if [[ "$target" == soloist && "$WEB_WAS_ENABLED" == 1 ]]; then
    if ! systemctl enable lumistripe-spotify.service; then
      provider_error 'could not enable the Soloist service.'
      provider_restore_service_state || true
      provider_cleanup_backup || true
      return 1
    fi
  elif [[ "$target" == soloist ]]; then
    systemctl disable lumistripe-spotify.service >/dev/null 2>&1 || true
  fi

  if [[ "$WEB_WAS_ACTIVE" == 1 ]]; then
    if [[ "$target" == soloist ]]; then
      if ! systemctl start lumistripe-spotify.service ||
         ! provider_service_active lumistripe-spotify.service; then
        provider_error 'Soloist did not become active.'
        provider_restore_service_state || true
        provider_cleanup_backup || true
        return 1
      fi
    fi
    if ! systemctl start lumistripe-web.service ||
       ! provider_service_active lumistripe-web.service ||
       ! provider_web_health; then
      provider_error 'the web service did not become healthy after switching providers.'
      provider_restore_service_state || true
      provider_cleanup_backup || true
      return 1
    fi
  fi
  if [[ "$target" != soloist ]] && provider_service_active lumistripe-spotify.service; then
    provider_error 'Soloist remained active after selecting a different provider.'
    provider_restore_service_state || true
    provider_cleanup_backup || true
    return 1
  fi
  if [[ "$WEB_WAS_ACTIVE" == 1 && "$target" == soloist ]] && \
     ! provider_service_active lumistripe-spotify.service; then
    provider_error 'Soloist is not active after the provider switch.'
    provider_restore_service_state || true
    provider_cleanup_backup || true
    return 1
  fi

  provider_cleanup_backup || return 1
  printf 'Spotify provider changed from %s to %s.\n' "$initial_backend" "$target"
  printf 'Web service: %s\n' "$(provider_service_state lumistripe-web.service)"
  if [[ "$target" == soloist ]]; then
    printf 'Spotify service: %s\n' "$(provider_service_state lumistripe-spotify.service)"
  else
    printf 'Spotify service: Not required for %s\n' "$target"
  fi
}

provider_print_status() {
  local data_dir configured=No binary
  local backend_readable=0
  if ! provider_load_context 2>/dev/null; then
    printf 'Spotify provider: Unknown (trusted installation configuration unavailable)\n'
    printf 'Spotify configured: Unavailable\n'
    printf 'Spotify enabled: Unknown\n'
    printf 'Spotify process: Unavailable\n'
    printf 'Spotify service: Unavailable\n'
    printf 'Spotify pairing: Unknown\n'
    printf 'Spotify audio transport: Runtime telemetry unavailable to read-only status\n'
    return 0
  fi
  binary="$ACTIVE_RELEASE/lumistripe-web"
  if provider_resolve_backend 2>/dev/null; then
    backend_readable=1
  fi
  if (( ! backend_readable )); then
    printf 'Spotify provider: Unknown (configuration is invalid)\n'
    printf 'Spotify configured: No\n'
    printf 'Spotify enabled: Unknown\n'
    printf 'Spotify process: Unavailable\n'
    printf 'Spotify service: %s\n' "$(provider_service_state lumistripe-spotify.service)"
    printf 'Spotify pairing: Unknown\n'
    printf 'Spotify audio transport: Runtime telemetry unavailable to read-only status\n'
    return 0
  fi

  case "$CURRENT_BACKEND" in
    librespot)
      printf 'Spotify provider: librespot%s\n' \
        "$([[ "$CURRENT_BACKEND_EXPLICIT" == 1 ]] || printf ' (legacy-compatible runtime setting)')"
      data_dir="$(provider_native_data_dir 2>/dev/null || true)"
      if [[ -n "$data_dir" ]] && provider_native_data_writable "$data_dir" && \
         spotify_web_binary_supports_backend "$binary" librespot "$SERVICE_USER"; then
        configured=Yes
      fi
      printf 'Spotify enabled: Yes\n'
      printf 'Spotify process: LumiStripe web process (embedded Rust receiver)\n'
      printf 'Spotify service: Not required; web service is %s\n' \
        "$(provider_service_state lumistripe-web.service)"
      if [[ -n "$data_dir" ]]; then
        printf 'Spotify pairing: %s\n' "$(provider_native_credentials_state "$data_dir")"
      else
        printf 'Spotify pairing: Unknown (native state path is invalid)\n'
      fi
      ;;
    soloist)
      printf 'Spotify provider: soloist%s\n' \
        "$([[ "$CURRENT_BACKEND_EXPLICIT" == 1 ]] || printf ' (legacy default)')"
      if provider_soloist_key_ready && provider_soloist_executable_ready; then
        configured=Yes
      fi
      printf 'Spotify enabled: Yes\n'
      printf 'Spotify process: lumistripe-spotify.service (Soloist)\n'
      printf 'Spotify service: %s\n' "$(provider_service_state lumistripe-spotify.service)"
      if provider_soloist_key_ready; then
        printf 'Spotify pairing: Soloist API key configured; live authentication not checked\n'
      else
        printf 'Spotify pairing: Required (Soloist API key is missing or invalid)\n'
      fi
      ;;
    disabled)
      printf 'Spotify provider: disabled\n'
      configured=Yes
      printf 'Spotify enabled: No\n'
      printf 'Spotify process: None\n'
      printf 'Spotify service: %s\n' "$(provider_service_state lumistripe-spotify.service)"
      printf 'Spotify pairing: Not required while disabled\n'
      ;;
  esac
  printf 'Spotify configured: %s\n' "$configured"
  printf 'Spotify audio transport: Runtime telemetry unavailable to read-only status\n'
}

provider_main() {
  local action="${1:-}" target="${2:-}"
  case "$action" in
    status)
      [[ "$#" == 1 ]] || { provider_error 'usage: spotify-provider.sh status'; return 2; }
      provider_print_status
      ;;
    use)
      [[ "$#" == 2 ]] || { provider_error 'usage: spotify-provider.sh use librespot|soloist'; return 2; }
      [[ "$target" == librespot || "$target" == soloist ]] || {
        provider_error 'use accepts only librespot or soloist.'
        return 2
      }
      provider_switch "$target"
      ;;
    disable)
      [[ "$#" == 1 ]] || { provider_error 'usage: spotify-provider.sh disable'; return 2; }
      provider_switch disabled
      ;;
    *) provider_error 'usage: spotify-provider.sh {status|use librespot|use soloist|disable}'; return 2 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  [[ "$EUID" == 0 ]] || { provider_error 'run provider management through sudo lumistripe-admin'; exit 1; }
  provider_main "$@"
  exit $?
fi
