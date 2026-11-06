#!/usr/bin/env bash
set -Eeuo pipefail

project=/opt/lumistripe
checkout_admin="$project/deploy/lumistripe-admin"
installed_admin=/usr/local/sbin/lumistripe-admin
commit="$(git -C "$project" rev-parse HEAD)"
fixture_release=/tmp/lumi-prebuilt-release
build_info=/tmp/lumi-release-build-info.json
cache_root=/var/cache/lumistripe/releases
git_as_pi() {
  runuser -u pi -- env HOME=/home/pi git -C "$project" "$@"
}

mkdir -p "$fixture_release/static/assets"
cat > "$fixture_release/lumistripe-web" <<'EOF'
#!/bin/sh
if [ "${1:-}" = --help ]; then
  printf '%s\n' '--spotify-backend <SPOTIFY_BACKEND> [possible values: librespot, soloist, disabled]'
fi
EOF
cat > "$fixture_release/lumistripe-engine" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$fixture_release/lumistripe" <<'EOF'
#!/bin/sh
printf 'rainbow_cycle\n'
EOF
cat > "$fixture_release/lumistripe-deploy-helper" <<'EOF'
#!/bin/sh
case "${1:-}" in
  --help) printf '%s\n' 'Restricted LumiStripe deployment utilities' ;;
  update-state|soloist-check) exit 0 ;;
esac
EOF
printf '<!doctype html><html><body>LumiStripe binary fixture</body></html>\n' \
  > "$fixture_release/static/index.html"
printf 'window.fixture=true;\n' > "$fixture_release/static/assets/app.js"
chmod 755 \
  "$fixture_release/lumistripe-web" \
  "$fixture_release/lumistripe-engine" \
  "$fixture_release/lumistripe" \
  "$fixture_release/lumistripe-deploy-helper"
chmod 644 "$fixture_release/static/index.html" "$fixture_release/static/assets/app.js"
printf '{"architecture":"aarch64","target":"aarch64-unknown-linux-gnu","glibc_minimum":"2.38"}\n' \
  > "$build_info"
seed_verified_cache() {
  local source_release="$1" target_commit="$2" label="$3"
  local release_dir="/tmp/lumi-prebuilt-$label"
  local release_archive="/tmp/lumi-prebuilt-$label.tar.gz"
  cp -a "$source_release" "$release_dir"
  python3 "$project/deploy/release_artifact.py" package \
    --release-dir "$release_dir" \
    --output "$release_archive" \
    --commit "$target_commit" \
    --project-root "$project" \
    --build-info "$build_info" \
    > "/tmp/lumi-prebuilt-$label-package.log"

  python3 - "$target_commit" "$release_dir" "$release_archive" "$cache_root" <<'PY'
import hashlib
import json
import shutil
import sys
from pathlib import Path

commit, release_name, archive_name, root_name = sys.argv[1:]
release = Path(release_name)
archive = Path(archive_name)
cache = Path(root_name) / commit
cache.mkdir(parents=True, mode=0o700)
shutil.copyfile(archive, cache / "bundle.tar.gz")
shutil.copyfile(release / "manifest.json", cache / "manifest.json")
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
release_archive_name = f"lumistripe-linux-aarch64-{commit}.tar.gz"
(cache / "bundle.sha256").write_text(
    f"{digest}  {release_archive_name}\n", encoding="ascii"
)
(cache / "bundle.sigstore.json").write_text("{}\n", encoding="ascii")
(cache / "verified.json").write_text(
    json.dumps(
        {
            "source_commit": commit,
            "release_id": 1,
            "archive_sha256": digest,
            "verified": True,
            "verification": "isolated fixture representing a previously verified cache entry",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n",
    encoding="utf-8",
)
for path in cache.iterdir():
    path.chmod(0o444)
cache.chmod(0o700)
PY
}

seed_verified_cache "$fixture_release" "$commit" install

printf 'binary update fixture\n' > "$project/lifecycle-binary-version"
git -C "$project" add lifecycle-binary-version
git -C "$project" commit --quiet -m 'binary update fixture'
update_commit="$(git -C "$project" rev-parse HEAD)"
git -C "$project" switch --detach "$commit" >/dev/null
seed_verified_cache "$fixture_release" "$update_commit" update

# The host container may be x86_64. Keep the production platform checks intact,
# but make Python's architecture probe match the fake uname used by the shell
# installer so this fixture can exercise the ARM64-only binary install path.
mkdir -p /tmp/lumi-test-python
cat > /tmp/lumi-test-python/sitecustomize.py <<'PY'
import platform

platform.machine = lambda: "aarch64"
PY

if ! PYTHONPATH=/tmp/lumi-test-python LUMI_PAIRING_CODE=2468 "$checkout_admin" install \
  --user pi --project-dir "$project" --mode binary --offline "$commit" \
  > /tmp/lumi-install-binary.log 2>&1; then
  cat /tmp/lumi-install-binary.log >&2
  exit 1
fi

release_root="$project/target/rust-deploy"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$commit"
test "$(cat "$release_root/current/source-commit")" = "$commit"
python3 "$project/deploy/release_artifact.py" validate \
  --release-dir "$(readlink -f -- "$release_root/current")" \
  --commit "$commit" --origin github
grep -Fqx 'LUMI_SPOTIFY_BACKEND=librespot' /etc/lumistripe/lumistripe-web.env
grep -Fqx 'LUMI_PAIRING_CODE=2468' /etc/lumistripe/lumistripe-web.env
grep -Fqx 'deployment_mode=binary' /var/lib/lumistripe/install-state
test ! -e /etc/lumistripe/lumistripe-spotify.env
test ! -e /etc/systemd/system/lumistripe-spotify.service
grep -Fq 'User=pi' /etc/systemd/system/lumistripe-web.service
test -x /usr/local/libexec/lumistripe/network/lumistripe-network-helper
test ! -e /tmp/lumi-apt-used
test ! -e /tmp/lumi-bun-build.log
test ! -e /tmp/lumi-cargo-build.log
test -L /home/pi/.local/bin/lumistripe-cli
test "$(readlink /home/pi/.local/bin/lumistripe-cli)" = "$release_root/current/lumistripe"

touch /tmp/lumi-fail-next-web-restart
if PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$update_commit" \
  > /tmp/lumi-binary-update-rollback.log 2>&1; then
  printf 'injected binary update restart failure unexpectedly succeeded\n' >&2
  exit 1
fi
test "$(git_as_pi rev-parse HEAD)" = "$commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$commit"
grep -Fq 'Update failed during Service health' /tmp/lumi-binary-update-rollback.log
grep -Fq 'The previous release' /tmp/lumi-binary-update-rollback.log
test -d "$release_root/releases/$update_commit"
test ! -e /tmp/lumi-apt-used

if ! PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$update_commit" \
  > /tmp/lumi-binary-update-success.log 2>&1; then
  cat /tmp/lumi-binary-update-success.log >&2
  exit 1
fi
test "$(git_as_pi rev-parse HEAD)" = "$update_commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$update_commit"

: > /tmp/lumi-systemctl.log
if ! PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$update_commit" \
  > /tmp/lumi-binary-update-repeat.log 2>&1; then
  cat /tmp/lumi-binary-update-repeat.log >&2
  exit 1
fi
! grep -Fqx 'restart lumistripe-web.service' /tmp/lumi-systemctl.log
test ! -e /tmp/lumi-apt-used

printf 'isolated binary install lifecycle passed\n'
