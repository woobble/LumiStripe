#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

usage() {
  cat <<'EOF'
Usage: release_smoke.sh --release-dir DIR --commit SHA [--image IMAGE]
                        [--inside-container] [--require-manifest]
EOF
}

fail() {
  printf 'Release artifact smoke test failed: %s\n' "$1" >&2
  exit 1
}

release_dir=
commit=
image_name=
inside_container=0
require_manifest=0
SMOKE_WEB_PID=
while (($#)); do
  option="$1"
  shift
  case "$option" in
    --help)
      usage
      exit 0
      ;;
    --release-dir|--commit|--image)
      [[ $# -gt 0 ]] || fail "missing value for $option"
      value="$1"
      shift
      case "$option" in
        --release-dir) release_dir="$value" ;;
        --commit) commit="$value" ;;
        --image) image_name="$value" ;;
      esac
      ;;
    --inside-container) inside_container=1 ;;
    --require-manifest) require_manifest=1 ;;
    *)
      usage >&2
      fail "unexpected argument: $option"
      ;;
  esac
done

[[ -n "$release_dir" ]] || fail '--release-dir is required'
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || fail '--commit must be a full lowercase Git SHA'
[[ -d "$release_dir" && ! -L "$release_dir" ]] || fail "release directory is not a real directory: $release_dir"
release_dir="$(cd -- "$release_dir" && pwd -P)"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
artifact_tool="${LUMI_RELEASE_ARTIFACT_TOOL:-$script_dir/release_artifact.sh}"
validate_release() {
  [[ -f "$artifact_tool" && ! -L "$artifact_tool" ]] || fail "trusted release verifier is missing: $artifact_tool"
  if (( inside_container )); then
    LUMI_RELEASE_HELPER="$release_dir/lumistripe-deploy-helper" \
      timeout --signal=TERM --kill-after=5s 120s "$artifact_tool" validate \
        --release-dir "$release_dir" --commit "$commit" --origin github --check-runtime || \
        fail 'release directory failed manifest, checksum, or target validation'
  else
    timeout --signal=TERM --kill-after=5s 120s "$artifact_tool" validate \
      --release-dir "$release_dir" --commit "$commit" --origin github --check-runtime || \
      fail 'release directory failed manifest, checksum, or target validation'
  fi
}

check_runtime() {
  local manifest="$release_dir/manifest.json"
  local minimum binary file_output header linked versions maximum
  [[ "$(uname -m)" == aarch64 ]] || fail "smoke container is not ARM64: $(uname -m)"
  local -a binaries=()
  if [[ -f "$manifest" && ! -L "$manifest" ]]; then
    validate_release
    minimum="$(jq -er '.runtime_abi.glibc_minimum' "$manifest")" || fail 'release manifest has no glibc minimum'
    mapfile -t binaries < <(jq -er '.binaries[] | select(type == "string")' "$manifest") || \
      fail 'release manifest has an invalid binary list'
  else
    (( require_manifest == 0 )) || fail '--require-manifest was set but the release manifest is missing'
    [[ -f "$release_dir/source-commit" && ! -L "$release_dir/source-commit" ]] || \
      fail 'unpackaged release has no source-commit marker'
    [[ "$(<"$release_dir/source-commit")" == "$commit" ]] || \
      fail 'unpackaged source-commit marker does not match the requested commit'
    minimum="$(getconf GNU_LIBC_VERSION | awk '{print $2}')" || fail 'could not read the runtime glibc version'
    binaries=(lumistripe-web lumistripe-engine lumistripe lumistripe-deploy-helper)
  fi
  [[ "$minimum" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail 'release manifest has an invalid glibc minimum'
  (( ${#binaries[@]} == 4 )) || fail 'release manifest has an unexpected binary count'
  local -a glibc_versions=()
  for binary in "${binaries[@]}"; do
    [[ "$binary" =~ ^(lumistripe-web|lumistripe-engine|lumistripe|lumistripe-deploy-helper)$ ]] || \
      fail "release manifest contains an unexpected binary name: $binary"
    [[ -f "$release_dir/$binary" && ! -L "$release_dir/$binary" && -x "$release_dir/$binary" ]] || \
      fail "required executable is missing or not executable: $release_dir/$binary"
    file_output="$(file -- "$release_dir/$binary")" || fail "file could not inspect $binary"
    [[ "$file_output" == *'ELF 64-bit'* && "$file_output" == *'ARM aarch64'* ]] || \
      fail "$binary is not an AArch64 ELF executable: $file_output"
    header="$(readelf -h -- "$release_dir/$binary")" || fail "readelf could not inspect $binary"
    grep -Eq '^[[:space:]]*Machine:[[:space:]]+AArch64[[:space:]]*$' <<< "$header" || \
      fail "$binary ELF header does not declare AArch64"
    linked="$(ldd -- "$release_dir/$binary" 2>&1)" || fail "ldd could not inspect $binary: $linked"
    [[ "$linked" != *'not found'* ]] || fail "$binary has unresolved runtime libraries: $linked"
    versions="$(readelf --version-info -- "$release_dir/$binary")" || fail "readelf could not inspect GLIBC requirements for $binary"
    while IFS= read -r version; do
      [[ -z "$version" ]] || glibc_versions+=("$version")
    done < <(grep -Eo 'GLIBC_[0-9]+(\.[0-9]+){1,2}' <<< "$versions" | sed 's/^GLIBC_//' | sort -Vu)
  done
  (( ${#glibc_versions[@]} > 0 )) || fail 'release binaries declare no dynamic GLIBC requirements'
  maximum="$(printf '%s\n' "${glibc_versions[@]}" | sort -V | tail -n 1)"
  [[ "$(printf '%s\n%s\n' "$maximum" "$minimum" | sort -V | head -n 1)" == "$maximum" ]] || \
    fail "release requires glibc $maximum, above its manifest minimum $minimum"

  local tool
  for tool in cargo rustc bun cc gcc make; do
    if command -v "$tool" >/dev/null 2>&1; then
      fail "release runtime unexpectedly contains source-build tool: $tool"
    fi
  done
}

wait_for_process_exit() {
  local pid="$1"
  timeout --signal=TERM --kill-after=2s 20s tail --pid="$pid" -f /dev/null
}

exercise_web() {
  local config_dir="$1"
  local log_file="$config_dir/web.log"
  local port base index_file headers_file body_file asset_path content_type
  local tries=0
  mkdir -p -- "$config_dir"
  port=$((20000 + RANDOM % 30000))
  base="http://127.0.0.1:$port"
  while (( tries < 20 )); do
    if ! timeout 1s bash -c ':</dev/tcp/127.0.0.1/$1' _ "$port" >/dev/null 2>&1; then
      break
    fi
    port=$((20000 + RANDOM % 30000))
    base="http://127.0.0.1:$port"
    ((tries += 1))
  done
  (( tries < 20 )) || fail 'could not select an unused loopback port'

  HOME="$config_dir" XDG_CONFIG_HOME="$config_dir" \
    LUMI_SPOTIFY_BACKEND=disabled RUST_LOG=warn \
    "$release_dir/lumistripe-web" \
      --simulate --host 127.0.0.1 --port "$port" \
      --static-dir "$release_dir/static" \
      --settings-file "$config_dir/settings.json" \
      --spotify-backend disabled >"$log_file" 2>&1 &
  SMOKE_WEB_PID=$!

  local healthy=0
  for ((tries = 0; tries < 150; tries += 1)); do
    kill -0 "$SMOKE_WEB_PID" 2>/dev/null || fail 'web process exited before health check'
    if curl --disable --fail --silent --show-error --max-time 2 "$base/api/health" \
      -o "$config_dir/health.json" 2>/dev/null && jq -e '. == {status:"ok"}' "$config_dir/health.json" >/dev/null 2>&1; then
      healthy=1
      break
    fi
    sleep 0.2
  done
  (( healthy )) || fail 'web health check timed out'

  index_file="$config_dir/index.html"
  headers_file="$config_dir/headers"
  curl --disable --fail --silent --show-error --max-time 5 -D "$headers_file" \
    -o "$index_file" "$base/" || fail 'frontend index request failed'
  grep -qi '^content-type:[[:space:]]*text/html' "$headers_file" || fail 'frontend index has an unexpected content type'
  grep -Fq 'LumiStripe' "$index_file" || fail 'frontend index did not contain LumiStripe'
  grep -Eo '(/assets/[^"?#[:space:]]+)' "$index_file" | sort -u > "$config_dir/assets.list" || true
  [[ -s "$config_dir/assets.list" ]] || fail 'frontend index does not reference static assets'
  while IFS= read -r asset_path; do
    [[ "$asset_path" == /assets/* && "$asset_path" != *'..'* ]] || fail "frontend index contains an unsafe asset path: $asset_path"
    body_file="$config_dir/asset.body"
    headers_file="$config_dir/asset.headers"
    curl --disable --fail --silent --show-error --max-time 5 -D "$headers_file" \
      -o "$body_file" "$base$asset_path" || fail "frontend asset request failed: $asset_path"
    [[ -s "$body_file" ]] || fail "frontend asset is empty: $asset_path"
    content_type="$(awk 'tolower($0) ~ /^content-type:/ { sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit }' "$headers_file")"
    [[ "${content_type,,}" != text/html* ]] || fail "frontend asset returned HTML: $asset_path"
  done < "$config_dir/assets.list"

  kill -TERM "$SMOKE_WEB_PID" || fail 'could not signal web process for shutdown'
  if wait_for_process_exit "$SMOKE_WEB_PID"; then
    wait "$SMOKE_WEB_PID" || fail 'web server exited unsuccessfully during shutdown'
  else
    fail 'web server did not shut down within 20 seconds after SIGTERM'
  fi
  SMOKE_WEB_PID=
}

run_inside_container() {
  if (( require_manifest )) && [[ ! -f "$release_dir/manifest.json" ]]; then
    fail '--require-manifest was set but the release manifest is missing'
  fi
  check_runtime
  local config_dir
  config_dir="$(mktemp -d /tmp/lumistripe-release-smoke.XXXXXX)" || fail 'could not create smoke-test temporary directory'
  cleanup_runtime() {
    if [[ -n "$SMOKE_WEB_PID" ]] && kill -0 "$SMOKE_WEB_PID" 2>/dev/null; then
      kill -TERM "$SMOKE_WEB_PID" 2>/dev/null || true
      timeout --signal=TERM --kill-after=2s 10s tail --pid="$SMOKE_WEB_PID" -f /dev/null >/dev/null 2>&1 || true
      kill -KILL "$SMOKE_WEB_PID" 2>/dev/null || true
      wait "$SMOKE_WEB_PID" 2>/dev/null || true
      SMOKE_WEB_PID=
      if [[ -s "$config_dir/web.log" ]]; then
        tail -c 4000 -- "$config_dir/web.log" >&2 || true
      fi
    fi
    rm -rf -- "$config_dir"
  }
  trap cleanup_runtime EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  local cli_output helper_output
  cli_output="$(timeout --signal=TERM --kill-after=5s 20s "$release_dir/lumistripe" list-animations)" || \
    fail 'Rust CLI failed to start or list animations'
  [[ -n "${cli_output//[[:space:]]/}" ]] || fail 'Rust CLI returned an empty animation catalog'
  helper_output="$(timeout --signal=TERM --kill-after=5s 20s "$release_dir/lumistripe-deploy-helper" --help)" || \
    fail 'deployment helper failed to start'
  [[ "$helper_output" == *'Restricted LumiStripe deployment utilities'* ]] || \
    fail 'deployment helper did not display its command interface'
  exercise_web "$config_dir"
}

if (( inside_container )); then
  [[ -z "$image_name" ]] || fail '--image cannot be used with --inside-container'
  run_inside_container
else
  [[ -n "$image_name" ]] || fail '--image is required when running the host-side Docker driver'
  [[ "$release_dir" != *','* && "$release_dir" != *$'\n'* && "$release_dir" != *$'\r'* ]] || \
    fail 'release directory contains unsupported Docker mount characters'
  command -v docker >/dev/null 2>&1 || fail 'Docker is required for host-side release smoke testing'
  script_inside=/usr/local/libexec/lumistripe/release_smoke.sh
  container_name="lumistripe-release-smoke-$$-$RANDOM"
  cleanup_container() {
    docker rm -f "$container_name" >/dev/null 2>&1 || true
  }
  trap cleanup_container EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  docker run --rm --name "$container_name" --network none --read-only \
    --cap-drop ALL --security-opt no-new-privileges \
    --user "$(id -u):$(id -g)" \
    --env LUMI_RELEASE_HELPER=/release/lumistripe-deploy-helper \
    --mount "type=bind,src=$release_dir,dst=/release,readonly" \
    --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
    --entrypoint /bin/bash "$image_name" "$script_inside" \
    --inside-container --require-manifest --release-dir /release --commit "$commit" || \
    fail 'release smoke container failed'
fi

printf 'Release artifact smoke test passed.\n'
