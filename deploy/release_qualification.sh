#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

usage() {
  cat <<'EOF'
Usage: release_qualification.sh --project-root DIR [--release-dir DIR] --commit SHA \
  --deployment-tests RESULT --frontend-tests RESULT --rust-x86-tests RESULT \
  --application-tests RESULT --stability-tests RESULT --rust-arm64-tests RESULT \
  --release-source RESULT --release-smoke RESULT --trixie-runtime RESULT \
  --ubuntu-2604-runtime RESULT --artifact-integrity RESULT --provenance RESULT \
  --output-dir DIR [--target-architecture ARCH] [--target-triple TRIPLE] \
  [--build-environment NAME]
EOF
}

fail() {
  printf 'could not generate release qualification report: %s\n' "$1" >&2
  exit 1
}

project_root=
release_dir=
commit=
deployment_tests=
frontend_tests=
rust_x86_tests=
application_tests=
stability_tests=
rust_arm64_tests=
release_source=
release_smoke=
trixie_runtime=
ubuntu_2604_runtime=
artifact_integrity=
provenance=
target_architecture=aarch64
target_triple=aarch64-unknown-linux-gnu
build_environment='not established'
output_dir=

while (($#)); do
  option="$1"
  shift
  case "$option" in
    --help)
      usage
      exit 0
      ;;
    --project-root|--release-dir|--commit|--deployment-tests|--frontend-tests|\
    --rust-x86-tests|--application-tests|--stability-tests|--rust-arm64-tests|\
    --release-source|--release-smoke|--trixie-runtime|--ubuntu-2604-runtime|\
    --artifact-integrity|--provenance|--target-architecture|--target-triple|\
    --build-environment|--output-dir)
      [[ $# -gt 0 ]] || fail "missing value for $option"
      value="$1"
      shift
      case "$option" in
        --project-root) project_root="$value" ;;
        --release-dir) release_dir="$value" ;;
        --commit) commit="$value" ;;
        --deployment-tests) deployment_tests="$value" ;;
        --frontend-tests) frontend_tests="$value" ;;
        --rust-x86-tests) rust_x86_tests="$value" ;;
        --application-tests) application_tests="$value" ;;
        --stability-tests) stability_tests="$value" ;;
        --rust-arm64-tests) rust_arm64_tests="$value" ;;
        --release-source) release_source="$value" ;;
        --release-smoke) release_smoke="$value" ;;
        --trixie-runtime) trixie_runtime="$value" ;;
        --ubuntu-2604-runtime) ubuntu_2604_runtime="$value" ;;
        --artifact-integrity) artifact_integrity="$value" ;;
        --provenance) provenance="$value" ;;
        --target-architecture) target_architecture="$value" ;;
        --target-triple) target_triple="$value" ;;
        --build-environment) build_environment="$value" ;;
        --output-dir) output_dir="$value" ;;
      esac
      ;;
    *)
      usage >&2
      fail "unexpected argument: $option"
      ;;
  esac
done

for required in project_root commit deployment_tests frontend_tests rust_x86_tests \
  application_tests stability_tests rust_arm64_tests release_source release_smoke \
  trixie_runtime ubuntu_2604_runtime artifact_integrity provenance output_dir; do
  [[ -n "${!required}" ]] || fail "missing required option for $required"
done

[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || fail 'source commit must be a full lowercase Git SHA'
command -v jq >/dev/null 2>&1 || fail 'jq is required'
[[ -d "$project_root" && ! -L "$project_root" ]] || fail 'project root must be a real directory'
project_root="$(cd -- "$project_root" && pwd -P)"
[[ -f "$project_root/rust-toolchain.toml" && ! -L "$project_root/rust-toolchain.toml" ]] || \
  fail 'rust-toolchain.toml is missing or is not a regular file'
[[ -f "$project_root/.bun-version" && ! -L "$project_root/.bun-version" ]] || \
  fail '.bun-version is missing or is not a regular file'

rust_channel="$(awk '
  /^[[:space:]]*channel[[:space:]]*=/ {
    if ($0 !~ /^[[:space:]]*channel[[:space:]]*=[[:space:]]*"[0-9]+\.[0-9]+\.[0-9]+"[[:space:]]*$/ || found++) {
      invalid = 1
      next
    }
    line = $0
    sub(/^[^"]*"/, "", line)
    sub(/".*$/, "", line)
    channel = line
  }
  END {
    if (invalid || found != 1 || channel == "") exit 1
    print channel
  }
' "$project_root/rust-toolchain.toml")" || fail 'rust-toolchain.toml has a missing, duplicate, or invalid pinned channel'
bun_lines=()
mapfile -t bun_lines < "$project_root/.bun-version"
(( ${#bun_lines[@]} == 1 )) || fail '.bun-version must contain one version'
bun_version="${bun_lines[0]}"
[[ "$bun_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail '.bun-version is malformed'

normalize_result() {
  local value="$1"
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]' | sed -E 's/[_-]/ /g; s/^[[:space:]]+//; s/[[:space:]]+$//; s/[[:space:]]+/ /g')"
  case "$value" in
    success) printf 'passed\n' ;;
    failure|cancelled) printf 'failed\n' ;;
    skipped) printf 'not verified\n' ;;
    passed|failed|'not verified') printf '%s\n' "$value" ;;
    *) fail "invalid check result: $1" ;;
  esac
}

deployment_tests="$(normalize_result "$deployment_tests")"
frontend_tests="$(normalize_result "$frontend_tests")"
rust_x86_tests="$(normalize_result "$rust_x86_tests")"
application_tests="$(normalize_result "$application_tests")"
stability_tests="$(normalize_result "$stability_tests")"
rust_arm64_tests="$(normalize_result "$rust_arm64_tests")"
release_source="$(normalize_result "$release_source")"
release_smoke="$(normalize_result "$release_smoke")"
trixie_runtime="$(normalize_result "$trixie_runtime")"
ubuntu_2604_runtime="$(normalize_result "$ubuntu_2604_runtime")"
artifact_integrity="$(normalize_result "$artifact_integrity")"
provenance="$(normalize_result "$provenance")"

glibc_minimum='not measured'
if [[ -n "$release_dir" ]]; then
  [[ -d "$release_dir" && ! -L "$release_dir" ]] || fail 'release directory must be a real directory'
  manifest="$release_dir/manifest.json"
  build_info="$(dirname -- "$release_dir")/release-build-info.json"
  for input in "$manifest" "$build_info"; do
    [[ -f "$input" && ! -L "$input" ]] || fail "required release metadata is missing or not a regular file: $input"
    input_size="$(wc -c < "$input")"
    (( input_size <= 1048576 )) || fail "release metadata exceeds the 1 MiB limit: $input"
    jq -e 'type == "object"' "$input" >/dev/null 2>&1 || fail "release metadata is malformed JSON: $input"
  done
  jq -e --arg commit "$commit" --arg rust "$rust_channel" --arg bun "$bun_version" '
    .source_commit == $commit and
    .target.triple == "aarch64-unknown-linux-gnu" and
    .target.architecture == "aarch64" and
    .target.abi == "GNU/Linux" and
    .runtime_abi.distribution == "debian-trixie" and
    (.runtime_abi.glibc_minimum | type == "string" and test("^[0-9]+\\.[0-9]+(\\.[0-9]+)?$")) and
    .toolchain.rust == $rust and
    .toolchain.bun == $bun and
    .toolchain.builder == "debian:trixie-slim" and
    (.binaries == ["lumistripe-web", "lumistripe-engine", "lumistripe", "lumistripe-deploy-helper"]) and
    (.checksums | type == "object")
  ' "$manifest" >/dev/null 2>&1 || fail 'release manifest does not match the requested commit, target, or toolchain'
  jq -e '
    .architecture == "aarch64" and
    .target == "aarch64-unknown-linux-gnu" and
    .distribution == "debian-trixie" and
    (.glibc_minimum | type == "string" and test("^[0-9]+\\.[0-9]+(\\.[0-9]+)?$"))
  ' "$build_info" >/dev/null 2>&1 || fail 'release build metadata has an invalid architecture, target, or ABI'
  manifest_glibc="$(jq -er '.runtime_abi.glibc_minimum' "$manifest")" || fail 'manifest glibc minimum is missing'
  glibc_minimum="$(jq -er '.glibc_minimum' "$build_info")" || fail 'build glibc minimum is missing'
  [[ "$manifest_glibc" == "$glibc_minimum" ]] || fail 'manifest and build metadata glibc minimum do not match'
  target_architecture=aarch64
  target_triple=aarch64-unknown-linux-gnu
  build_environment="$(jq -er '.toolchain.builder' "$manifest")" || fail 'manifest build environment is missing'
fi

mkdir -p -- "$output_dir" || fail "could not create output directory: $output_dir"
[[ -d "$output_dir" && ! -L "$output_dir" ]] || fail 'output directory must be a real directory'
json_tmp="$(mktemp "$output_dir/.release-qualification.json.XXXXXX")" || fail 'could not create JSON staging file'
markdown_tmp="$(mktemp "$output_dir/.release-qualification.md.XXXXXX")" || {
  rm -f -- "$json_tmp"
  fail 'could not create Markdown staging file'
}
cleanup() {
  rm -f -- "${json_tmp:-}" "${markdown_tmp:-}"
}
trap cleanup EXIT

jq -nS \
  --arg commit "$commit" \
  --arg rust "$rust_channel" \
  --arg bun "$bun_version" \
  --arg architecture "$target_architecture" \
  --arg triple "$target_triple" \
  --arg build_environment "$build_environment" \
  --arg glibc_minimum "$glibc_minimum" \
  --arg deployment "$deployment_tests" \
  --arg frontend "$frontend_tests" \
  --arg rust_x86 "$rust_x86_tests" \
  --arg application "$application_tests" \
  --arg stability "$stability_tests" \
  --arg rust_arm64 "$rust_arm64_tests" \
  --arg release_source "$release_source" \
  --arg release_smoke "$release_smoke" \
  --arg trixie "$trixie_runtime" \
  --arg ubuntu "$ubuntu_2604_runtime" \
  --arg integrity "$artifact_integrity" \
  --arg provenance "$provenance" '
  def check($name; $result; $verification; $evidence):
    {name: $name, result: $result, verification: $verification, evidence: $evidence};
  {
    schema_version: 1,
    source_commit: $commit,
    toolchain: {rust: $rust, bun: $bun},
    target: {
      architecture: $architecture,
      triple: $triple,
      build_environment: $build_environment,
      glibc_minimum: $glibc_minimum
    },
    checks: [
      check("Deployment regression tests"; $deployment; "automated"; "GitHub-hosted deployment-tests job"),
      check("Frontend contract, lint, tests, and build"; $frontend; "automated"; "GitHub-hosted frontend job"),
      check("Rust formatting, Clippy, and x86_64 build"; $rust_x86; "automated"; "GitHub-hosted rust-engine job"),
      check("Application REST, WebSocket, simulator, and engine tests"; $application; "automated"; "Rust workspace test suite on the GitHub-hosted x86_64 runner"),
      check("Opt-in simulator stability profile"; $stability; "automated when selected"; "Bounded synthetic audio, mocked Soloist, REST, resource, and shutdown profile"),
      check("Rust ARM64 tests and build"; $rust_arm64; "automated"; "GitHub-hosted rust-engine-arm64 job"),
      check("Release source commit and immutable-tag eligibility"; $release_source; "automated"; "Publisher checkout, ARM64 runner, and existing build-tag guard"),
      check("Debian Trixie ARM64 runtime compatibility"; $trixie; "automated"; "AArch64 Debian Trixie runtime container"),
      check("Actual packaged ARM64 release startup smoke"; $release_smoke; "automated"; "Packaged release directory, CLI, deployment helper, loopback web health, frontend assets, and clean shutdown"),
      check("Ubuntu 26.04 LTS ARM64 runtime compatibility"; $ubuntu; "automated"; "Ubuntu 26.04 ARM64 container using the packaged release directory"),
      check("Raspberry Pi OS Trixie ARM64 userspace compatibility"; "not verified"; "static inspection"; "Raspberry Pi OS is Trixie-based; no OS image is executed in hosted CI"),
      check("Release manifest, source commit, ELF architecture, and dependencies"; $integrity; "automated"; "Rust release-artifact validation and Bash ARM64 smoke checks"),
      check("Sigstore build provenance"; $provenance; "automated"; "gh attestation verify pinned to ci.yml and the source commit"),
      check("Live Spotify authentication and playback"; "not verified"; "manual acceptance required"; "No Spotify account or live provider is used by CI"),
      check("Physical Raspberry Pi hardware behavior"; "not verified"; "manual acceptance required"; "GPIO/SPI, Bluetooth radio, audio devices, power, and thermal behavior are not exercised")
    ],
    known_limitations: [
      "Raspberry Pi OS Trixie is not independently run in a hosted container; its hardware remains unverified.",
      "Ubuntu 24.04 LTS is outside the prebuilt binary support matrix; source mode is available.",
      "Live Spotify authentication and physical audio/LED behavior are manual acceptance checks."
    ]
  }
' > "$json_tmp" || fail 'could not serialize qualification JSON'

jq -r '
  def cell: tostring | gsub("[\\r\\n]"; " ") | split("\\") | join("\\\\") | split("|") | join("\\|");
  def inline: cell | split("`") | join("\\`") | "`" + . + "`";
  "# LumiStripe release qualification: \(.source_commit | inline)",
  "",
  "- Rust toolchain: \(.toolchain.rust | inline)",
  "- Bun: \(.toolchain.bun | inline)",
  "- Target: \(.target.triple | inline) (\(.target.architecture | cell))",
  "- Build environment: \(.target.build_environment | inline)",
  "- Declared glibc minimum: \(.target.glibc_minimum | inline)",
  "",
  "| Validation | Result | Evidence type | Evidence |",
  "| --- | --- | --- | --- |",
  (.checks[] | "| \(.name | cell) | \(.result | cell) | \(.verification | cell) | \(.evidence | cell) |"),
  "",
  "## Known limitations",
  "",
  (.known_limitations[] | "- \(.)")
' "$json_tmp" > "$markdown_tmp" || fail 'could not render qualification Markdown'

chmod 644 -- "$json_tmp" "$markdown_tmp" || fail 'could not set report permissions'
mv -fT -- "$markdown_tmp" "$output_dir/release-qualification.md" || fail 'could not atomically replace Markdown report'
markdown_tmp=
mv -fT -- "$json_tmp" "$output_dir/release-qualification.json" || fail 'could not atomically replace JSON report'
json_tmp=
