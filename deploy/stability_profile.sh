#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

usage() {
  cat <<'EOF'
Usage: stability_profile.sh --binary FILE [--duration-seconds SECONDS] [--output FILE]
EOF
}

binary=
duration_seconds=180
output_file=
while (($#)); do
  option="$1"
  shift
  case "$option" in
    --help) usage; exit 0 ;;
    --binary|--duration-seconds|--output)
      [[ $# -gt 0 ]] || { printf 'missing value for %s\n' "$option" >&2; exit 2; }
      value="$1"
      shift
      case "$option" in
        --binary) binary="$value" ;;
        --duration-seconds) duration_seconds="$value" ;;
        --output) output_file="$value" ;;
      esac
      ;;
    *) usage >&2; printf 'unexpected argument: %s\n' "$option" >&2; exit 2 ;;
  esac
done
[[ -n "$binary" ]] || { printf '%s\n' '--binary is required' >&2; exit 2; }
[[ "$duration_seconds" =~ ^[0-9]+$ ]] && (( duration_seconds >= 30 && duration_seconds <= 1800 )) || {
  printf '%s\n' '--duration-seconds must be between 30 and 1800' >&2
  exit 2
}
for required in jq curl timeout setsid getconf; do
  command -v "$required" >/dev/null 2>&1 || { printf '%s is required\n' "$required" >&2; exit 1; }
done

driver="${LUMI_STABILITY_DRIVER:-$PWD/target/release/lumistripe-stability-driver}"
[[ -f "$binary" ]] && binary="$(readlink -f -- "$binary")"
temporary_root=
web_pid=
mock_pid=
audio_pid=
REPORT_WRITTEN=0
REPORT_RESULT=failed
PROFILE_START_NS=0
PROFILE_END_NS=0
API_REQUESTS=0
ANIMATION_CHANGES=0
PARAMETER_CHANGES=0
AUDIO_SOURCE_CHANGES=0
API_ERRORS=0
STATE_ERRORS=0
AUDIO_MESSAGES=0
AUDIO_DROPS=0
RENDER_DROPS=0
SAMPLES=0
CPU_FIRST=0
CPU_LAST=0
RSS_PEAK=0
THREADS_PEAK=0
FD_PEAK=0
SPOTIFY_CONNECTIONS=0
SPOTIFY_TRANSITIONS=0
SPOTIFY_INTERRUPTION_COUNT=0
SPOTIFY_API_STATES=0
RAPID_START_STOP=0
TASK_TERMINATION=failed
API_LAST_ERROR=
API_BODY=
COOKIE_JAR=
WEB_LOG=
MAIN_WEB_LOG=
MOCK_STATS_FILE=
AUDIO_STATS_FILE=
declare -a API_ERROR_EXAMPLES=()
declare -a VALIDATION_ERRORS=()
declare -a ANIMATION_NAMES=()
declare -A SPOTIFY_STATE_SET=()
declare -A SPOTIFY_LOGIN_SET=()
declare -A SPOTIFY_PLAYBACK_SET=()

stop_group() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    timeout --signal=TERM --kill-after=2s 10s tail --pid="$pid" -f /dev/null >/dev/null 2>&1 || true
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  stop_group "$audio_pid"
  stop_group "$web_pid"
  stop_group "$mock_pid"
  if [[ -n "$temporary_root" && -d "$temporary_root" ]]; then
    rm -rf -- "$temporary_root"
  fi
}

write_report() {
  (( REPORT_WRITTEN == 0 )) || return 0
  REPORT_WRITTEN=1
  local elapsed=0 cpu_delta=0 errors_json examples_json report_json
  local output_parent output_name output_tmp
  if (( PROFILE_START_NS > 0 )); then
    PROFILE_END_NS="$(date +%s%N 2>/dev/null || printf '%s' "$PROFILE_START_NS")"
    elapsed="$(awk -v start="$PROFILE_START_NS" -v end="$PROFILE_END_NS" 'BEGIN { printf "%.2f", (end-start)/1000000000 }')"
  fi
  cpu_delta="$(awk -v first="$CPU_FIRST" -v last="$CPU_LAST" 'BEGIN { value=last-first; if (value < 0) value=0; printf "%.3f", value }')"
  errors_json="$(printf '%s\n' "${VALIDATION_ERRORS[@]}" | jq -Rsc 'split("\n") | map(select(length > 0))')"
  examples_json="$(printf '%s\n' "${API_ERROR_EXAMPLES[@]}" | jq -Rsc 'split("\n") | map(select(length > 0))')"
  report_json="$(jq -nS \
    --arg result "$REPORT_RESULT" --argjson duration "$elapsed" \
    --argjson api_requests "$API_REQUESTS" --argjson animation_changes "$ANIMATION_CHANGES" \
    --argjson parameter_changes "$PARAMETER_CHANGES" --argjson audio_source_changes "$AUDIO_SOURCE_CHANGES" \
    --argjson samples "$SAMPLES" --argjson cpu_delta "$cpu_delta" \
    --argjson rss_peak "$RSS_PEAK" --argjson threads_peak "$THREADS_PEAK" --argjson fd_peak "$FD_PEAK" \
    --argjson audio_messages "$AUDIO_MESSAGES" --argjson audio_drops "$AUDIO_DROPS" \
    --argjson render_drops "$RENDER_DROPS" --argjson api_errors "$API_ERRORS" \
    --argjson api_examples "$examples_json" --argjson state_errors "$STATE_ERRORS" \
    --argjson spotify_connections "$SPOTIFY_CONNECTIONS" --argjson spotify_transitions "$SPOTIFY_TRANSITIONS" \
    --argjson spotify_states "$SPOTIFY_API_STATES" --argjson interruptions "$SPOTIFY_INTERRUPTION_COUNT" \
    --argjson rapid_cycles "$RAPID_START_STOP" --arg termination "$TASK_TERMINATION" \
    --argjson errors "$errors_json" --rawfile simulator_log "${MAIN_WEB_LOG:-/dev/null}" '
      {
        result: $result,
        duration_seconds: $duration,
        operations: {
          api_requests: $api_requests,
          animation_changes: $animation_changes,
          parameter_changes: $parameter_changes,
          audio_source_changes: $audio_source_changes
        },
        resource_samples: $samples,
        cpu_seconds_start_to_end: $cpu_delta,
        rss_bytes_peak: $rss_peak,
        thread_count_peak: $threads_peak,
        file_descriptors_peak: $fd_peak,
        audio_telemetry_messages: $audio_messages,
        audio_queue_dropped_samples_max: $audio_drops,
        render_queue_drops_max: $render_drops,
        api_error_count: $api_errors,
        api_error_examples: $api_examples,
        state_error_count: $state_errors,
        mock_spotify_connections: $spotify_connections,
        mock_spotify_state_transitions: $spotify_transitions,
        mock_spotify_api_states: $spotify_states,
        simulated_network_interruptions: $interruptions,
        rapid_start_stop_cycles: $rapid_cycles,
        task_termination: $termination,
        validation_errors: $errors,
        simulator_log_tail: ($simulator_log | .[-4000:])
      }
    ' )" || {
      printf '{"result":"failed","validation_errors":["could not serialize stability report"]}\n'
      return 1
    }
  printf '%s\n' "$report_json"
  if [[ -n "$output_file" ]]; then
    output_parent="$(dirname -- "$output_file")"
    output_name="$(basename -- "$output_file")"
    mkdir -p -- "$output_parent"
    output_tmp="$(mktemp "$output_parent/.${output_name}.XXXXXX")"
    printf '%s\n' "$report_json" > "$output_tmp"
    chmod 644 -- "$output_tmp"
    mv -fT -- "$output_tmp" "$output_file"
  fi
}

fatal_profile() {
  VALIDATION_ERRORS+=("$1")
  REPORT_RESULT=failed
  printf 'stability profile failed: %s\n' "$1" >&2
  write_report
  exit 1
}

on_error() {
  local status="$1" line="$2"
  trap - ERR
  VALIDATION_ERRORS+=("stability profile command failed at line $line (exit $status)")
  REPORT_RESULT=failed
  printf 'stability profile failed: command at line %s exited %s\n' "$line" "$status" >&2
  write_report
  exit "$status"
}

trap 'on_error "$?" "${BASH_LINENO[0]:-unknown}"' ERR
trap cleanup EXIT
trap 'VALIDATION_ERRORS+=("stability profile interrupted"); REPORT_RESULT=failed; printf "%s\\n" "stability profile interrupted" >&2; write_report; exit 130' INT
trap 'VALIDATION_ERRORS+=("stability profile terminated"); REPORT_RESULT=failed; printf "%s\\n" "stability profile terminated" >&2; write_report; exit 143' TERM

temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/lumistripe-stability.XXXXXX")" || fatal_profile 'could not create temporary directory'
API_BODY="$temporary_root/http-response.json"
COOKIE_JAR="$temporary_root/cookies.txt"
MOCK_STATS_FILE="$temporary_root/mock-stats.json"
AUDIO_STATS_FILE="$temporary_root/audio-stats.json"
[[ -f "$binary" && -x "$binary" ]] || fatal_profile "simulator binary is missing or not executable: $binary"
[[ -x "$driver" ]] || fatal_profile "Rust stability protocol driver is missing: $driver"

pick_port() {
  local candidate tries=0
  while (( tries < 50 )); do
    candidate=$((30000 + RANDOM % 30000))
    if ! timeout 1s bash -c ':</dev/tcp/127.0.0.1/$1' _ "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
    ((tries += 1))
  done
  return 1
}

wait_health() {
  local base="$1" pid="$2" max_tries="$3" index=0 health_file="$temporary_root/health.json"
  while (( index < max_tries )); do
    kill -0 "$pid" 2>/dev/null || return 1
    if curl --disable --fail --silent --show-error --max-time 2 "$base/api/health" \
      -o "$health_file" 2>/dev/null && jq -e '.status == "ok"' "$health_file" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
    ((index += 1))
  done
  return 1
}

start_web() {
  local config_dir="$1" backend="$2" spotify_uri="${3:-}" port
  port="$(pick_port)" || return 1
  mkdir -p -- "$config_dir"
  WEB_LOG="$config_dir/web.log"
  local -a arguments=(--simulate --host 127.0.0.1 --port "$port" --settings-file "$config_dir/settings.json" --spotify-backend "$backend")
  [[ -z "$spotify_uri" ]] || arguments+=(--spotify-ws "$spotify_uri")
  HOME="$config_dir" XDG_CONFIG_HOME="$config_dir" \
    LUMI_SPOTIFY_BACKEND="$backend" LUMI_PAIRING_CODE=2468 RUST_LOG=warn \
    setsid "$binary" "${arguments[@]}" >"$WEB_LOG" 2>&1 &
  web_pid=$!
  WEB_PORT="$port"
  WEB_BASE="http://127.0.0.1:$WEB_PORT"
}

stop_web_cleanly() {
  local pid="$web_pid"
  [[ -n "$pid" ]] || return 0
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || return 1
  timeout --signal=TERM --kill-after=2s 20s tail --pid="$pid" -f /dev/null || return 1
  wait "$pid"
  web_pid=
}

api_request() {
  local method="$1" path="$2" body="${3:-}" status curl_error="$temporary_root/curl-error.txt"
  local -a curl_arguments=(--disable --silent --show-error --max-time 4 --request "$method" --cookie "$COOKIE_JAR" --output "$API_BODY" --write-out '%{http_code}')
  [[ -z "$body" ]] || curl_arguments+=(--header 'Content-Type: application/json' --data "$body")
  if ! status="$(curl "${curl_arguments[@]}" "$WEB_BASE$path" 2>"$curl_error")"; then
    API_LAST_ERROR="$(cat -- "$curl_error" 2>/dev/null || true)"
    [[ -n "$API_LAST_ERROR" ]] || API_LAST_ERROR='HTTP request failed'
    return 1
  fi
  [[ "$status" == 200 ]] || { API_LAST_ERROR="HTTP $status"; return 1; }
  jq -e 'type == "object"' "$API_BODY" >/dev/null 2>&1 || { API_LAST_ERROR='response was not a JSON object'; return 1; }
  API_LAST_ERROR=
}

record_api_error() {
  local endpoint="$1"
  ((API_ERRORS += 1))
  if ((${#API_ERROR_EXAMPLES[@]} < 5)); then API_ERROR_EXAMPLES+=("$endpoint: $API_LAST_ERROR"); fi
}

pair_session() {
  local status cookie_value
  status="$(curl --disable --silent --show-error --max-time 4 \
    --request POST --header 'Content-Type: application/json' --data '{"code":"2468"}' \
    --cookie-jar "$COOKIE_JAR" --output "$API_BODY" --write-out '%{http_code}' "$WEB_BASE/api/auth/pair")" || return 1
  [[ "$status" == 200 ]] && jq -e '.authenticated == true' "$API_BODY" >/dev/null || return 1
  cookie_value="$(awk -F '\t' '$6 == "lumistripe_session" { print $7; exit }' "$COOKIE_JAR")"
  [[ -n "$cookie_value" ]] || return 1
  SESSION_COOKIE="lumistripe_session=$cookie_value"
}

proc_sample() {
  local pid="$1" stat_line stat_rest rss_kib threads fd_count ticks cpu
  [[ -r "/proc/$pid/stat" && -r "/proc/$pid/status" ]] || return 1
  stat_line="$(cat "/proc/$pid/stat")" || return 1
  stat_rest="${stat_line##*) }"
  local -a stat_fields=()
  read -r -a stat_fields <<< "$stat_rest"
  (( ${#stat_fields[@]} > 12 )) || return 1
  ticks=$((stat_fields[11] + stat_fields[12]))
  cpu="$(awk -v ticks="$ticks" -v rate="$CLK_TCK" 'BEGIN { printf "%.6f", ticks/rate }')"
  rss_kib="$(awk '$1 == "VmRSS:" { print $2; exit }' "/proc/$pid/status")"
  threads="$(awk '$1 == "Threads:" { print $2; exit }' "/proc/$pid/status")"
  fd_count="$(find "/proc/$pid/fd" -mindepth 1 -maxdepth 1 -type l 2>/dev/null | wc -l)"
  [[ "$rss_kib" =~ ^[0-9]+$ ]] || rss_kib=0
  [[ "$threads" =~ ^[0-9]+$ ]] || threads=0
  [[ "$fd_count" =~ ^[0-9]+$ ]] || fd_count=0
  ((SAMPLES += 1))
  (( SAMPLES != 1 )) || CPU_FIRST="$cpu"
  CPU_LAST="$cpu"
  (( rss_kib * 1024 > RSS_PEAK )) && RSS_PEAK=$((rss_kib * 1024))
  (( threads > THREADS_PEAK )) && THREADS_PEAK="$threads"
  (( fd_count > FD_PEAK )) && FD_PEAK="$fd_count"
}

api_body_file() { jq -nce "$@"; }

CLK_TCK="$(getconf CLK_TCK)"
[[ "$CLK_TCK" =~ ^[1-9][0-9]*$ ]] || fatal_profile 'could not determine the process clock tick rate'

mock_ready="$temporary_root/spotify-ready.json"
mock_log="$temporary_root/spotify.log"
setsid "$driver" mock-spotify --port 0 --ready-file "$mock_ready" \
  --stats-file "$MOCK_STATS_FILE" >"$mock_log" 2>&1 &
mock_pid=$!
mock_ready_count=0
while (( mock_ready_count < 100 )) && [[ ! -s "$mock_ready" ]]; do
  kill -0 "$mock_pid" 2>/dev/null || break
  sleep 0.1
  ((mock_ready_count += 1))
done
[[ -s "$mock_ready" ]] || fatal_profile "mock Spotify server failed to start: $(tail -c 1000 -- "$mock_log" 2>/dev/null || true)"
spotify_uri="$(jq -er '.uri | select(type == "string" and startswith("ws://127.0.0.1:"))' "$mock_ready")" || \
  fatal_profile 'mock Spotify server returned an invalid loopback URI'

start_web "$temporary_root/main" soloist "$spotify_uri" || fatal_profile 'could not start the simulator'
MAIN_WEB_LOG="$WEB_LOG"
wait_health "$WEB_BASE" "$web_pid" 250 || \
  fatal_profile "simulator did not become healthy: $(tail -c 2000 -- "$MAIN_WEB_LOG" 2>/dev/null || true)"
pair_session || fatal_profile 'simulator pairing did not establish an authenticated session'

api_request GET /api/animations || fatal_profile "animation catalog request failed: $API_LAST_ERROR"
mapfile -t ANIMATION_NAMES < <(jq -er '.items[] | select(.name | type == "string") | .name' "$API_BODY")
((${#ANIMATION_NAMES[@]} > 0)) || fatal_profile 'animation catalog returned no selectable animations'
api_request GET /api/animations/rainbow_cycle/settings || fatal_profile "animation settings request failed: $API_LAST_ERROR"
jq -e '.values | type == "object" and has("speed")' "$API_BODY" >/dev/null || \
  fatal_profile 'rainbow_cycle settings did not expose a speed control'
animation_values="$(jq -c '.values' "$API_BODY")"
api_request PUT /api/mode "$(api_body_file --arg mode dynamic '{mode:$mode}')" || \
  fatal_profile "could not enable dynamic mode: $API_LAST_ERROR"
api_request PUT /api/audio/source "$(api_body_file --arg source demo '{source:$source}')" || \
  fatal_profile "could not select demo audio: $API_LAST_ERROR"
API_REQUESTS=4

setsid "$driver" audio-telemetry --address "127.0.0.1:$WEB_PORT" \
  --cookie "$SESSION_COOKIE" --duration-seconds "$((duration_seconds + 10))" \
  --output "$AUDIO_STATS_FILE" >"$temporary_root/audio.log" 2>&1 &
audio_pid=$!

PROFILE_START_NS="$(date +%s%N)"
deadline=$(( $(date +%s) + duration_seconds ))
iteration=0
while (( $(date +%s) < deadline )); do
  kill -0 "$web_pid" 2>/dev/null || fatal_profile 'simulator exited during the stability profile'
  kill -0 "$audio_pid" 2>/dev/null || \
    fatal_profile "audio telemetry driver exited early: $(tail -c 1000 -- "$temporary_root/audio.log" 2>/dev/null || true)"

  endpoint=/api/animation
  body="$(jq -nc --arg name "${ANIMATION_NAMES[iteration % ${#ANIMATION_NAMES[@]}]}" '{name:$name}')"
  if api_request PUT "$endpoint" "$body"; then
    ((ANIMATION_CHANGES += 1))
    ((API_REQUESTS += 1))
  else
    record_api_error "$endpoint"
  fi

  endpoint=/api/animations/rainbow_cycle/settings
  speed=$((35 + iteration % 50))
  body="$(jq -nc --argjson values "$animation_values" --argjson speed "$speed" '{values:($values | .speed=$speed)}')"
  if api_request PUT "$endpoint" "$body"; then
    ((PARAMETER_CHANGES += 1))
    ((API_REQUESTS += 1))
  else
    record_api_error "$endpoint"
  fi

  endpoint=/api/audio/source
  source=demo
  (( iteration % 4 == 3 )) && source=off
  body="$(jq -nc --arg source "$source" '{source:$source}')"
  if api_request PUT "$endpoint" "$body"; then
    ((AUDIO_SOURCE_CHANGES += 1))
    ((API_REQUESTS += 1))
  else
    record_api_error "$endpoint"
  fi

  endpoint=/api/state
  if api_request GET "$endpoint"; then
    ((API_REQUESTS += 1))
    if jq -e '.error' "$API_BODY" >/dev/null 2>&1; then ((STATE_ERRORS += 1)); fi
    dropped="$(jq -r '(.output_dropped_frame_count // 0) | if type == "number" and . >= 0 then floor else 0 end' "$API_BODY")"
    (( dropped > RENDER_DROPS )) && RENDER_DROPS="$dropped"
  else
    record_api_error "$endpoint"
  fi

  endpoint=/api/audio/spotify
  if api_request GET "$endpoint"; then
    ((API_REQUESTS += 1))
    connected="$(jq -r 'if .connected == true then "true" else "false" end' "$API_BODY")"
    logged_in="$(jq -r 'if .logged_in == true then "true" else "false" end' "$API_BODY")"
    active="$(jq -r 'if .is_active == true then "true" else "false" end' "$API_BODY")"
    playback="$(jq -r 'if (.status | type) == "string" then .status else "unknown" end' "$API_BODY")"
    state_key="$(jq -cn --arg connected "$connected" --arg login "$logged_in" --arg active "$active" --arg status "$playback" '[$connected,$login,$active,$status] | @json')"
    SPOTIFY_STATE_SET["$state_key"]=1
    SPOTIFY_LOGIN_SET["$logged_in"]=1
    SPOTIFY_PLAYBACK_SET["$playback"]=1
  else
    record_api_error "$endpoint"
  fi

  if (( iteration % 2 == 0 )); then
    proc_sample "$web_pid" || fatal_profile 'could not read simulator process resource metrics'
  fi
  ((iteration += 1))
  sleep 0.75
done
PROFILE_END_NS="$(date +%s%N)"

stop_web_cleanly || fatal_profile 'simulator did not shut down cleanly within 20 seconds'
TASK_TERMINATION=clean

timeout --signal=TERM --kill-after=3s "$((duration_seconds + 30))s" tail --pid="$audio_pid" -f /dev/null || \
  fatal_profile 'audio telemetry client did not stop within its bounded runtime'
wait "$audio_pid" || fatal_profile "audio telemetry client failed: $(tail -c 1000 -- "$temporary_root/audio.log" 2>/dev/null || true)"
audio_pid=
[[ -s "$AUDIO_STATS_FILE" ]] || fatal_profile 'audio telemetry client did not write its statistics'
AUDIO_MESSAGES="$(jq -er '.messages | numbers' "$AUDIO_STATS_FILE")" || fatal_profile 'audio telemetry statistics are malformed'
AUDIO_DROPS="$(jq -er '.max_dropped_samples | numbers' "$AUDIO_STATS_FILE")" || fatal_profile 'audio telemetry statistics are malformed'

kill -TERM -- "-$mock_pid" 2>/dev/null || kill -TERM "$mock_pid" 2>/dev/null || true
timeout --signal=TERM --kill-after=2s 10s tail --pid="$mock_pid" -f /dev/null || \
  fatal_profile 'mock Spotify server did not stop within 10 seconds'
wait "$mock_pid" || fatal_profile 'mock Spotify server exited unsuccessfully'
mock_pid=
[[ -s "$MOCK_STATS_FILE" ]] || fatal_profile 'mock Spotify server did not write its statistics'
SPOTIFY_CONNECTIONS="$(jq -er '.connections | numbers' "$MOCK_STATS_FILE")" || fatal_profile 'mock Spotify statistics are malformed'
SPOTIFY_TRANSITIONS="$(jq -er '.transitions | numbers' "$MOCK_STATS_FILE")" || fatal_profile 'mock Spotify statistics are malformed'
SPOTIFY_INTERRUPTION_COUNT="$(jq -er '.interruptions | numbers' "$MOCK_STATS_FILE")" || fatal_profile 'mock Spotify statistics are malformed'
SPOTIFY_API_STATES="${#SPOTIFY_STATE_SET[@]}"

for cycle in 1 2; do
  quick_root="$temporary_root/quick-$cycle"
  start_web "$quick_root" disabled || fatal_profile "could not start rapid shutdown simulator cycle $cycle"
  quick_pid="$web_pid"
  quick_log="$WEB_LOG"
  wait_health "$WEB_BASE" "$quick_pid" 150 || \
    fatal_profile "rapid shutdown simulator cycle $cycle failed health: $(tail -c 1000 -- "$quick_log" 2>/dev/null || true)"
  stop_web_cleanly || fatal_profile "rapid shutdown simulator cycle $cycle did not stop cleanly"
  ((RAPID_START_STOP += 1))
done

if (( API_ERRORS > 0 || STATE_ERRORS > 0 )); then
  VALIDATION_ERRORS+=("observed $API_ERRORS API errors and $STATE_ERRORS dashboard state errors")
fi
(( AUDIO_MESSAGES > 0 )) || VALIDATION_ERRORS+=("received no audio telemetry")
if (( SPOTIFY_CONNECTIONS < 2 || SPOTIFY_INTERRUPTION_COUNT == 0 )); then
  VALIDATION_ERRORS+=("mock Spotify did not complete a reconnect after a simulated network interruption")
fi
if [[ -z "${SPOTIFY_LOGIN_SET[false]:-}" || -z "${SPOTIFY_LOGIN_SET[true]:-}" ||
      -z "${SPOTIFY_PLAYBACK_SET[playing]:-}" || -z "${SPOTIFY_PLAYBACK_SET[paused]:-}" ]]; then
  VALIDATION_ERRORS+=("mock Spotify transitions did not reach both login and playback states; observed ${#SPOTIFY_STATE_SET[@]} API states")
fi
(( SAMPLES > 0 )) || VALIDATION_ERRORS+=("did not collect process resource samples")
(( RAPID_START_STOP == 2 )) || VALIDATION_ERRORS+=("did not complete two rapid start-stop cycles")

if ((${#VALIDATION_ERRORS[@]} == 0)); then
  REPORT_RESULT=passed
  TASK_TERMINATION=clean
else
  REPORT_RESULT=failed
fi
write_report
if [[ "$REPORT_RESULT" != passed ]]; then
  printf 'stability profile failed: %s\n' "$(printf '%s; ' "${VALIDATION_ERRORS[@]}")" >&2
  exit 1
fi
