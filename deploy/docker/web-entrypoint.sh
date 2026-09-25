#!/bin/sh
set -eu

: "${LUMI_PAIRING_CODE:?LUMI_PAIRING_CODE is required}"

if [ -n "${LUMI_SPI_DEVICE_2:-}" ]; then
  exec /opt/lumistripe/.venv/bin/lumistripe-web \
    --hardware \
    --pairing-code "${LUMI_PAIRING_CODE}" \
    --spi-device "${LUMI_SPI_DEVICE:-/dev/spidev0.0}" \
    --chip "${LUMI_GPIO_CHIP:-/dev/gpiochip0}" \
    --settings-file "${LUMI_SETTINGS_FILE:-/var/lib/lumistripe/settings.json}" \
    --spi-device-2 "$LUMI_SPI_DEVICE_2" \
    "$@"
fi

exec /opt/lumistripe/.venv/bin/lumistripe-web \
  --hardware \
  --pairing-code "${LUMI_PAIRING_CODE}" \
  --spi-device "${LUMI_SPI_DEVICE:-/dev/spidev0.0}" \
  --chip "${LUMI_GPIO_CHIP:-/dev/gpiochip0}" \
  --settings-file "${LUMI_SETTINGS_FILE:-/var/lib/lumistripe/settings.json}" \
  "$@"
