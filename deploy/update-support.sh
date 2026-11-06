#!/usr/bin/env bash

# Shared systemd updater installation for install.sh and update.sh.
UPDATE_LIBEXEC_DIR=/usr/local/libexec/lumistripe/deploy
NETWORK_LIBEXEC_DIR=/usr/local/libexec/lumistripe/network
RELEASE_CACHE_DIR=/var/cache/lumistripe/releases
LUMI_INSTALL_STATE_DIR=/var/lib/lumistripe
LUMI_INSTALL_STATE_FILE="$LUMI_INSTALL_STATE_DIR/install-state"

FILE_CHANGED=0

install_file_if_changed() {
  local source_file="$1"
  local destination_file="$2"
  local owner="$3"
  local group="$4"
  local mode="$5"
  local owner_uid
  local group_gid
  local destination_metadata

  FILE_CHANGED=0
  [[ -f "$source_file" && ! -L "$source_file" ]] || {
    echo "Managed deployment source is not a regular file: $source_file" >&2
    return 1
  }
  if [[ -L "$destination_file" ]]; then
    echo "Managed deployment destination must not be a symlink: $destination_file" >&2
    return 1
  fi
  owner_uid="$(id -u "$owner")" || return 1
  group_gid="$(getent group "$group" | cut -d: -f3)"
  [[ -n "$group_gid" ]] || {
    echo "Could not resolve managed deployment group $group." >&2
    return 1
  }
  if [[ -f "$destination_file" ]]; then
    destination_metadata="$(stat -c '%u:%g:%a' -- "$destination_file")" || return 1
    if [[ "$destination_metadata" == "$owner_uid:$group_gid:$mode" ]] && \
      cmp -s -- "$source_file" "$destination_file"; then
      return 0
    fi
  fi
  install -o "$owner" -g "$group" -m "$mode" -- \
    "$source_file" "$destination_file" || return 1
  FILE_CHANGED=1
}

# The installation state is plain data and is never sourced as shell. Write it
# beside the previous file and rename it into place so readers see one complete
# version. The project path is always canonicalized before it is persisted.
write_lumistripe_install_state() {
  local service_user="${1:-${SERVICE_USER:-}}"
  local project_dir="${2:-${PROJECT_DIR:-}}"
  local deployment_mode="${3:-}"
  local requested_nginx_default_removed="${4:-}"
  local canonical_project nginx_default_removed=0 previous_mode=
  local temp_file directory metadata mode owner

  [[ "$service_user" =~ ^[a-z_][a-z0-9_-]*[$]?$ && "$service_user" != root ]] || {
    echo "Cannot save LumiStripe installation state with an invalid service user." >&2
    return 1
  }
  canonical_project="$(canonical_project_directory "$project_dir")" || return 1
  [[ -z "$deployment_mode" || "$deployment_mode" == binary ||
     "$deployment_mode" == source || "$deployment_mode" == auto ]] || {
    echo "Cannot save LumiStripe installation state with an invalid deployment mode." >&2
    return 1
  }
  [[ -z "$requested_nginx_default_removed" || "$requested_nginx_default_removed" == 0 ||
     "$requested_nginx_default_removed" == 1 ]] || {
    echo "Cannot save LumiStripe installation state with invalid Nginx metadata." >&2
    return 1
  }

  for directory in /var /var/lib; do
    [[ -d "$directory" && ! -L "$directory" ]] || {
      echo "Installation state parent is missing or unsafe: $directory" >&2
      return 1
    }
    metadata="$(stat -c '%u:%a' -- "$directory")" || return 1
    owner="${metadata%%:*}"
    mode="${metadata#*:}"
    [[ "$owner" == 0 ]] && (( (8#$mode & 0022) == 0 )) || {
      echo "Installation state parent is not protected: $directory" >&2
      return 1
    }
  done

  if [[ -L "$LUMI_INSTALL_STATE_DIR" ]]; then
    echo "Installation state directory must not be a symlink." >&2
    return 1
  fi
  install -d -o root -g root -m 755 "$LUMI_INSTALL_STATE_DIR"
  metadata="$(stat -c '%u:%a' -- "$LUMI_INSTALL_STATE_DIR")" || return 1
  owner="${metadata%%:*}"
  mode="${metadata#*:}"
  [[ "$owner" == 0 && ! -L "$LUMI_INSTALL_STATE_DIR" ]] && (( (8#$mode & 0022) == 0 )) || {
    echo "Installation state directory is not a protected root-owned directory." >&2
    return 1
  }

  if [[ -e "$LUMI_INSTALL_STATE_FILE" || -L "$LUMI_INSTALL_STATE_FILE" ]]; then
    [[ -f "$LUMI_INSTALL_STATE_FILE" && ! -L "$LUMI_INSTALL_STATE_FILE" ]] || {
      echo "Installation state must be a regular, non-symlink file." >&2
      return 1
    }
    metadata="$(stat -c '%u:%a' -- "$LUMI_INSTALL_STATE_FILE")" || return 1
    owner="${metadata%%:*}"
    mode="${metadata#*:}"
    [[ "$owner" == 0 ]] && (( (8#$mode & 0077) == 0 )) || {
      echo "Installation state must be root-owned with restricted permissions." >&2
      return 1
    }
    nginx_default_removed="$(awk -F= '$1 == "nginx_default_removed" { print $2; count++ } END { if (count > 1) exit 2 }' "$LUMI_INSTALL_STATE_FILE")" || {
      echo "Installation state contains duplicate Nginx metadata." >&2
      return 1
    }
    [[ -z "$nginx_default_removed" || "$nginx_default_removed" == 0 || "$nginx_default_removed" == 1 ]] || {
      echo "Installation state contains invalid Nginx metadata." >&2
      return 1
    }
    previous_mode="$(awk -F= '$1 == "deployment_mode" { print $2; count++ } END { if (count > 1) exit 2 }' "$LUMI_INSTALL_STATE_FILE")" || {
      echo "Installation state contains duplicate deployment-mode metadata." >&2
      return 1
    }
    [[ -z "$previous_mode" || "$previous_mode" == binary || "$previous_mode" == source ||
       "$previous_mode" == auto ]] || {
      echo "Installation state contains invalid deployment-mode metadata." >&2
      return 1
    }
  fi
  [[ -n "$deployment_mode" ]] || deployment_mode="$previous_mode"
  # This flag records an action that is reversed only by uninstall. An
  # idempotent reinstall must not forget that the stock site was removed.
  if [[ "$requested_nginx_default_removed" == 1 ]]; then
    nginx_default_removed=1
  fi

  temp_file="$(mktemp "$LUMI_INSTALL_STATE_DIR/.install-state.XXXXXX")" || return 1
  {
    printf 'service_user=%s\n' "$service_user"
    printf 'project_dir=%s\n' "$canonical_project"
    [[ -z "$deployment_mode" ]] || printf 'deployment_mode=%s\n' "$deployment_mode"
    printf 'nginx_default_removed=%s\n' "$nginx_default_removed"
  } > "$temp_file" || { rm -f -- "$temp_file"; return 1; }
  chown root:root -- "$temp_file" && chmod 600 -- "$temp_file" || {
    rm -f -- "$temp_file"
    return 1
  }
  mv -fT -- "$temp_file" "$LUMI_INSTALL_STATE_FILE" || {
    rm -f -- "$temp_file"
    return 1
  }
}

install_user_file_if_changed() {
  local source_file="$1"
  local destination_file="$2"
  local owner="$3"
  local group="$4"
  local mode="$5"
  local owner_uid
  local group_gid
  local destination_metadata

  FILE_CHANGED=0
  [[ -f "$source_file" && ! -L "$source_file" ]] || {
    echo "Managed deployment source is not a regular file: $source_file" >&2
    return 1
  }
  if [[ -L "$destination_file" ]]; then
    echo "Managed user configuration must not be a symlink: $destination_file" >&2
    return 1
  fi
  owner_uid="$(id -u "$owner")" || return 1
  group_gid="$(getent group "$group" | cut -d: -f3)"
  [[ -n "$group_gid" ]] || return 1
  if [[ -f "$destination_file" ]]; then
    destination_metadata="$(stat -c '%u:%g:%a' -- "$destination_file")" || return 1
    if [[ "$destination_metadata" == "$owner_uid:$group_gid:$mode" ]] && \
      as_user cmp -s -- "$source_file" "$destination_file"; then
      return 0
    fi
  fi
  as_user install -m "$mode" -- "$source_file" "$destination_file" || return 1
  FILE_CHANGED=1
}

restart_spotify_service_if_needed() {
  local restart_required="${1:-0}"
  systemctl enable lumistripe-spotify.service || return 1
  if [[ "$restart_required" == 1 ]] || \
    ! systemctl is-active --quiet lumistripe-spotify.service; then
    systemctl restart lumistripe-spotify.service
  else
    echo "Spotify configuration and executable are unchanged; skipping service restart."
  fi
}

binary_target_supported() {
  local architecture="${1:-$(uname -m)}"
  local os_release_file="${2:-/etc/os-release}"
  local distribution codename
  distribution="$(. "$os_release_file" && printf '%s' "${ID:-}")"
  codename="$(. "$os_release_file" && printf '%s' "${VERSION_CODENAME:-}")"
  [[ "$architecture" == aarch64 &&
      ( "$distribution" == ubuntu ||
        ( "$codename" == trixie && ( "$distribution" == debian || "$distribution" == raspbian ) ) ) ]]
}

release_artifact_tool() {
  if [[ -f "$UPDATE_LIBEXEC_DIR/release_artifact.py" && ! -L "$UPDATE_LIBEXEC_DIR/release_artifact.py" ]]; then
    printf '%s\n' "$UPDATE_LIBEXEC_DIR/release_artifact.py"
  else
    printf '%s\n' "$PROJECT_DIR/deploy/release_artifact.py"
  fi
}

may_fallback_to_source() {
  local mode="${1:-}"
  local status="${2:-}"
  [[ "$mode" == auto && ( "$status" == 10 || "$status" == 11 ) ]]
}

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
        ! -e "$canonical/.git" || ! -f "$canonical/Cargo.toml" ||
        ! -d "$canonical/deploy" || ! -d "$canonical/apps/lumistripe-web/frontend" ||
        ! -f "$canonical/apps/lumistripe-web/Cargo.toml" ]]; then
    echo "Project directory is not a valid LumiStripe checkout: $canonical" >&2
    return 1
  fi
  printf '%s\n' "$canonical"
}

validate_rust_release_binaries() {
  local releases_dir="${1:-}"
  local commit="${2:-}"
  local release_dir="$releases_dir/$commit"
  local binary

  [[ "$commit" =~ ^[0-9a-f]{40}$ && -d "$release_dir" && ! -L "$release_dir" ]] || {
    echo "Rust deployment release has an invalid commit directory: $release_dir" >&2
    return 1
  }
  [[ -f "$release_dir/source-commit" && ! -L "$release_dir/source-commit" &&
        "$(cat -- "$release_dir/source-commit" 2>/dev/null || true)" == "$commit" ]] || {
    echo "Rust deployment release marker does not match $commit: $release_dir" >&2
    return 1
  }
  for binary in lumistripe-web lumistripe-engine lumistripe-deploy-helper lumistripe; do
    [[ -f "$release_dir/$binary" && ! -L "$release_dir/$binary" &&
          -x "$release_dir/$binary" ]] || {
      echo "Rust deployment release $commit is incomplete: $release_dir/$binary" >&2
      return 1
    }
  done
}

validate_rust_release() {
  local releases_dir="${1:-}"
  local commit="${2:-}"
  local release_dir="$releases_dir/$commit"
  local tool
  [[ "$commit" =~ ^[0-9a-f]{40}$ && -d "$release_dir" && ! -L "$release_dir" ]] || {
    echo "Rust deployment release has an invalid commit directory: $release_dir" >&2
    return 1
  }
  tool="$(release_artifact_tool)" || return 1
  python3 "$tool" validate --release-dir "$release_dir" --commit "$commit" || return 1
}

activate_rust_release() {
  local commit="${1:-}"
  local deploy_root="$PROJECT_DIR/target/rust-deploy"
  local current="$deploy_root/current"
  local temporary="$deploy_root/.current.$$.tmp"
  validate_rust_release "$deploy_root/releases" "$commit" || return 1
  if [[ -L "$current" &&
        "$(readlink -f -- "$current" 2>/dev/null || true)" == "$deploy_root/releases/$commit" ]]; then
    RELEASE_POINTER_CHANGED=0
    return 0
  fi
  ln -s "releases/$commit" "$temporary" || return 1
  mv -Tf -- "$temporary" "$current" || return 1
  RELEASE_POINTER_CHANGED=1
}

ensure_current_release_static() {
  local deploy_root="$PROJECT_DIR/target/rust-deploy"
  local current="$deploy_root/current"
  local release_dir
  local commit
  local checkout_commit
  local tool
  local project_user="${PROJECT_USER:-${SERVICE_USER:-}}"

  [[ -L "$current" ]] || return 0
  release_dir="$(readlink -f -- "$current" 2>/dev/null || true)"
  case "$release_dir" in
    "$deploy_root/releases"/*) ;;
    *)
      echo "The Rust release pointer is outside its expected directory." >&2
      return 1
      ;;
  esac
  commit="${release_dir##*/}"
  [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || {
    echo "The current Rust release directory has an invalid commit name." >&2
    return 1
  }
  if [[ -s "$release_dir/static/index.html" ]]; then
    validate_rust_release "$deploy_root/releases" "$commit"
    return $?
  fi
  if [[ -e "$release_dir/static" || -L "$release_dir/static" ]]; then
    echo "The current Rust release has an incomplete frontend directory; refusing to overwrite it." >&2
    return 1
  fi
  checkout_commit="$(git -C "$PROJECT_DIR" rev-parse --verify HEAD 2>/dev/null)" || return 1
  [[ "$checkout_commit" == "$commit" ]] || {
    echo "Cannot migrate the legacy frontend because the checkout and active release commits differ." >&2
    return 1
  }
  [[ -s "$PROJECT_DIR/apps/lumistripe-web/static/index.html" ]] || {
    echo "The legacy frontend output is missing; preserving the active Rust release without changing it." >&2
    return 1
  }
  tool="$(release_artifact_tool)" || return 1
  if declare -F as_user >/dev/null 2>&1 && [[ -n "$project_user" ]]; then
    as_user python3 "$tool" migrate-legacy-static \
      --release-dir "$release_dir" \
      --static-dir "$PROJECT_DIR/apps/lumistripe-web/static" \
      --commit "$commit" --project-root "$PROJECT_DIR"
  else
    python3 "$tool" migrate-legacy-static \
      --release-dir "$release_dir" \
      --static-dir "$PROJECT_DIR/apps/lumistripe-web/static" \
      --commit "$commit" --project-root "$PROJECT_DIR"
  fi
  validate_rust_release "$deploy_root/releases" "$commit"
}

validate_legacy_rust_deploy_root() {
  local legacy_root="${1:-}"
  local entry name commit resolved
  local releases_dir="$legacy_root/releases"

  [[ -d "$legacy_root" && ! -L "$legacy_root" ]] || {
    echo "Legacy Rust deployment state is not a real directory: $legacy_root" >&2
    return 1
  }
  for entry in "$legacy_root"/* "$legacy_root"/.[!.]* "$legacy_root"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="${entry##*/}"
    case "$name" in
      build)
        [[ -d "$entry" && ! -L "$entry" ]] || {
          echo "Legacy Rust build cache is not a real directory: $entry" >&2
          return 1
        }
        ;;
      releases)
        [[ -d "$entry" && ! -L "$entry" ]] || {
          echo "Legacy Rust releases are not a real directory: $entry" >&2
          return 1
        }
        ;;
      current)
        [[ -L "$entry" ]] || {
          echo "Legacy Rust current release pointer is not a symlink: $entry" >&2
          return 1
        }
        ;;
      *)
        echo "Unknown entry in legacy Rust deployment state: $entry" >&2
        return 1
        ;;
    esac
  done

  if [[ -d "$releases_dir" ]]; then
    for entry in "$releases_dir"/* "$releases_dir"/.[!.]* "$releases_dir"/..?*; do
      [[ -e "$entry" || -L "$entry" ]] || continue
      name="${entry##*/}"
      [[ "$name" =~ ^[0-9a-f]{40}$ ]] || {
        echo "Legacy Rust release has an invalid directory name: $entry" >&2
        return 1
      }
      validate_rust_release_binaries "$releases_dir" "$name" || return 1
    done
  fi

  if [[ -L "$legacy_root/current" ]]; then
    resolved="$(readlink -f -- "$legacy_root/current" 2>/dev/null || true)"
    case "$resolved" in
      "$releases_dir"/*) commit="${resolved##*/}" ;;
      *)
        echo "Legacy Rust release pointer resolves outside its release directory." >&2
        return 1
        ;;
    esac
    validate_rust_release_binaries "$releases_dir" "$commit" || return 1
  fi
}

migrate_legacy_rust_deploy_root() {
  local legacy_root="$PROJECT_DIR/rust/target/rust-deploy"
  local deploy_root="$PROJECT_DIR/target/rust-deploy"
  local marker="$deploy_root/.layout-migrated-from-rust-target"
  local stage release_dir release_commit resolved

  [[ -e "$legacy_root" || -L "$legacy_root" ]] || return 0
  validate_legacy_rust_deploy_root "$legacy_root" || {
    echo "Refusing to migrate invalid legacy Rust release state; the existing release was left untouched." >&2
    return 1
  }

  if [[ -e "$deploy_root" || -L "$deploy_root" ]]; then
    if [[ -f "$marker" && ! -L "$marker" &&
          "$(cat -- "$marker" 2>/dev/null || true)" == "layout-v1" ]]; then
      if [[ -d "$legacy_root/releases" ]]; then
        for release_dir in "$legacy_root/releases"/*; do
          [[ -e "$release_dir" || -L "$release_dir" ]] || continue
          release_commit="${release_dir##*/}"
          if [[ -e "$deploy_root/releases/$release_commit" ||
                -L "$deploy_root/releases/$release_commit" ]]; then
            [[ -d "$deploy_root/releases/$release_commit" &&
                  ! -L "$deploy_root/releases/$release_commit" ]] || {
              echo "Migrated release $release_commit is not a real directory in $deploy_root; refusing to use mixed release state." >&2
              return 1
            }
            diff -qr -- "$release_dir" "$deploy_root/releases/$release_commit" >/dev/null || {
              echo "Migrated release $release_commit differs from $deploy_root; refusing to use mixed release state." >&2
              return 1
            }
          fi
        done
      fi
      if [[ -e "$deploy_root/current" || -L "$deploy_root/current" ]]; then
        [[ -L "$deploy_root/current" ]] || {
          echo "The migrated Rust release pointer is not a symlink: $deploy_root/current" >&2
          return 1
        }
        resolved="$(readlink -f -- "$deploy_root/current" 2>/dev/null || true)"
        case "$resolved" in
          "$deploy_root/releases/"*) release_commit="${resolved##*/}" ;;
          *)
            echo "The migrated Rust release pointer is outside its release directory." >&2
            return 1
            ;;
        esac
        validate_rust_release_binaries "$deploy_root/releases" "$release_commit" || return 1
      fi
      return 0
    fi
    echo "Both legacy and root Rust deployment directories exist without a migration marker; refusing to merge them." >&2
    return 1
  fi

  mkdir -p -- "$PROJECT_DIR/target"
  stage="$(mktemp -d "$PROJECT_DIR/target/.rust-deploy-migration.XXXXXX")" || return 1
  if [[ -d "$legacy_root/releases" ]]; then
    cp -a -- "$legacy_root/releases" "$stage/releases" || {
      rm -rf -- "$stage"
      return 1
    }
  fi
  if [[ -L "$legacy_root/current" ]]; then
    cp -a -- "$legacy_root/current" "$stage/current" || {
      rm -rf -- "$stage"
      return 1
    }
  fi
  printf '%s\n' layout-v1 > "$stage/.layout-migrated-from-rust-target"
  if ! mv -- "$stage" "$deploy_root"; then
    rm -rf -- "$stage"
    return 1
  fi
  echo "Copied validated legacy Rust releases to $deploy_root; the prior release directory remains available for rollback."
}

retire_legacy_rust_deploy_root() {
  local active_commit="${1:-}"
  local previous_commit="${2:-}"
  local legacy_root="$PROJECT_DIR/rust/target/rust-deploy"
  local deploy_root="$PROJECT_DIR/target/rust-deploy"
  local marker="$deploy_root/.layout-migrated-from-rust-target"
  local legacy_commit resolved requested_active="${1:-}"

  [[ -e "$legacy_root" || -L "$legacy_root" ]] || return 0
  migrate_legacy_rust_deploy_root || return 1
  [[ -f "$marker" && ! -L "$marker" &&
        "$(cat -- "$marker" 2>/dev/null || true)" == "layout-v1" ]] || {
    echo "Legacy Rust deployment state has no verified migration marker; leaving it in place." >&2
    return 1
  }
  validate_legacy_rust_deploy_root "$legacy_root" || return 1
  [[ -L "$deploy_root/current" ]] || {
    echo "The root Rust deployment has no active release pointer; keeping the legacy release for rollback." >&2
    return 1
  }
  resolved="$(readlink -f -- "$deploy_root/current" 2>/dev/null || true)"
  case "$resolved" in
    "$deploy_root/releases/"*) active_commit="${resolved##*/}" ;;
    *)
      echo "The root Rust deployment pointer is outside its release directory; keeping the legacy release." >&2
      return 1
      ;;
  esac
  if [[ -n "$requested_active" && "$requested_active" != "$active_commit" ]]; then
    echo "The root release pointer does not match the expected active commit; keeping the legacy release." >&2
    return 1
  fi
  validate_rust_release "$deploy_root/releases" "$active_commit" || return 1
  [[ "$previous_commit" =~ ^[0-9a-f]{40}$ ]] || {
    echo "The previous source commit is not a full Git commit; keeping the legacy release." >&2
    return 1
  }
  validate_rust_release "$deploy_root/releases" "$previous_commit" || {
    echo "The previous commit-matched Rust release is unavailable; keeping the legacy release." >&2
    return 1
  }
  if [[ -L "$legacy_root/current" ]]; then
    resolved="$(readlink -f -- "$legacy_root/current" 2>/dev/null || true)"
    legacy_commit="${resolved##*/}"
    if [[ "$legacy_commit" == "$active_commit" || "$legacy_commit" == "$previous_commit" ]]; then
      echo "The legacy Rust release is still needed for rollback; keeping it in place."
      return 0
    fi
  fi

  rm -rf -- "$legacy_root"
  rmdir -- "$PROJECT_DIR/rust/target" "$PROJECT_DIR/rust" 2>/dev/null || true
  echo "Retired the legacy Rust deployment directory after the active and previous root releases were verified."
}

install_privileged_network_helper() {
  local deploy_root="$PROJECT_DIR/target/rust-deploy"
  local current="$deploy_root/current"
  local release_dir
  local release_commit
  local source_commit
  local checkout_commit
  local source_binary
  local temporary
  local destination="$NETWORK_LIBEXEC_DIR/lumistripe-network-helper"
  local destination_metadata
  local cached_stage=
  local tool

  migrate_legacy_rust_deploy_root || return 1

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
  validate_rust_release "$deploy_root/releases" "$release_commit" || return 1
  checkout_commit="$(git -C "$PROJECT_DIR" rev-parse --verify HEAD 2>/dev/null)" || return 1
  source_binary="$release_dir/lumistripe-deploy-helper"
  if [[ ! "$release_commit" =~ ^[0-9a-f]{40}$ || "$source_commit" != "$release_commit" ||
        "$checkout_commit" != "$release_commit" || ! -f "$source_binary" ||
        -L "$source_binary" || ! -x "$source_binary" ]]; then
    echo "The Rust NetworkManager helper does not match the checked-out commit." >&2
    return 1
  fi

  install -d -o root -g root -m 755 "$NETWORK_LIBEXEC_DIR"
  [[ ! -L "$destination" ]] || {
    echo "The installed NetworkManager helper must not be a symlink: $destination" >&2
    return 1
  }
  if [[ -d "$RELEASE_CACHE_DIR/$release_commit" ]]; then
    tool="$(release_artifact_tool)" || return 1
    cached_stage="$(mktemp -d /tmp/lumistripe-verified-helper.XXXXXX)" || return 1
    if ! python3 "$tool" extract-binary \
      --cache-dir "$RELEASE_CACHE_DIR/$release_commit" \
      --commit "$release_commit" \
      --binary lumistripe-deploy-helper \
      --destination "$cached_stage/lumistripe-deploy-helper"; then
      rm -rf -- "$cached_stage"
      return 1
    fi
    source_binary="$cached_stage/lumistripe-deploy-helper"
  fi
  if [[ -f "$destination" ]]; then
    destination_metadata="$(stat -c '%u:%g:%a' -- "$destination")" || {
      [[ -z "$cached_stage" ]] || rm -rf -- "$cached_stage"
      return 1
    }
    if [[ "$destination_metadata" == 0:0:755 ]] && cmp -s -- "$source_binary" "$destination"; then
      [[ -z "$cached_stage" ]] || rm -rf -- "$cached_stage"
      return 0
    fi
  fi
  temporary="$(mktemp "$NETWORK_LIBEXEC_DIR/.lumistripe-network-helper.XXXXXX")" || {
    [[ -z "$cached_stage" ]] || rm -rf -- "$cached_stage"
    return 1
  }
  if ! install -o root -g root -m 755 "$source_binary" "$temporary" ||
     ! mv -fT -- "$temporary" "$destination"; then
    rm -f -- "$temporary"
    [[ -z "$cached_stage" ]] || rm -rf -- "$cached_stage"
    return 1
  fi
  [[ -z "$cached_stage" ]] || rm -rf -- "$cached_stage"
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

read_spotify_backend() {
  local env_file="${1:-/etc/lumistripe/lumistripe-web.env}"
  [[ -r "$env_file" ]] || return 0
  awk '
    /^[[:space:]]*#/ { next }
    {
      line = $0
      sub(/^[[:space:]]*/, "", line)
      if (line !~ /^LUMI_SPOTIFY_BACKEND[[:space:]]*=/) next
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
      count++
    }
    END {
      if (count > 1) exit 2
      print backend
    }
  ' "$env_file"
}

resolve_spotify_backend() {
  local requested="${1:-}"
  local env_file="${2:-/etc/lumistripe/lumistripe-web.env}"
  local default_backend="${3:-soloist}"
  local configured="" selected
  case "$default_backend" in
    librespot|soloist|disabled) ;;
    *) echo "Unsupported Spotify backend default: $default_backend." >&2; return 1 ;;
  esac
  if [[ -r "$env_file" ]]; then
    configured="$(read_spotify_backend "$env_file")" || return 1
  fi
  selected="${requested:-${configured:-$default_backend}}"
  case "$selected" in
    librespot|soloist|disabled) printf '%s\n' "$selected" ;;
    *) echo "Unsupported LUMI_SPOTIFY_BACKEND: $selected (expected librespot, soloist, or disabled)." >&2; return 1 ;;
  esac
}

spotify_web_binary_supports_backend() {
  local binary="$1" backend="$2" run_user="${3:-}" help_text
  case "$backend" in
    librespot|soloist|disabled) ;;
    *) return 1 ;;
  esac
  [[ -x "$binary" && ! -L "$binary" ]] || return 1
  command -v timeout >/dev/null 2>&1 || return 1
  if [[ -z "$run_user" ]]; then
    [[ "$EUID" != 0 ]] || return 1
    run_user="$(id -un)"
  fi
  command -v runuser >/dev/null 2>&1 || return 1
  help_text="$(runuser -u "$run_user" -- env -u LUMI_SPOTIFY_BACKEND \
    timeout 10 "$binary" --help 2>/dev/null)" || return 1
  [[ "$help_text" == *"--spotify-backend"* ]] || return 1
  grep -Eq "possible values:.*(^|[[:space:],])${backend}([[:space:],]|$)" <<< "$help_text"
}

write_spotify_backend_setting() {
  local env_file="$1"
  local backend="$2"
  local owner="$3"
  local group="$4"
  local temporary
  case "$backend" in
    librespot|soloist|disabled) ;;
    *) echo "Unsupported Spotify backend: $backend." >&2; return 1 ;;
  esac
  [[ -f "$env_file" && ! -L "$env_file" ]] || {
    echo "Spotify backend configuration must be a regular file: $env_file" >&2
    return 1
  }
  temporary="$(mktemp "$(dirname -- "$env_file")/.lumistripe-web-env.XXXXXX")" || return 1
  if ! awk -v backend="$backend" '
    {
      line = $0
      sub(/^[[:space:]]*/, "", line)
      if (line ~ /^LUMI_SPOTIFY_BACKEND[[:space:]]*=/) {
        if (!written) print "LUMI_SPOTIFY_BACKEND=" backend
        written = 1
        next
      }
      print $0
    }
    END { if (!written) print "LUMI_SPOTIFY_BACKEND=" backend }
  ' "$env_file" > "$temporary"; then
    rm -f -- "$temporary"
    return 1
  fi
  if cmp -s -- "$temporary" "$env_file"; then
    rm -f -- "$temporary"
    WEB_ENV_CHANGED=0
    return 0
  fi
  chown --no-dereference "$owner:$group" -- "$temporary" &&
    chmod 600 -- "$temporary" &&
    mv -fT -- "$temporary" "$env_file" || {
      rm -f -- "$temporary"
      return 1
    }
  WEB_ENV_CHANGED=1
}

resolve_web_backend() {
  local requested="${1:-}"
  local env_file="${2:-/etc/lumistripe/lumistripe-web.env}"
  local configured=""
  if [[ -r "$env_file" ]]; then
    configured="$(read_web_backend "$env_file")" || return 1
  fi
  require_rust_backend "$configured" || return 1
  require_rust_backend "$requested" || return 1
  printf 'rust\n'
}

require_rust_backend() {
  case "${1:-rust}" in
    rust) return 0 ;;
    python)
      cat >&2 <<'EOF'
LUMI_WEB_BACKEND=python is no longer supported. LumiStripe now runs the native Rust web application.
This check left the environment file and application data unchanged. Review the existing
settings file, set LUMI_WEB_BACKEND=rust manually in /etc/lumistripe/lumistripe-web.env,
then rerun the installer or updater. Rust settings migrations preserve existing settings.
EOF
      return 1
      ;;
    *) echo "Unsupported LUMI_WEB_BACKEND: ${1:-} (expected rust)." >&2; return 1 ;;
  esac
}

install_rust_cli_command() {
  local service_home="${1:-}"
  local command_path
  local release_binary="$PROJECT_DIR/target/rust-deploy/current/lumistripe"

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
  if [[ -L "$command_path" && "$(readlink -- "$command_path")" == "$release_binary" ]]; then
    return 0
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
  SYSTEMD_UNITS_CHANGED=0
  local rendered_dir
  local rendered_file
  local user_replacement
  local group_replacement
  local home_replacement
  local project_replacement
  local template
  local source_file
  local destination_file
  local state_metadata

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
    install.sh update.sh uninstall.sh update-support.sh lumistripe-admin \
    spotify-provider.sh \
    tls.sh soloist.sh package-provisioning.sh timing.sh \
    build-rust-backend.sh release_artifact.py install-github-cli.sh rust-toolchain.sh \
    lumistripe-network lumistripe-network.sudoers \
    lumistripe-spotify-service.sudoers \
    90-lumistripe-bluetooth.conf 90-lumistripe-spotify.conf \
    lumistripe-spotify.env.example \
    lumistripe-web.env.example \
    lumistripe-web.service lumistripe-spotify.service \
    lumistripe-update-check.service lumistripe-update-check.timer \
    lumistripe-update.service lumistripe-update.sudoers \
    lumistripe-update-trigger; do
    local mode=644
    case "$template" in
      install.sh|update.sh|uninstall.sh|lumistripe-admin|spotify-provider.sh|build-rust-backend.sh|release_artifact.py|install-github-cli.sh|lumistripe-network|lumistripe-update-trigger) mode=755 ;;
    esac
    source_file="$SCRIPT_DIR/$template"
    destination_file="$UPDATE_LIBEXEC_DIR/$template"
    [[ -f "$source_file" ]] || {
      rm -rf -- "$rendered_dir"
      echo "Trusted deployment snapshot is missing $source_file." >&2
      return 1
    }
    [[ "$source_file" == "$destination_file" ]] && continue
    install_file_if_changed "$source_file" "$destination_file" \
      root root "$mode" || {
        rm -rf -- "$rendered_dir"
        return 1
      }
  done

  install -d -o root -g root -m 755 "$UPDATE_LIBEXEC_DIR/nginx"
  source_file="$SCRIPT_DIR/nginx/led-controller.conf"
  destination_file="$UPDATE_LIBEXEC_DIR/nginx/led-controller.conf"
  if [[ "$source_file" != "$destination_file" ]]; then
    install_file_if_changed "$source_file" "$destination_file" root root 644 || {
      rm -rf -- "$rendered_dir"
      return 1
    }
  fi

  install_file_if_changed \
    "$UPDATE_LIBEXEC_DIR/lumistripe-network" /usr/local/sbin/lumistripe-network \
    root root 755 || {
      rm -rf -- "$rendered_dir"
      return 1
    }
  install_file_if_changed \
    "$UPDATE_LIBEXEC_DIR/lumistripe-admin" /usr/local/sbin/lumistripe-admin \
    root root 755 || {
      rm -rf -- "$rendered_dir"
      return 1
    }
  if [[ "${SELECTED_SPOTIFY_BACKEND:-soloist}" == soloist ]]; then
    sed \
      -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
      "$UPDATE_LIBEXEC_DIR/lumistripe-spotify-service.sudoers" \
      > "$rendered_dir/lumistripe-spotify-service.sudoers"
    chmod 440 "$rendered_dir/lumistripe-spotify-service.sudoers"
    if ! visudo -cf "$rendered_dir/lumistripe-spotify-service.sudoers" >/dev/null; then
      rm -rf -- "$rendered_dir"
      echo "The Spotify service restart permission did not pass visudo validation." >&2
      return 1
    fi
    install_file_if_changed \
      "$rendered_dir/lumistripe-spotify-service.sudoers" \
      /etc/sudoers.d/lumistripe-spotify-service root root 440 || {
        rm -rf -- "$rendered_dir"
        return 1
      }
  fi
  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    "$UPDATE_LIBEXEC_DIR/lumistripe-network.sudoers" \
    > "$rendered_dir/lumistripe-network.sudoers"
  chmod 440 "$rendered_dir/lumistripe-network.sudoers"
  if ! visudo -cf "$rendered_dir/lumistripe-network.sudoers" >/dev/null; then
    rm -rf -- "$rendered_dir"
    echo "The LumiStripe network permission did not pass visudo validation." >&2
    return 1
  fi
  install_file_if_changed "$rendered_dir/lumistripe-network.sudoers" \
    /etc/sudoers.d/lumistripe-network root root 440 || {
      rm -rf -- "$rendered_dir"
      return 1
    }

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
    install_file_if_changed "$rendered_file" "/etc/systemd/system/$template" \
      root root 644 || {
        rm -rf -- "$rendered_dir"
        return 1
      }
    if [[ "$FILE_CHANGED" == 1 ]]; then
      SYSTEMD_UNITS_CHANGED=1
    fi
  done

  migrate_spotify_device_name_override

  install_file_if_changed "$UPDATE_LIBEXEC_DIR/lumistripe-update-check.timer" \
    /etc/systemd/system/lumistripe-update-check.timer root root 644 || {
      rm -rf -- "$rendered_dir"
      return 1
    }
  if [[ "$FILE_CHANGED" == 1 ]]; then
    SYSTEMD_UNITS_CHANGED=1
  fi
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
      state_metadata="$(stat -c '%u:%g:%a' -- "$state_path")"
      if [[ "$state_metadata" != "$(id -u "$SERVICE_USER"):$(id -g "$SERVICE_USER"):644" ]]; then
        chown --no-dereference "$SERVICE_USER:$SERVICE_GROUP" "$state_path"
        chmod 644 "$state_path"
      fi
    fi
  done

  install_file_if_changed "$UPDATE_LIBEXEC_DIR/lumistripe-update-trigger" \
    /usr/local/sbin/lumistripe-update-trigger root root 755 || {
      rm -rf -- "$rendered_dir"
      return 1
    }
  sed \
    -e "s|@LUMI_SERVICE_USER@|$user_replacement|g" \
    "$UPDATE_LIBEXEC_DIR/lumistripe-update.sudoers" > "$rendered_dir/lumistripe-update.sudoers"
  chmod 440 "$rendered_dir/lumistripe-update.sudoers"
  if ! visudo -cf "$rendered_dir/lumistripe-update.sudoers" >/dev/null; then
    rm -rf -- "$rendered_dir"
    echo "The LumiStripe updater sudoers file did not pass visudo validation." >&2
    return 1
  fi
  install_file_if_changed "$rendered_dir/lumistripe-update.sudoers" \
    /etc/sudoers.d/lumistripe-update root root 440 || {
      rm -rf -- "$rendered_dir"
      return 1
    }
  rm -rf -- "$rendered_dir"
  rm -f -- /etc/sudoers.d/lumistripe-spotify-name
  rm -f -- /usr/local/sbin/lumistripe-spotify-device-name
}

