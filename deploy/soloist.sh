#!/usr/bin/env bash

# Install Spotify's optional executable with service-user permissions. A valid
# install is reused during normal LumiStripe updates. Set
# LUMI_SOLOIST_REFRESH=1 to download and compare the current official build.
#
# Return status: 0 means the executable changed, 10 means it was reused, and 1
# means installation failed. Callers should decide whether an optional refresh
# failure can be tolerated based on whether the old executable still validates.
spotify_soloist_installation_is_valid() {
  local service_home="$1"
  local service_user="${2:-${SERVICE_USER:-${PROJECT_USER:-}}}"
  local service_group="${3:-${SERVICE_GROUP:-${PROJECT_GROUP:-}}}"
  local installed="$service_home/.local/bin/soloist"
  local deploy_helper="$PROJECT_DIR/target/rust-deploy/current/lumistripe-deploy-helper"
  local service_uid
  local service_gid
  [[ -n "$service_user" && -n "$service_group" ]] || return 1
  service_uid="$(id -u "$service_user")" || return 1
  service_gid="$(getent group "$service_group" | cut -d: -f3)"
  [[ -n "$service_gid" && -f "$installed" && ! -L "$installed" && -x "$installed" ]] || return 1
  [[ "$(stat -c '%u:%g:%a' -- "$installed")" == "$service_uid:$service_gid:755" ]] || return 1
  as_user test -x "$deploy_helper" || return 1
  as_user "$deploy_helper" soloist-check --binary "$installed" >/dev/null
}

install_spotify_soloist() (
  set -Eeuo pipefail
  local service_home="$1"
  local service_user="${2:-${SERVICE_USER:-${PROJECT_USER:-}}}"
  local service_group="${3:-${SERVICE_GROUP:-${PROJECT_GROUP:-}}}"
  local architecture
  local archive_arch
  local archive_url
  local install_dir="$service_home/.local/bin"
  local installed="$install_dir/soloist"
  local work_dir
  local archive_file
  local executable_file
  local replacement_file=
  local deploy_helper
  local service_uid
  local service_gid
  local existing_valid=0
  local installed_metadata
  local candidate_metadata
  local installed_version_info=
  local candidate_version_info=
  local installed_epoch=
  local candidate_epoch=
  local installed_version=
  local candidate_version=
  local candidate_is_newer=0

  [[ -n "$service_user" && -n "$service_group" ]] || {
    echo "Spotify Soloist installation requires the LumiStripe service user and group." >&2
    return 1
  }
  service_uid="$(id -u "$service_user")"
  service_gid="$(getent group "$service_group" | cut -d: -f3)"
  [[ -n "$service_gid" ]] || {
    echo "Could not resolve the LumiStripe service group for Spotify Soloist." >&2
    return 1
  }

  architecture="$(uname -m)"
  case "$architecture" in
    aarch64) archive_arch=arm64 ;;
    armv7l) archive_arch=arm32 ;;
    x86_64) archive_arch=x86_64 ;;
    *)
      echo "Spotify Soloist has no configured build for $architecture." >&2
      return 1
      ;;
  esac

  archive_url="https://soloist-builds.spotifycdn.com/soloist_release_${archive_arch}.tar.gz"
  as_user install -d -m 755 "$install_dir" \
    "$service_home/.cache/lumistripe" || {
    echo "Could not prepare Spotify Soloist installation directories." >&2
    return 1
  }
  deploy_helper="$PROJECT_DIR/target/rust-deploy/current/lumistripe-deploy-helper"
  as_user test -x "$deploy_helper" || {
    echo "The verified LumiStripe deployment helper is unavailable for Soloist validation." >&2
    return 1
  }

  installed_metadata=
  if [[ -f "$installed" && ! -L "$installed" && -x "$installed" ]]; then
    installed_metadata="$(stat -c '%u:%g:%a' -- "$installed")"
  fi
  if spotify_soloist_installation_is_valid "$service_home" "$service_user" "$service_group"; then
    existing_valid=1
  fi

  if [[ "${LUMI_SOLOIST_OFFLINE:-0}" == 1 ]]; then
    if (( existing_valid )); then
      echo "Offline installation is reusing the valid Spotify Soloist executable."
      return 10
    fi
    echo "Offline installation requires a valid existing Spotify Soloist executable." >&2
    return 1
  fi
  if (( existing_valid )) && [[ "${LUMI_SOLOIST_REFRESH:-0}" != 1 ]]; then
    echo "Reusing the valid Spotify Soloist executable; no download is needed."
    return 10
  fi

  work_dir="$(as_user mktemp -d "$service_home/.cache/lumistripe/soloist.XXXXXX")" || {
    echo "Could not create temporary storage for Spotify Soloist." >&2
    return 1
  }
  archive_file="$work_dir/soloist.tar.gz"
  executable_file="$work_dir/soloist"
  trap 'if [[ -n "$replacement_file" ]]; then as_user rm -f -- "$replacement_file" || true; fi; as_user rm -rf -- "$work_dir" || true' EXIT

  if (( existing_valid )); then
    echo "Refreshing Spotify Soloist from the official archive..."
  else
    echo "Installing Spotify Soloist for $architecture from the official archive..."
  fi
  if ! as_user "$deploy_helper" soloist-download \
    --url "$archive_url" \
    --archive "$archive_file" \
    --destination "$executable_file"; then
    echo "Spotify Soloist download or archive validation failed; the installed executable was left untouched." >&2
    return 1
  fi
  as_user test -s "$executable_file" && as_user test -x "$executable_file" || {
    echo "The downloaded Spotify Soloist executable is missing or not executable." >&2
    return 1
  }
  if ! as_user "$deploy_helper" soloist-check --binary "$executable_file"; then
    echo "The downloaded Spotify Soloist executable failed validation; the installed executable was left untouched." >&2
    return 1
  fi

  if [[ -f "$installed" && ! -L "$installed" ]] && \
    as_user cmp -s -- "$executable_file" "$installed"; then
    if [[ "$installed_metadata" != "$service_uid:$service_gid:755" ]]; then
      # Content is unchanged. Repair metadata in place rather than replacing a
      # byte-identical binary or restarting a running Spotify service.
      chown --no-dereference "$service_user:$service_group" -- "$installed"
      chmod 755 -- "$installed"
      [[ -f "$installed" && ! -L "$installed" ]] || {
        echo "The Spotify Soloist path changed during permission repair." >&2
        return 1
      }
      [[ "$(stat -c '%u:%g:%a' -- "$installed")" == "$service_uid:$service_gid:755" ]] || {
        echo "Could not repair Spotify Soloist ownership or permissions." >&2
        return 1
      }
    fi
    echo "The official Spotify Soloist download is byte-identical; keeping the installed executable."
    return 10
  fi

  if (( existing_valid )); then
    installed_version_info="$(as_user "$installed" --version 2>/dev/null || true)"
    candidate_version_info="$(as_user "$executable_file" --version 2>/dev/null || true)"
    if [[ "$installed_version_info" =~ [[:space:]]build[[:space:]]+([0-9]+) ]]; then
      installed_epoch="${BASH_REMATCH[1]}"
    fi
    if [[ "$candidate_version_info" =~ [[:space:]]build[[:space:]]+([0-9]+) ]]; then
      candidate_epoch="${BASH_REMATCH[1]}"
    fi
    if [[ "$installed_version_info" =~ [Ss]oloist[[:space:]]+([0-9]+([.][0-9]+)+) ]]; then
      installed_version="${BASH_REMATCH[1]}"
    fi
    if [[ "$candidate_version_info" =~ [Ss]oloist[[:space:]]+([0-9]+([.][0-9]+)+) ]]; then
      candidate_version="${BASH_REMATCH[1]}"
    fi

    if [[ -n "$candidate_epoch" && -n "$installed_epoch" ]]; then
      if (( candidate_epoch > installed_epoch )); then
        candidate_is_newer=1
      elif (( candidate_epoch == installed_epoch )) && \
        [[ -n "$candidate_version" && -n "$installed_version" &&
           "$candidate_version" != "$installed_version" ]] && \
        [[ "$(printf '%s\n%s\n' "$installed_version" "$candidate_version" | LC_ALL=C sort -V | tail -n 1)" == "$candidate_version" ]]; then
        candidate_is_newer=1
      fi
    elif [[ -n "$candidate_version" && -n "$installed_version" &&
            "$candidate_version" != "$installed_version" ]] && \
      [[ "$(printf '%s\n%s\n' "$installed_version" "$candidate_version" | LC_ALL=C sort -V | tail -n 1)" == "$candidate_version" ]]; then
      candidate_is_newer=1
    fi

    if (( ! candidate_is_newer )); then
      echo "The official Spotify Soloist download is not newer than the valid installed build; keeping the installed executable."
      return 10
    fi
  fi

  replacement_file="$(as_user mktemp "$install_dir/.soloist.XXXXXX")" || {
    echo "Could not create a same-directory Spotify Soloist staging file." >&2
    return 1
  }
  as_user install -m 755 -- "$executable_file" "$replacement_file" || {
    echo "Could not stage the Spotify Soloist replacement." >&2
    return 1
  }
  [[ -f "$replacement_file" && ! -L "$replacement_file" && -x "$replacement_file" ]] || {
    echo "Could not stage the Spotify Soloist replacement safely." >&2
    return 1
  }
  candidate_metadata="$(stat -c '%u:%g:%a' -- "$replacement_file")"
  [[ "$candidate_metadata" == "$service_uid:$service_gid:755" ]] || {
    echo "The staged Spotify Soloist replacement has unexpected ownership or permissions." >&2
    return 1
  }
  as_user "$deploy_helper" soloist-check --binary "$replacement_file" >/dev/null || {
    echo "The staged Spotify Soloist replacement failed validation; the installed executable was left untouched." >&2
    return 1
  }

  # The staged file is on the same filesystem as the target. A rename changes
  # the executable atomically, so a failed or interrupted copy cannot truncate
  # a previously working Soloist binary.
  as_user mv -fT -- "$replacement_file" "$installed" || {
    echo "Could not atomically replace Spotify Soloist; the installed executable was left untouched." >&2
    return 1
  }
  replacement_file=
  echo "Installed the verified Spotify Soloist executable atomically."
  return 0
)
