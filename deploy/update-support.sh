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
  [[ -x /usr/bin/python3 && -x /usr/bin/systemctl && -x /usr/bin/sudo ]] || {
    echo "python3, systemctl, and sudo are required for the LumiStripe updater." >&2
    return 1
  }

  rendered_dir="$(mktemp -d /tmp/lumistripe-update-support.XXXXXX)"
  user_replacement="$(printf '%s' "$SERVICE_USER" | sed 's/[&|\\]/\\&/g')"
  group_replacement="$(printf '%s' "$SERVICE_GROUP" | sed 's/[&|\\]/\\&/g')"
  home_replacement="$(printf '%s' "$SERVICE_HOME" | sed 's/[&|\\]/\\&/g')"
  project_replacement="$(printf '%s' "$PROJECT_DIR" | sed 's/[&|\\]/\\&/g')"

  install -d -o root -g root -m 755 "$UPDATE_LIBEXEC_DIR"
  install -d -o root -g root -m 755 "$NETWORK_LIBEXEC_DIR"
  for template in \
    update.sh update_state.py update-support.sh tls.sh soloist.sh extract_soloist.py \
    90-lumistripe-bluetooth.conf 90-lumistripe-spotify.conf \
    lumistripe-spotify.env.example \
    lumistripe-web.service lumistripe-spotify.service \
    lumistripe-update-check.service lumistripe-update-check.timer \
    lumistripe-update.service lumistripe-update.sudoers \
    lumistripe-update-trigger; do
    local mode=644
    case "$template" in
      update.sh|update_state.py|lumistripe-update-trigger) mode=755 ;;
    esac
    install -o root -g root -m "$mode" \
      "$PROJECT_DIR/deploy/$template" "$UPDATE_LIBEXEC_DIR/$template"
  done

  install -d -o root -g root -m 755 "$UPDATE_LIBEXEC_DIR/nginx"
  install -o root -g root -m 644 \
    "$PROJECT_DIR/deploy/nginx/led-controller.conf" "$UPDATE_LIBEXEC_DIR/nginx/led-controller.conf"

  install -o root -g root -m 644 \
    "$PROJECT_DIR/deploy/lumistripe_network.py" \
    "$NETWORK_LIBEXEC_DIR/lumistripe_network.py"
  install -o root -g root -m 755 \
    "$PROJECT_DIR/deploy/lumistripe-network" \
    /usr/local/sbin/lumistripe-network
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

  install -o root -g root -m 644 \
    "$PROJECT_DIR/deploy/lumistripe-update-check.timer" \
    /etc/systemd/system/lumistripe-update-check.timer
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 755 \
    /var/lib/lumistripe/update
  if [[ ! -f /var/lib/lumistripe/update/status.json ]]; then
    /usr/bin/python3 "$UPDATE_LIBEXEC_DIR/update_state.py" initialize \
      --project-dir "$PROJECT_DIR" \
      --state-file /var/lib/lumistripe/update/status.json
  fi

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
}

