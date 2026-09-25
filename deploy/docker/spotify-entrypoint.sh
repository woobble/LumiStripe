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

exec /usr/local/bin/soloist \
  --device-name "${LUMI_SPOTIFY_DEVICE_NAME:-LumiStripe}" \
  --api-key "$api_key" \
  --ws "${LUMI_SPOTIFY_WS:-127.0.0.1:9090}" \
  --pipewire-device "${LUMI_SPOTIFY_PIPEWIRE_DEVICE:-lumistripe_spotify}" \
  --data-dir "${LUMI_SPOTIFY_DATA_DIR:-/var/lib/lumistripe/soloist}" \
  --cache-dir "${LUMI_SPOTIFY_CACHE_DIR:-/var/lib/lumistripe/soloist-cache}"
