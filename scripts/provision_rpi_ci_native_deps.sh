#!/usr/bin/env bash
set -euo pipefail

packages=(
  build-essential
  ca-certificates
  curl
  pkg-config
  libasound2-dev
  libclang-dev
  libdbus-1-dev
  libpipewire-0.3-dev
  libpulse-dev
  pulseaudio-utils
)

native_dependencies_ready() {
  command -v cc >/dev/null 2>&1 \
    && command -v make >/dev/null 2>&1 \
    && command -v curl >/dev/null 2>&1 \
    && command -v pkg-config >/dev/null 2>&1 \
    && command -v pactl >/dev/null 2>&1 \
    && test -s "${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}" \
    && test -f "${LUMI_RPI_NATIVE_DEPS_ROOT:-}/usr/include/alsa/asoundlib.h" \
    && pkg-config --exists dbus-1 \
    && pkg-config --exists libpipewire-0.3 \
    && pkg-config --exists libpulse
}

if native_dependencies_ready; then
  echo "Raspberry Pi native build dependencies are already available."
  exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Native build dependencies are missing and apt-get is unavailable." >&2
  exit 1
fi

if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  sudo apt-get update
  sudo apt-get install --yes --no-install-recommends "${packages[@]}"
  exit 0
fi

if test "$(id -u)" -eq 0; then
  apt-get update
  apt-get install --yes --no-install-recommends "${packages[@]}"
  exit 0
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "dpkg-deb is required to unpack native packages without root." >&2
  exit 1
fi

task_temp="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/lumistripe-rpi-native-deps"
apt_temp="$task_temp/apt"
root="$task_temp/root"
mkdir -p "$apt_temp/lists/partial" "$apt_temp/archives/partial" "$root"
chmod 755 "$task_temp" "$apt_temp" "$apt_temp/lists" \
  "$apt_temp/lists/partial" "$apt_temp/archives" "$apt_temp/archives/partial" "$root"

apt_options=(
  -o "Dir::State::lists=$apt_temp/lists"
  -o "Dir::Cache::archives=$apt_temp/archives"
  -o "Dir::Cache::pkgcache=$apt_temp/pkgcache.bin"
  -o "Dir::Cache::srcpkgcache=$apt_temp/srcpkgcache.bin"
  -o "APT::Sandbox::User=$(id -un)"
  -o "Debug::NoLocking=1"
)

echo "Fetching signed native packages into $root (no system package changes)."
apt-get "${apt_options[@]}" update
apt-get "${apt_options[@]}" install \
  --download-only --reinstall --yes --no-install-recommends "${packages[@]}"

shopt -s nullglob
archives=("$apt_temp"/archives/*.deb)
if ((${#archives[@]} == 0)); then
  echo "apt-get did not download any native package archives." >&2
  exit 1
fi
for archive in "${archives[@]}"; do
  dpkg-deb --extract "$archive" "$root"
done

# Development packages contain unversioned linker symlinks. When their
# runtime library is already installed on the host, point a dangling extracted
# symlink at that host copy so the overlay remains linkable without copying it.
for library_root in "$root/lib" "$root/usr/lib"; do
  if [[ -d "$library_root" ]]; then
    while IFS= read -r -d '' link; do
      [[ -e "$link" ]] && continue
      target="$(readlink "$link")"
      [[ "$target" = /* ]] && continue
      for system_library_dir in \
        /lib/aarch64-linux-gnu \
        /usr/lib/aarch64-linux-gnu \
        /lib \
        /usr/lib; do
        if [[ -e "$system_library_dir/$target" ]]; then
          ln -sfn "$system_library_dir/$target" "$link"
          break
        fi
      done
    done < <(find "$library_root" -type l -print0)
  fi
done

real_pkg_config="$(command -v pkg-config || true)"
if [[ -z "$real_pkg_config" ]]; then
  real_pkg_config="$root/usr/bin/pkg-config"
fi
if [[ ! -x "$real_pkg_config" ]]; then
  echo "The downloaded packages did not provide a usable pkg-config command." >&2
  exit 1
fi

pkg_config_dirs=()
for base in "$root/usr/lib" "$root/usr/share"; do
  if [[ -d "$base" ]]; then
    while IFS= read -r directory; do
      pkg_config_dirs+=("$directory")
    done < <(find "$base" -type d -name pkgconfig -print)
  fi
done
if ((${#pkg_config_dirs[@]} == 0)); then
  echo "The downloaded packages did not provide pkg-config metadata." >&2
  exit 1
fi

wrapper_dir="$task_temp/bin"
mkdir -p "$wrapper_dir"
cat > "$wrapper_dir/pkg-config" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail

root="${LUMI_RPI_NATIVE_DEPS_ROOT:?}"
real_pkg_config="${LUMI_RPI_SYSTEM_PKG_CONFIG:?}"
output="$("$real_pkg_config" "$@")" || exit $?
if [[ -z "$output" ]]; then
  exit 0
fi

read -r -a flags <<< "$output"
for flag in "${flags[@]}"; do
  case "$flag" in
    -I/usr/*|-L/usr/*)
      kind="${flag:0:2}"
      path="${flag:2}"
      overlay="$root$path"
      if [[ -d "$overlay" ]]; then
        printf '%s%s ' "$kind" "$overlay"
      fi
      printf '%s ' "$flag"
      ;;
    *)
      printf '%s ' "$flag"
      ;;
  esac
done
printf '\n'
WRAPPER
chmod 755 "$wrapper_dir/pkg-config"

lib_dirs=()
for directory in \
  "$root/lib/aarch64-linux-gnu" \
  "$root/usr/lib/aarch64-linux-gnu" \
  "$root/usr/lib" \
  "$root/lib" \
  "$root"/usr/lib/llvm-*/lib; do
  if [[ -d "$directory" ]]; then
    lib_dirs+=("$directory")
  fi
done
joined_pkg_config_dirs="$(IFS=:; printf '%s' "${pkg_config_dirs[*]}")"
joined_lib_dirs="$(IFS=:; printf '%s' "${lib_dirs[*]}")"

export LUMI_RPI_NATIVE_DEPS_ROOT="$root"
export LUMI_RPI_SYSTEM_PKG_CONFIG="$real_pkg_config"
export PKG_CONFIG="$wrapper_dir/pkg-config"
export PKG_CONFIG_PATH="$joined_pkg_config_dirs${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export PKG_CONFIG_ALLOW_SYSTEM_CFLAGS=1
export PKG_CONFIG_ALLOW_SYSTEM_LIBS=1
export PATH="$wrapper_dir:$PATH:$root/usr/bin"
export C_INCLUDE_PATH="$root/usr/include${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}"
export CPLUS_INCLUDE_PATH="$root/usr/include${CPLUS_INCLUDE_PATH:+:$CPLUS_INCLUDE_PATH}"
export LIBRARY_PATH="$joined_lib_dirs${LIBRARY_PATH:+:$LIBRARY_PATH}"
export LD_LIBRARY_PATH="$joined_lib_dirs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

libclang_file="$(find "$root/usr/lib" -type f -o -type l 2>/dev/null \
  | while IFS= read -r file; do
      case "${file##*/}" in
        libclang.so|libclang-*.so) printf '%s\n' "$file"; break ;;
      esac
    done)"
if [[ -n "$libclang_file" ]]; then
  export LIBCLANG_PATH="${libclang_file%/*}"
fi

ca_bundle="$root/etc/ssl/certs/ca-certificates.crt"
if [[ -s "$ca_bundle" ]]; then
  export SSL_CERT_FILE="$ca_bundle"
fi

native_dependencies_ready || {
  echo "The temporary native package environment is incomplete." >&2
  exit 1
}

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    printf 'LUMI_RPI_NATIVE_DEPS_ROOT=%s\n' "$LUMI_RPI_NATIVE_DEPS_ROOT"
    printf 'LUMI_RPI_SYSTEM_PKG_CONFIG=%s\n' "$LUMI_RPI_SYSTEM_PKG_CONFIG"
    printf 'PKG_CONFIG=%s\n' "$PKG_CONFIG"
    printf 'PKG_CONFIG_PATH=%s\n' "$PKG_CONFIG_PATH"
    printf 'PKG_CONFIG_ALLOW_SYSTEM_CFLAGS=1\n'
    printf 'PKG_CONFIG_ALLOW_SYSTEM_LIBS=1\n'
    printf 'PATH=%s\n' "$PATH"
    printf 'C_INCLUDE_PATH=%s\n' "$C_INCLUDE_PATH"
    printf 'CPLUS_INCLUDE_PATH=%s\n' "$CPLUS_INCLUDE_PATH"
    printf 'LIBRARY_PATH=%s\n' "$LIBRARY_PATH"
    printf 'LD_LIBRARY_PATH=%s\n' "$LD_LIBRARY_PATH"
    if [[ -n "${LIBCLANG_PATH:-}" ]]; then
      printf 'LIBCLANG_PATH=%s\n' "$LIBCLANG_PATH"
    fi
    if [[ -n "${SSL_CERT_FILE:-}" ]]; then
      printf 'SSL_CERT_FILE=%s\n' "$SSL_CERT_FILE"
    fi
  } >> "$GITHUB_ENV"
fi

echo "Temporary Raspberry Pi native package environment is ready at $root."
