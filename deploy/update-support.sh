#!/usr/bin/env bash

# Shared systemd updater installation for install.sh and update.sh.
UPDATE_LIBEXEC_DIR=/usr/local/libexec/lumistripe/deploy
NETWORK_LIBEXEC_DIR=/usr/local/libexec/lumistripe/network

canonical_project_directory() {
  local candidate="${1:-}"
  local canonical

  if [[ -z "$candidate" || "$candidate" == *$'\n'* || "$candidate" == *$'\r'* ]]; then
    echo "Project directory must be a single-line path." >&2
    return 1
  fi
  if ! canonical="$(cd -- "$candidate" 2>/dev/null && pwd -P)"; then
    echo "Project directory does not exist: $candidate" >&2
    return 1
  fi
  if [[ "$canonical" == "/" || "$canonical" == *$'\n'* || "$canonical" == *$'\r'* ||
        ! -e "$canonical/.git" || ! -f "$canonical/pyproject.toml" ||
        ! -d "$canonical/deploy" || ! -d "$canonical/apps/lumistripe-web" ||
        ! -d "$canonical/packages/lumistripe-core" ]]; then
    echo "Project directory is not a valid LumiStripe checkout: $canonical" >&2
    return 1
  fi
  printf '%s\n' "$canonical"
}

uv_version_at_least() {
  local installed_version="$1"
  local minimum_version="$2"
  [[ -n "$installed_version" && -n "$minimum_version" ]] || return 1
  [[ "$(printf '%s\n' "$minimum_version" "$installed_version" | sort -V | tail -n 1)" == "$installed_version" ]]
}

install_privileged_network_helper() {
  local deploy_root="$PROJECT_DIR/rust/target/rust-deploy"
  local current="$deploy_root/current"
  local release_dir
  local release_commit
  local source_commit
  local checkout_commit
  local source_binary
  local temporary

  [[ -L "$current" ]] || {
    echo "The commit-matched Rust deployment helper is missing." >&2
    return 1
  }
  release_dir="$(readlink -f -- "$current")" || return 1
  case "$release_dir" in
    "$deploy_root"/releases/*) ;;
    *)
      echo "The Rust deployment helper is outside the release directory." >&2
      return 1
      ;;
  esac
  release_commit="${release_dir##*/}"
  [[ -f "$release_dir/source-commit" && ! -L "$release_dir/source-commit" ]] || {
    echo "The Rust deployment release marker is missing." >&2
    return 1
  }
  source_commit="$(cat -- "$release_dir/source-commit")" || return 1
  checkout_commit="$(git -C "$PROJECT_DIR" rev-parse --verify HEAD 2>/dev/null)" || return 1
  source_binary="$release_dir/lumistripe-deploy-helper"
  if [[ ! "$release_commit" =~ ^[0-9a-f]{40}$ || "$source_commit" != "$release_commit" ||
        "$checkout_commit" != "$release_commit" || ! -f "$source_binary" ||
        -L "$source_binary" || ! -x "$source_binary" ]]; then
    echo "The Rust NetworkManager helper does not match the checked-out commit." >&2
    return 1
  fi

  install -d -o root -g root -m 755 "$NETWORK_LIBEXEC_DIR"
  temporary="$(mktemp "$NETWORK_LIBEXEC_DIR/.lumistripe-network-helper.XXXXXX")" || return 1
  if ! install -o root -g root -m 755 "$source_binary" "$temporary" ||
     ! mv -fT -- "$temporary" "$NETWORK_LIBEXEC_DIR/lumistripe-network-helper"; then
    rm -f -- "$temporary"
    return 1
  fi
}

read_web_backend() {
  local env_file="${1:-/etc/lumistripe/lumistripe-web.env}"
  [[ -r "$env_file" ]] || {
    echo "Web backend configuration is missing: $env_file" >&2
    return 1
  }
  awk '
    /^[[:space:]]*#/ { next }
    {
      line = $0
      sub(/^[[:space:]]*/, "", line)
      if (line !~ /^LUMI_WEB_BACKEND[[:space:]]*=/) next
      sub(/^[^=]*=/, "", line)
      sub(/^[[:space:]]*/, "", line)
      sub(/[[:space:]]*$/, "", line)
      first = substr(line, 1, 1)
      last = substr(line, length(line), 1)
      if ((first == "\"" && last == "\"") ||
          (first == sprintf("%c", 39) && last == sprintf("%c", 39))) {
        line = substr(line, 2, length(line) - 2)
      }
      backend = line
    }
    END { print backend }
  ' "$env_file"
}

resolve_web_backend() {
  local requested="${1:-}"
  local env_file="${2:-/etc/lumistripe/lumistripe-web.env}"
  local backend="$requested"
  if [[ -z "$backend" && -r "$env_file" ]]; then
    backend="$(read_web_backend "$env_file")" || return 1
  fi
  [[ -n "$backend" ]] || backend=rust
  case "$backend" in
    python|rust) printf '%s\n' "$backend" ;;
    *) echo "Unsupported LUMI_WEB_BACKEND: $backend" >&2; return 1 ;;
  esac
}

web_backend_requires_python() {
  [[ "${1:-}" == python ]]
}

install_rust_cli_command() {
  local service_home="${1:-}"
  local command_path
  local release_binary="$PROJECT_DIR/rust/target/rust-deploy/current/lumistripe"

  [[ -n "$service_home" ]] || {
    echo "The LumiStripe service home is required to install the Rust CLI." >&2
    return 1
  }
  [[ -f "$release_binary" && -x "$release_binary" ]] || {
    echo "The commit-matched Rust CLI is missing at $release_binary; run deploy/build-rust-backend.sh first." >&2
    return 1
  }

  command_path="$service_home/.local/bin/lumistripe-cli"
  as_user install -d -m 755 "$service_home/.local/bin" || return 1
  if [[ -e "$command_path" && ! -L "$command_path" ]]; then
    echo "Refusing to replace an existing non-symlink CLI command: $command_path" >&2
    return 1
  fi
  as_user ln -sfn "$release_binary" "$command_path"
}

migrate_spotify_device_name_override() {
  local old_file=/var/lib/lumistripe/spotify-device-name.env
  local new_directory="$SERVICE_HOME/.config/lumistripe"
  local new_file="$new_directory/spotify-device-name.env"

  if [[ -L "$old_file" || ( -e "$old_file" && ! -f "$old_file" ) ]]; then
    echo "Spotify device-name state must be a regular file: $old_file" >&2
    return 1
  fi
  if [[ -L "$new_file" || ( -e "$new_file" && ! -f "$new_file" ) ]]; then
    echo "Spotify device-name state must be a regular file: $new_file" >&2
    return 1
  fi

  as_user install -d -m 755 "$new_directory"
  if [[ -f "$old_file" && ! -e "$new_file" ]]; then
    as_user install -m 644 "$old_file" "$new_file"
  fi
}

install_update_support() {
  local rendered_dir
  local rendered_file
  local user_replacement
  local group_replacement
  local home_replacement
  local project_replacement
  local template

  command -v visudo >/dev/null 2>&1 || {
    echo "visudo is required to install the LumiStripe update permission." >&2
    return 1
  }
  [[ -x /usr/bin/systemctl && -x /usr/bin/sudo ]] || {
    echo "systemctl and sudo are required for the LumiStripe updater." >&2
    return 1
  }

  rendered_dir="$(mktemp -d /tmp/lumistripe-update-support.XXXXXX)"
  user_replacement="$(printf '%s' "$SERVICE_USER" | sed 's/[&|\\]/\\&/g')"
  group_replacement="$(printf '%s' "$SERVICE_GROUP" | sed 's/[&|\\]/\\&/g')"
  home_replacement="$(printf '%s' "$SERVICE_HOME" | sed 's/[&|\\]/\\&/g')"
  project_replacement="$(printf '%s' "$PROJECT_DIR" | sed 's/[&|\\]/\\&/g')"

  install -d -o root -g root -m 755 "$UPDATE_LIBEXEC_DIR"
  install -d -o root -g root -m 755 "$NETWORK_LIBEXEC_DIR"
  rm -f -- "$NETWORK_LIBEXEC_DIR/lumistripe_network.py"
  for template in \
    update.sh update-support.sh tls.sh soloist.sh \
    build-rust-backend.sh \
    lumistripe-spotify-service.sudoers \
    90-lumistripe-bluetooth.conf 90-lumistripe-spotify.conf \
    lumistripe-spotify.env.example \
    lumistripe-web.service lumistripe-spotify.service \
    lumistripe-update-check.service lumistripe-update-check.timer \
    lumistripe-update.service lumistripe-update.sudoers \
    lumistripe-update-trigger; do
    local mode=644
    case "$template" in
      update.sh|build-rust-backend.sh|lumistripe-update-trigger) mode=755 ;;
    esac
    install -o root -g root -m "$mode" \
      "$PROJECT_DIR/deploy/$template" "$UPDATE_LIBEXEC_DIR/$template"
  done

  install -d -o root -g root -m 755 "$UPDATE_LIBEXEC_DIR/nginx"
  install -o root -g root -m 644 \
    "$PROJECT_DIR/deploy/nginx/led-controller.conf" "$UPDATE_LIBEXEC_DIR/nginx/led-controller.conf"

  install -o root -g root -m 755 \
    "$PROJECT_DIR/deploy/lumistripe-network" \
    /usr/local/sbin/lumistripe-network
  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    "$PROJECT_DIR/deploy/lumistripe-spotify-service.sudoers" \
    > "$rendered_dir/lumistripe-spotify-service.sudoers"
  chmod 440 "$rendered_dir/lumistripe-spotify-service.sudoers"
  if ! visudo -cf "$rendered_dir/lumistripe-spotify-service.sudoers" >/dev/null; then
    rm -rf -- "$rendered_dir"
    echo "The Spotify service restart permission did not pass visudo validation." >&2
    return 1
  fi
  install -o root -g root -m 440 \
    "$rendered_dir/lumistripe-spotify-service.sudoers" \
    /etc/sudoers.d/lumistripe-spotify-service
  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    "$PROJECT_DIR/deploy/lumistripe-network.sudoers" \
    > "$rendered_dir/lumistripe-network.sudoers"
  chmod 440 "$rendered_dir/lumistripe-network.sudoers"
  if ! visudo -cf "$rendered_dir/lumistripe-network.sudoers" >/dev/null; then
    rm -rf -- "$rendered_dir"
    echo "The LumiStripe network permission did not pass visudo validation." >&2
    return 1
  fi
  install -o root -g root -m 440 \
    "$rendered_dir/lumistripe-network.sudoers" \
    /etc/sudoers.d/lumistripe-network

  for template in lumistripe-update-check.service lumistripe-update.service; do
    rendered_file="$rendered_dir/$template"
    sed \
      -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
      -e "s|@LUMI_SERVICE_GROUP@|$group_replacement|g" \
      -e "s|@LUMI_SERVICE_HOME@|$home_replacement|g" \
      -e "s|@LUMI_PROJECT_DIR@|$project_replacement|g" \
      "$UPDATE_LIBEXEC_DIR/$template" > "$rendered_file"
    if grep -q '@LUMI_[A-Z_]*@' "$rendered_file"; then
      rm -rf -- "$rendered_dir"
      echo "An updater service template contains unresolved placeholders." >&2
      return 1
    fi
    install -o root -g root -m 644 "$rendered_file" "/etc/systemd/system/$template"
  done

  migrate_spotify_device_name_override

  install -o root -g root -m 644 \
    "$PROJECT_DIR/deploy/lumistripe-update-check.timer" \
    /etc/systemd/system/lumistripe-update-check.timer
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 \
    /var/lib/lumistripe/update
  for state_path in \
    /var/lib/lumistripe/update/status.json \
    /var/lib/lumistripe/update/update-check.lock; do
    if [[ -L "$state_path" ]]; then
      echo "Updater state path must not be a symlink: $state_path" >&2
      return 1
    fi
    if [[ -e "$state_path" ]]; then
      [[ -f "$state_path" ]] || {
        echo "Updater state path must be a regular file: $state_path" >&2
        return 1
      }
      chown --no-dereference "$SERVICE_USER:$SERVICE_GROUP" "$state_path"
      chmod 644 "$state_path"
    fi
  done

  install -o root -g root -m 755 \
    "$UPDATE_LIBEXEC_DIR/lumistripe-update-trigger" \
    /usr/local/sbin/lumistripe-update-trigger
  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    "$UPDATE_LIBEXEC_DIR/lumistripe-update.sudoers" > "$rendered_dir/lumistripe-update.sudoers"
  chmod 440 "$rendered_dir/lumistripe-update.sudoers"
  if ! visudo -cf "$rendered_dir/lumistripe-update.sudoers" >/dev/null; then
    rm -rf -- "$rendered_dir"
    echo "The LumiStripe updater sudoers file did not pass visudo validation." >&2
    return 1
  fi
  install -o root -g root -m 440 \
    "$rendered_dir/lumistripe-update.sudoers" \
    /etc/sudoers.d/lumistripe-update
  rm -rf -- "$rendered_dir"
  rm -f -- /etc/sudoers.d/lumistripe-spotify-name
  rm -f -- /usr/local/sbin/lumistripe-spotify-device-name
}

