#!/bin/sh
set -eu

api_key_file="${LUMI_SPOTIFY_API_KEY_FILE:-/run/secrets/spotify_api_key}"
if [ ! -r "$api_key_file" ]; then
  echo "Spotify API key file is not readable: $api_key_file" >&2
  exit 1
fi

api_key=$(cat "$api_key_file")
if [ -z "$api_key" ]; then
  echo "Spotify API key file is empty: $api_key_file" >&2
  exit 1
fi

spotify_sink_module=""
if [ "${LUMI_SPOTIFY_CREATE_SINK:-0}" = "1" ]; then
  if ! pactl list short sinks | awk '$2 == "lumistripe_spotify" { found = 1 } END { exit !found }'; then
    spotify_sink_module=$(pactl load-module module-null-sink sink_name=lumistripe_spotify)
  fi
fi

soloist_bin="${LUMI_SPOTIFY_BIN:-/usr/local/bin/soloist}"
set -- "$soloist_bin" \
  --device-name "${LUMI_SPOTIFY_DEVICE_NAME:-LumiStripe}" \
  --api-key "$api_key" \
  --ws "${LUMI_SPOTIFY_WS:-127.0.0.1:9090}" \
  --pipewire-device "${LUMI_SPOTIFY_PIPEWIRE_DEVICE:-lumistripe_spotify}" \
  --data-dir "${LUMI_SPOTIFY_DATA_DIR:-/var/lib/lumistripe/soloist}" \
  --cache-dir "${LUMI_SPOTIFY_CACHE_DIR:-/var/lib/lumistripe/soloist-cache}"

if [ -z "$spotify_sink_module" ]; then
  exec "$@"
fi

cleanup() {
  trap - EXIT INT TERM
  if [ -n "${soloist_pid:-}" ]; then
    # Give Soloist a short chance to close its PipeWire stream cleanly. A
    # container stop has a finite grace period, so force it down before that
    # expires and leave time to unload the sink from the host audio server.
    kill -TERM "$soloist_pid" 2>/dev/null || true
    attempts=0
    while kill -0 "$soloist_pid" 2>/dev/null && [ "$attempts" -lt 3 ]; do
      sleep 1
      attempts=$((attempts + 1))
    done
    if kill -0 "$soloist_pid" 2>/dev/null; then
      kill -KILL "$soloist_pid" 2>/dev/null || true
    fi
    wait "$soloist_pid" 2>/dev/null || true
  fi
  if [ -n "$spotify_sink_module" ]; then
    timeout --kill-after=1s 3s pactl unload-module "$spotify_sink_module" \
      >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT
trap 'exit 0' INT TERM
"$@" &
soloist_pid=$!
wait "$soloist_pid"
