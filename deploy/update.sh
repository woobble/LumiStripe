#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${LUMI_PROJECT_DIR:-/home/woobb/lumistripe}"
PROJECT_USER=woobb
TARGET_REF="${1:-origin/main}"
LOCK_FILE=/run/lock/lumistripe-update.lock
PREVIOUS_REF=
ROLLING_BACK=0

if [[ $EUID -ne 0 ]]; then
  echo "Run this updater as root (for example: sudo $0 origin/main)." >&2
  exit 1
fi
[[ -d "$PROJECT_DIR/.git" ]] || { echo "Not a Git checkout: $PROJECT_DIR" >&2; exit 1; }
command -v runuser >/dev/null || { echo "runuser is required" >&2; exit 1; }
command -v flock >/dev/null || { echo "flock is required (install util-linux)" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another LumiStripe update is already running." >&2; exit 1; }

as_user() { runuser -u "$PROJECT_USER" -H -- "$@"; }
git_user() { as_user git -C "$PROJECT_DIR" "$@"; }

rollback() {
  [[ -n "$PREVIOUS_REF" && "$ROLLING_BACK" -eq 0 ]] || return 0
  ROLLING_BACK=1
  echo "Update failed; rolling back to $PREVIOUS_REF..." >&2
  git_user switch --detach "$PREVIOUS_REF" || true
  as_user /home/woobb/.local/bin/uv sync --extra hardware-gain || true
  as_user /bin/bash -c "cd '$PROJECT_DIR/apps/lumistripe-web/frontend' && /home/woobb/.bun/bin/bun install --frozen-lockfile && /home/woobb/.bun/bin/bun run build" || true
  systemctl restart lumistripe-web.service || true
}
trap rollback ERR

STATUS=$(git_user status --porcelain)
[[ -z "$STATUS" ]] || { echo "Working tree is dirty; commit or stash changes before updating." >&2; exit 1; }
PREVIOUS_REF=$(git_user rev-parse HEAD)
git_user fetch --prune --tags origin
TARGET_COMMIT=$(git_user rev-parse --verify "${TARGET_REF}^{commit}")
git_user switch --detach "$TARGET_COMMIT"

as_user /home/woobb/.local/bin/uv sync --extra hardware-gain
as_user /bin/bash -c "cd '$PROJECT_DIR/apps/lumistripe-web/frontend' && /home/woobb/.bun/bin/bun install --frozen-lockfile && /home/woobb/.bun/bin/bun run build"
systemctl restart lumistripe-web.service
systemctl is-active --quiet lumistripe-web.service

for _ in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:8000/api/health >/dev/null; then
    echo "LumiStripe updated to $(git_user rev-parse --short HEAD)."
    trap - ERR
    exit 0
  fi
  sleep 1
done
echo "Service did not become healthy in time." >&2
exit 1
