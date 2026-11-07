#!/usr/bin/env bash

# Phase timings use Linux /proc/uptime, which is based on monotonic uptime and
# is not affected by wall-clock corrections. The summary contains phase names
# and elapsed milliseconds only; it never includes command arguments or env.
declare -Ag LUMI_PHASE_START_MS=()
declare -Ag LUMI_PHASE_TOTAL_MS=()
declare -ag LUMI_PHASE_ORDER=()
declare -gi LUMI_PHASE_SEQUENCE=0
LUMI_ACTIVE_PHASE="preflight"

lumi_phase_component() {
  case "${LUMI_ACTIVE_PHASE:-preflight}" in
    "Package setup") printf '%s\n' "system packages and runtime libraries" ;;
    Configuration) printf '%s\n' "deployment configuration and service files" ;;
    "Release activation") printf '%s\n' "commit-matched release pointer and CLI activation" ;;
    "Source build") printf '%s\n' "Rust and frontend source build" ;;
    Soloist) printf '%s\n' "Spotify Soloist provider" ;;
    "Service health") printf '%s\n' "systemd service startup and health" ;;
    "between deployment phases") printf '%s\n' "deployment finalization" ;;
    *) printf '%s\n' "deployment preflight" ;;
  esac
}

lumi_monotonic_ms() {
  awk '{ printf "%.0f\n", $1 * 1000 }' /proc/uptime
}

lumi_phase_start() {
  local phase="$1"
  LUMI_ACTIVE_PHASE="$phase"
  if [[ -z "${LUMI_PHASE_TOTAL_MS[$phase]+present}" ]]; then
    LUMI_PHASE_ORDER+=("$phase")
    LUMI_PHASE_TOTAL_MS["$phase"]=0
  fi
  LUMI_PHASE_SEQUENCE=$((LUMI_PHASE_SEQUENCE + 1))
  printf '[%d] %s\n' "$LUMI_PHASE_SEQUENCE" "$phase"
  LUMI_PHASE_START_MS["$phase"]="$(lumi_monotonic_ms)"
}

lumi_phase_end() {
  local phase="$1"
  local start_ms="${LUMI_PHASE_START_MS[$phase]:-}"
  local end_ms
  [[ -n "$start_ms" ]] || return 0
  end_ms="$(lumi_monotonic_ms)"
  LUMI_PHASE_TOTAL_MS["$phase"]=$((
    ${LUMI_PHASE_TOTAL_MS[$phase]:-0} + end_ms - start_ms
  ))
  unset 'LUMI_PHASE_START_MS[$phase]'
  if [[ "$LUMI_ACTIVE_PHASE" == "$phase" ]]; then
    LUMI_ACTIVE_PHASE="between deployment phases"
  fi
  printf '    Complete: %s\n' "$phase"
}

lumi_phase_report() {
  local phase elapsed_ms now_ms start_ms
  ((${#LUMI_PHASE_ORDER[@]} > 0)) || return 0
  now_ms="$(lumi_monotonic_ms)"
  printf '\nInstallation/update timing (monotonic elapsed):\n'
  for phase in "${LUMI_PHASE_ORDER[@]}"; do
    elapsed_ms="${LUMI_PHASE_TOTAL_MS[$phase]:-0}"
    start_ms="${LUMI_PHASE_START_MS[$phase]:-}"
    if [[ -n "$start_ms" ]]; then
      elapsed_ms=$((elapsed_ms + now_ms - start_ms))
    fi
    printf '  %-30s %d.%03d s\n' \
      "$phase" "$((elapsed_ms / 1000))" "$((elapsed_ms % 1000))"
  done
}

