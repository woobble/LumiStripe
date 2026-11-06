#!/usr/bin/env bash

# Phase timings use Linux /proc/uptime, which is based on monotonic uptime and
# is not affected by wall-clock corrections. The summary contains phase names
# and elapsed milliseconds only; it never includes command arguments or env.
declare -Ag LUMI_PHASE_START_MS=()
declare -Ag LUMI_PHASE_TOTAL_MS=()
declare -ag LUMI_PHASE_ORDER=()
declare -gi LUMI_PHASE_SEQUENCE=0

lumi_monotonic_ms() {
  awk '{ printf "%.0f\n", $1 * 1000 }' /proc/uptime
}

lumi_phase_start() {
  local phase="$1"
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
  printf '    Complete: %s\n' "$phase"
}

lumi_phase_report() {
  local phase elapsed_ms
  ((${#LUMI_PHASE_ORDER[@]} > 0)) || return 0
  printf '\nInstallation/update timing (monotonic elapsed):\n'
  for phase in "${LUMI_PHASE_ORDER[@]}"; do
    elapsed_ms="${LUMI_PHASE_TOTAL_MS[$phase]:-0}"
    printf '  %-30s %d.%03d s\n' \
      "$phase" "$((elapsed_ms / 1000))" "$((elapsed_ms % 1000))"
  done
}

