#!/usr/bin/env bash

# Download and install Spotify's optional executable with service-user
# permissions. The update service does not need to grant this binary root.
install_spotify_soloist() (
  set -Eeuo pipefail
  local service_home="$1"
  local architecture
  local archive_arch
  local archive_url
  local work_dir
  local archive_file
  local executable_file
  local deploy_helper

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
  as_user install -d -m 755 \
    "$service_home/.local/bin" "$service_home/.cache/lumistripe"
  work_dir="$(as_user mktemp -d "$service_home/.cache/lumistripe/soloist.XXXXXX")"
  archive_file="$work_dir/soloist.tar.gz"
  executable_file="$work_dir/soloist"
  deploy_helper="$PROJECT_DIR/target/rust-deploy/current/lumistripe-deploy-helper"
  trap 'as_user rm -rf -- "$work_dir" || true' EXIT

  echo "Downloading Spotify Soloist for $architecture as the service user..."
  as_user "$deploy_helper" soloist-download \
    --url "$archive_url" \
    --archive "$archive_file" \
    --destination "$executable_file"
  as_user test -s "$executable_file"
  as_user test -x "$deploy_helper"
  as_user "$deploy_helper" soloist-check --binary "$executable_file"
  as_user install -m 755 "$executable_file" "$service_home/.local/bin/soloist"
)
