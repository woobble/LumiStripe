#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${LUMI_PROJECT_DIR:-/home/woobb/lumistripe}"
ENV_DIR=/etc/lumistripe
ENV_FILE="$ENV_DIR/lumistripe-web.env"
SERVICE_FILE=/etc/systemd/system/lumistripe-web.service
NGINX_AVAILABLE=/etc/nginx/sites-available/led-controller
NGINX_ENABLED=/etc/nginx/sites-enabled/led-controller

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer as root (for example: sudo $0)." >&2
  exit 1
fi
[[ -d "$PROJECT_DIR" ]] || { echo "Project directory not found: $PROJECT_DIR" >&2; exit 1; }
[[ -x /home/woobb/.local/bin/uv ]] || { echo "uv not found at /home/woobb/.local/bin/uv" >&2; exit 1; }
[[ -x /home/woobb/.bun/bin/bun ]] || { echo "bun not found at /home/woobb/.bun/bin/bun" >&2; exit 1; }
[[ -f "$PROJECT_DIR/deploy/lumistripe-web.service" ]] || { echo "Missing service template" >&2; exit 1; }
[[ -f "$PROJECT_DIR/deploy/nginx/led-controller.conf" ]] || { echo "Missing nginx template" >&2; exit 1; }

for group in gpio spi audio; do
  getent group "$group" >/dev/null || { echo "Required group is missing: $group" >&2; exit 1; }
done

install -d -m 755 "$ENV_DIR" /var/backups/lumistripe /etc/nginx/sites-available /etc/nginx/sites-enabled
if [[ ! -e "$ENV_FILE" ]]; then
  install -o woobb -g woobb -m 600 "$PROJECT_DIR/deploy/lumistripe-web.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE. Edit LUMI_PAIRING_CODE, then run this installer again." >&2
  exit 1
fi
chown woobb:woobb "$ENV_FILE"
chmod 600 "$ENV_FILE"
# Read only the pairing value for validation; do not source a user-writable
# environment file as root.
PAIRING_CODE=$(awk -F= '$1 == "LUMI_PAIRING_CODE" { print $2; exit }' "$ENV_FILE")
[[ "$PAIRING_CODE" =~ ^[0-9]{4}$ ]] || { echo "LUMI_PAIRING_CODE must be exactly four digits in $ENV_FILE" >&2; exit 1; }
install -o root -g root -m 644 "$PROJECT_DIR/deploy/lumistripe-web.service" "$SERVICE_FILE"
install -o root -g root -m 644 "$PROJECT_DIR/deploy/nginx/led-controller.conf" "$NGINX_AVAILABLE"
ln -sfn "$NGINX_AVAILABLE" "$NGINX_ENABLED"

nginx -t
systemctl daemon-reload
systemctl enable lumistripe-web.service
systemctl restart nginx.service
systemctl restart lumistripe-web.service
systemctl --no-pager --full status lumistripe-web.service
echo "LumiStripe deployment installed successfully."
