#!/usr/bin/env bash

# Shared APT checks for the installer and updater. Requirements can be written
# as "package" or "package|minimum-version". No minimums are invented for
# runtime packages whose supported lower bound is not part of the deployment
# contract.

LUMI_APT_LISTS_UPDATED=0
export LUMI_APT_LISTS_UPDATED

installed_package_version() {
  local package="$1"
  local status_line
  local status_first status_second status_third version
  status_line="$(dpkg-query -W -f='${Status} ${Version}' "$package" 2>/dev/null)" || return 1
  read -r status_first status_second status_third version <<< "$status_line"
  [[ "$status_first $status_second $status_third" == 'install ok installed' && -n "$version" ]] || return 1
  printf '%s\n' "$version"
}

version_at_least() {
  local installed="$1"
  local minimum="$2"
  [[ -n "$installed" && -n "$minimum" ]] || return 1
  dpkg --compare-versions "$installed" ge "$minimum"
}

package_is_satisfied() {
  local requirement="$1"
  local package="${requirement%%|*}"
  local minimum=
  local installed
  if [[ "$requirement" == *'|'* ]]; then
    minimum="${requirement#*|}"
  fi
  installed="$(installed_package_version "$package")" || return 1
  [[ -z "$minimum" ]] || version_at_least "$installed" "$minimum"
}

apt_update_for_install() {
  if (( LUMI_APT_LISTS_UPDATED )); then
    return 0
  fi
  apt-get update
  LUMI_APT_LISTS_UPDATED=1
  export LUMI_APT_LISTS_UPDATED
}

install_missing_apt_packages() {
  local label="$1"
  shift
  local requirement package
  local -a missing=()
  for requirement in "$@"; do
    package="${requirement%%|*}"
    if ! package_is_satisfied "$requirement"; then
      missing+=("$package")
    fi
  done

  if ((${#missing[@]} == 0)); then
    echo "$label: all required packages are installed; skipping APT."
    return 0
  fi
  if [[ -n "${OFFLINE_COMMIT:-}" ]]; then
    echo "Offline installation requires these preinstalled packages: ${missing[*]}" >&2
    return 1
  fi

  echo "$label: installing missing packages: ${missing[*]}"
  apt_update_for_install
  apt-get install --yes --no-install-recommends "${missing[@]}"
}

