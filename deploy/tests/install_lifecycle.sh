#!/usr/bin/env bash
set -Eeuo pipefail

project=/opt/lumistripe
checkout_admin="$project/deploy/lumistripe-admin"
installed_admin=/usr/local/sbin/lumistripe-admin
base_commit="$(git -C "$project" rev-parse HEAD)"
git_as_pi() {
  runuser -u pi -- env HOME=/home/pi git -C "$project" "$@"
}

if ! LUMI_PAIRING_CODE=2468 "$checkout_admin" install \
  --user pi --project-dir "$project" --mode source \
  > /tmp/lumi-install-fresh.log 2>&1; then
  cat /tmp/lumi-install-fresh.log >&2
  exit 1
fi

release_root="$project/target/rust-deploy"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$base_commit"
test "$(cat "$release_root/current/source-commit")" = "$base_commit"
grep -Fqx 'LUMI_SPOTIFY_BACKEND=librespot' /etc/lumistripe/lumistripe-web.env
grep -Fqx 'LUMI_PAIRING_CODE=2468' /etc/lumistripe/lumistripe-web.env
test ! -e /etc/lumistripe/lumistripe-spotify.env
test ! -e /etc/systemd/system/lumistripe-spotify.service
grep -Fqx 'deployment_mode=source' /var/lib/lumistripe/install-state
grep -Fq "User=pi" /etc/systemd/system/lumistripe-web.service
grep -Fq "$project/deploy/lumistripe-web-launcher.sh" /etc/systemd/system/lumistripe-web.service
grep -Fq 'LUMI_DEPLOY_HELPER=' /etc/systemd/system/lumistripe-web.service
grep -Fqx 'build --frozen-lockfile' /tmp/lumi-bun-build.log || grep -Fq 'install --frozen-lockfile' /tmp/lumi-bun-build.log
for name in lumistripe-web lumistripe-engine lumistripe-deploy-helper lumistripe; do
  test -x "$release_root/current/$name"
done
test "$(wc -l < /tmp/lumi-cargo-build.log)" -eq 4
test ! -e /tmp/lumi-apt-used

printf '{"preserved":true}\n' > /home/pi/.config/lumistripe-settings.json
mkdir -p /home/pi/.local/share/lumistripe/spotify
chmod 700 /home/pi/.local/share/lumistripe/spotify
printf '{"device_id":"fixture-id"}\n' > /home/pi/.local/share/lumistripe/spotify/credentials.json
chown -R pi:pi /home/pi/.config /home/pi/.local/share/lumistripe
chmod 600 /home/pi/.local/share/lumistripe/spotify/credentials.json
: > /tmp/lumi-systemctl.log
if ! "$installed_admin" install --user pi --project-dir "$project" --mode source \
  > /tmp/lumi-install-repeat.log 2>&1; then
  cat /tmp/lumi-install-repeat.log >&2
  exit 1
fi
test "$(cat /home/pi/.config/lumistripe-settings.json)" = '{"preserved":true}'
test "$(cat /home/pi/.local/share/lumistripe/spotify/credentials.json)" = '{"device_id":"fixture-id"}'
! grep -Fqx 'restart lumistripe-web.service' /tmp/lumi-systemctl.log
test "$(wc -l < /tmp/lumi-cargo-build.log)" -eq 4

mkdir -p /etc/sudoers.d /home/pi/.local/bin
chmod 755 /etc/sudoers.d
printf 'LUMI_SPOTIFY_API_KEY=fixture-not-a-real-key\n' > /etc/lumistripe/lumistripe-spotify.env
chown pi:pi /etc/lumistripe/lumistripe-spotify.env
chmod 600 /etc/lumistripe/lumistripe-spotify.env
printf '#!/bin/sh\nexit 0\n' > /home/pi/.local/bin/soloist
chown pi:pi /home/pi/.local/bin/soloist
chmod 755 /home/pi/.local/bin/soloist
: > /tmp/lumi-systemctl.log
if ! printf 'y\n' | "$installed_admin" spotify use soloist > /tmp/lumi-provider-soloist.log 2>&1; then
  cat /tmp/lumi-provider-soloist.log >&2
  exit 1
fi
grep -Fqx 'LUMI_SPOTIFY_BACKEND=soloist' /etc/lumistripe/lumistripe-web.env
test -f /etc/systemd/system/lumistripe-spotify.service
grep -Fq 'User=pi' /etc/systemd/system/lumistripe-spotify.service
grep -Fqx 'active lumistripe-web.service' /tmp/lumi-systemctl-state
grep -Fqx 'active lumistripe-spotify.service' /tmp/lumi-systemctl-state
test "$(cat /home/pi/.local/share/lumistripe/spotify/credentials.json)" = '{"device_id":"fixture-id"}'
grep -Fqx 'LUMI_SPOTIFY_API_KEY=fixture-not-a-real-key' /etc/lumistripe/lumistripe-spotify.env

if ! printf 'y\n' | "$installed_admin" spotify disable > /tmp/lumi-provider-disabled.log 2>&1; then
  cat /tmp/lumi-provider-disabled.log >&2
  exit 1
fi
grep -Fqx 'LUMI_SPOTIFY_BACKEND=disabled' /etc/lumistripe/lumistripe-web.env
if grep -Fqx 'active lumistripe-spotify.service' /tmp/lumi-systemctl-state; then exit 1; fi
test -f /etc/systemd/system/lumistripe-spotify.service
grep -Fqx 'LUMI_SPOTIFY_API_KEY=fixture-not-a-real-key' /etc/lumistripe/lumistripe-spotify.env

if ! printf 'y\n' | "$installed_admin" spotify use soloist > /tmp/lumi-provider-reenable.log 2>&1; then
  cat /tmp/lumi-provider-reenable.log >&2
  exit 1
fi
grep -Fqx 'LUMI_SPOTIFY_BACKEND=soloist' /etc/lumistripe/lumistripe-web.env
grep -Fqx 'active lumistripe-spotify.service' /tmp/lumi-systemctl-state

touch /tmp/lumi-fail-provider-health
if printf 'y\n' | "$installed_admin" spotify use librespot > /tmp/lumi-provider-health-rollback.log 2>&1; then
  printf 'injected provider health failure unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'web service did not become healthy after switching providers' /tmp/lumi-provider-health-rollback.log || {
  cat /tmp/lumi-provider-health-rollback.log >&2
  exit 1
}
grep -Fqx 'LUMI_SPOTIFY_BACKEND=soloist' /etc/lumistripe/lumistripe-web.env
grep -Fqx 'active lumistripe-web.service' /tmp/lumi-systemctl-state
grep -Fqx 'active lumistripe-spotify.service' /tmp/lumi-systemctl-state

if ! printf 'y\n' | "$installed_admin" spotify use librespot > /tmp/lumi-provider-librespot.log 2>&1; then
  cat /tmp/lumi-provider-librespot.log >&2
  exit 1
fi
grep -Fqx 'LUMI_SPOTIFY_BACKEND=librespot' /etc/lumistripe/lumistripe-web.env
if grep -Fqx 'active lumistripe-spotify.service' /tmp/lumi-systemctl-state; then exit 1; fi
test "$(cat /home/pi/.local/share/lumistripe/spotify/credentials.json)" = '{"device_id":"fixture-id"}'
grep -Fqx 'LUMI_SPOTIFY_API_KEY=fixture-not-a-real-key' /etc/lumistripe/lumistripe-spotify.env

touch /tmp/lumi-fail-next-web-start
if printf 'y\n' | "$installed_admin" spotify use soloist > /tmp/lumi-provider-start-rollback.log 2>&1; then
  printf 'injected provider restart failure unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'web service did not become healthy after switching providers' /tmp/lumi-provider-start-rollback.log || {
  cat /tmp/lumi-provider-start-rollback.log >&2
  exit 1
}
grep -Fqx 'LUMI_SPOTIFY_BACKEND=librespot' /etc/lumistripe/lumistripe-web.env
grep -Fqx 'active lumistripe-web.service' /tmp/lumi-systemctl-state
if grep -Fqx 'active lumistripe-spotify.service' /tmp/lumi-systemctl-state; then exit 1; fi
test "$(cat /home/pi/.local/share/lumistripe/spotify/credentials.json)" = '{"device_id":"fixture-id"}'
grep -Fqx 'LUMI_SPOTIFY_API_KEY=fixture-not-a-real-key' /etc/lumistripe/lumistripe-spotify.env

: > /tmp/lumi-systemctl.log
if ! "$installed_admin" update --user pi --project-dir "$project" --mode source \
  > /tmp/lumi-update-current.log 2>&1; then
  cat /tmp/lumi-update-current.log >&2
  exit 1
fi
! grep -Fqx 'restart lumistripe-web.service' /tmp/lumi-systemctl.log
grep -Fq 'The checkout is unchanged; reusing the active source-built release.' /tmp/lumi-update-current.log

runuser -u pi -- env HOME=/home/pi /bin/sh -c \
  'cd /opt/lumistripe && git switch main >/dev/null && printf "second commit\\n" > lifecycle-version && git add lifecycle-version && git commit --quiet -m second'
second_commit="$(git_as_pi rev-parse main)"
runuser -u pi -- env HOME=/home/pi git -C "$project" switch --detach "$base_commit" >/dev/null
: > /tmp/lumi-systemctl.log
if ! "$installed_admin" update --user pi --project-dir "$project" --mode source \
  > /tmp/lumi-update-success.log 2>&1; then
  cat /tmp/lumi-update-success.log >&2
  exit 1
fi
test "$(git_as_pi rev-parse HEAD)" = "$second_commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$second_commit"
grep -Fq "Activated version-matched Rust application" /tmp/lumi-update-success.log
grep -Fqx 'restart lumistripe-web.service' /tmp/lumi-systemctl.log

runuser -u pi -- env HOME=/home/pi /bin/sh -c \
  'cd /opt/lumistripe && git switch main >/dev/null && printf "restart failure commit\\n" > lifecycle-version && git add lifecycle-version && git commit --quiet -m restart-failure'
restart_failure_commit="$(git_as_pi rev-parse main)"
runuser -u pi -- env HOME=/home/pi git -C "$project" switch --detach "$second_commit" >/dev/null
touch /tmp/lumi-fail-next-web-restart
if "$installed_admin" update --user pi --project-dir "$project" --mode source \
  > /tmp/lumi-update-rollback.log 2>&1; then
  printf 'injected service restart failure unexpectedly succeeded\n' >&2
  exit 1
fi
test "$(git_as_pi rev-parse HEAD)" = "$second_commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$second_commit"
grep -Fq 'Update failed during Service health' /tmp/lumi-update-rollback.log
grep -Fq 'The previous release' /tmp/lumi-update-rollback.log
test -d "$release_root/releases/$restart_failure_commit"

runuser -u pi -- env HOME=/home/pi /bin/sh -c \
  'cd /opt/lumistripe && git switch main >/dev/null && printf "health failure commit\\n" > lifecycle-version && git add lifecycle-version && git commit --quiet -m health-failure'
health_failure_commit="$(git_as_pi rev-parse main)"
runuser -u pi -- env HOME=/home/pi git -C "$project" switch --detach "$second_commit" >/dev/null
touch /tmp/lumi-fail-next-health
if "$installed_admin" update --user pi --project-dir "$project" --mode source \
  > /tmp/lumi-update-health-rollback.log 2>&1; then
  printf 'injected health timeout unexpectedly succeeded\n' >&2
  exit 1
fi
test "$(git_as_pi rev-parse HEAD)" = "$second_commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$second_commit"
grep -Fq 'Service did not become healthy in time.' /tmp/lumi-update-health-rollback.log
grep -Fq 'The previous release' /tmp/lumi-update-health-rollback.log
test -d "$release_root/releases/$health_failure_commit"
test ! -e /tmp/lumi-apt-used

printf 'isolated install and update lifecycle passed\n'
