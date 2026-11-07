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
printf 'binary interruption fixture\n' > "$project/lifecycle-binary-interruption"
git -C "$project" add lifecycle-binary-interruption
git -C "$project" commit --quiet -m 'binary interruption fixture'
interrupt_commit="$(git -C "$project" rev-parse HEAD)"
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

cache_entry="$cache_root/$update_commit"
cache_archive="$cache_entry/bundle.tar.gz"
cp -p "$cache_archive" /tmp/lumi-update-bundle.valid
cp -p "$cache_entry/verified.json" /tmp/lumi-update-verified.valid
cp -p "$cache_entry/bundle.sha256" /tmp/lumi-update-checksum.valid

mv "$cache_entry" /tmp/lumi-update-cache.valid
if PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$update_commit" \
  > /tmp/lumi-binary-update-missing-cache.log 2>&1; then
  printf 'offline update unexpectedly succeeded without a verified cached release\n' >&2
  exit 1
fi
mv /tmp/lumi-update-cache.valid "$cache_entry"
test "$(git_as_pi rev-parse HEAD)" = "$commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$commit"
grep -Fq 'requires an intact, previously verified cached release' /tmp/lumi-binary-update-missing-cache.log || {
  cat /tmp/lumi-binary-update-missing-cache.log >&2
  exit 1
}
test ! -e /tmp/lumi-cargo-build.log

chmod u+w "$cache_archive"
printf '%s\n' 'corrupt release bytes' >> "$cache_archive"
chmod 444 "$cache_archive"
if PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$update_commit" \
  > /tmp/lumi-binary-update-corrupt-cache.log 2>&1; then
  printf 'offline update unexpectedly accepted a modified verified archive\n' >&2
  exit 1
fi
cp -p /tmp/lumi-update-bundle.valid "$cache_archive"
chmod 444 "$cache_archive"
test "$(git_as_pi rev-parse HEAD)" = "$commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$commit"
grep -Fq 'previously verified release cache archive was modified' /tmp/lumi-binary-update-corrupt-cache.log || {
  cat /tmp/lumi-binary-update-corrupt-cache.log >&2
  exit 1
}
test ! -e /tmp/lumi-cargo-build.log

python3 - "$cache_entry" "$update_commit" <<'PY'
import copy
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

cache = Path(sys.argv[1])
commit = sys.argv[2]
archive_path = cache / "bundle.tar.gz"
temporary = cache / "bundle.invalid-target.tar.gz"
with tarfile.open(archive_path, "r:gz") as source, tarfile.open(temporary, "w:gz") as output:
    for original in source.getmembers():
        member = copy.copy(original)
        payload = source.extractfile(original) if original.isfile() else None
        if original.name == "release/manifest.json":
            assert payload is not None
            manifest = json.loads(payload.read())
            manifest["target"]["architecture"] = "armv7l"
            encoded = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
            member.size = len(encoded)
            output.addfile(member, io.BytesIO(encoded))
        else:
            output.addfile(member, payload)
        if payload is not None:
            payload.close()
temporary.replace(archive_path)
digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
verified_path = cache / "verified.json"
verified = json.loads(verified_path.read_text(encoding="utf-8"))
verified["archive_sha256"] = digest
verified_path.write_text(json.dumps(verified, sort_keys=True, separators=(",", ":")) + "\n")
verified_path.chmod(0o444)
(cache / "bundle.sha256").write_text(
    f"{digest}  lumistripe-linux-aarch64-{commit}.tar.gz\n", encoding="ascii"
)
(cache / "bundle.sha256").chmod(0o444)
archive_path.chmod(0o444)
PY

if PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$update_commit" \
  > /tmp/lumi-binary-update-wrong-target.log 2>&1; then
  printf 'offline update unexpectedly accepted a wrong-architecture release manifest\n' >&2
  exit 1
fi
cp -p /tmp/lumi-update-bundle.valid "$cache_archive"
cp -p /tmp/lumi-update-verified.valid "$cache_entry/verified.json"
cp -p /tmp/lumi-update-checksum.valid "$cache_entry/bundle.sha256"
chmod 444 "$cache_archive" "$cache_entry/verified.json" "$cache_entry/bundle.sha256"
test "$(git_as_pi rev-parse HEAD)" = "$commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$commit"
grep -Fq 'must be aarch64-unknown-linux-gnu' /tmp/lumi-binary-update-wrong-target.log
test ! -e /tmp/lumi-cargo-build.log

# The verifier and updater must never turn integrity failures into source builds.
test ! -e /tmp/lumi-apt-used

touch /tmp/lumi-fail-next-release-activation
if PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$update_commit" \
  > /tmp/lumi-binary-update-activation-failure.log 2>&1; then
  printf 'injected release activation failure unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'injected release activation failure' /tmp/lumi-binary-update-activation-failure.log
grep -Fq 'Update failed during Release activation (commit-matched release pointer and CLI activation' \
  /tmp/lumi-binary-update-activation-failure.log
grep -Fq 'The previous release' /tmp/lumi-binary-update-activation-failure.log
test ! -e /tmp/lumi-fail-next-release-activation
test "$(git_as_pi rev-parse HEAD)" = "$commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$commit"
if compgen -G "$release_root/.current.*.tmp" >/dev/null; then
  printf 'failed release activation left a temporary pointer behind\n' >&2
  exit 1
fi
test ! -e /tmp/lumi-apt-used

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

rm -f /tmp/lumi-web-restart-blocked /tmp/lumi-web-restart-parent-pid
touch /tmp/lumi-block-next-web-restart
PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$update_commit" \
  > /tmp/lumi-binary-update-success.log 2>&1 &
update_pid=$!
for _attempt in $(seq 1 300); do
  [ -e /tmp/lumi-web-restart-blocked ] && break
  python3 -c 'import time; time.sleep(0.02)'
done
if [ ! -e /tmp/lumi-web-restart-blocked ]; then
  touch /tmp/lumi-release-web-restart
  wait "$update_pid" || true
  cat /tmp/lumi-binary-update-success.log >&2
  printf 'binary update did not reach the injected service restart barrier\n' >&2
  exit 1
fi

if printf 'y\n' | PYTHONPATH=/tmp/lumi-test-python "$installed_admin" spotify disable \
  > /tmp/lumi-provider-concurrent-update.log 2>&1; then
  touch /tmp/lumi-release-web-restart
  wait "$update_pid" || true
  printf 'provider switch unexpectedly ran while an application update held the shared lock\n' >&2
  exit 1
fi
grep -Fq 'an update is running; retry the provider change after it finishes' \
  /tmp/lumi-provider-concurrent-update.log
grep -Fqx 'LUMI_SPOTIFY_BACKEND=librespot' /etc/lumistripe/lumistripe-web.env
touch /tmp/lumi-release-web-restart
if ! wait "$update_pid"; then
  cat /tmp/lumi-binary-update-success.log >&2
  exit 1
fi
test "$(git_as_pi rev-parse HEAD)" = "$update_commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$update_commit"
seed_verified_cache "$fixture_release" "$interrupt_commit" interrupted-update

rm -f /tmp/lumi-web-restart-blocked /tmp/lumi-web-restart-parent-pid
touch /tmp/lumi-block-next-web-restart
PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$interrupt_commit" \
  > /tmp/lumi-binary-update-interrupted.log 2>&1 &
interrupted_update_pid=$!
for _attempt in $(seq 1 300); do
  [ -e /tmp/lumi-web-restart-blocked ] && break
  python3 -c 'import time; time.sleep(0.02)'
done
if [ ! -e /tmp/lumi-web-restart-blocked ]; then
  touch /tmp/lumi-release-web-restart
  wait "$interrupted_update_pid" || true
  cat /tmp/lumi-binary-update-interrupted.log >&2
  printf 'interrupted update did not reach the injected service restart barrier\n' >&2
  exit 1
fi
update_shell_pid="$(cat /tmp/lumi-web-restart-parent-pid)"
kill -TERM "$update_shell_pid"
touch /tmp/lumi-release-web-restart
if wait "$interrupted_update_pid"; then
  printf 'interrupted binary update unexpectedly reported success\n' >&2
  exit 1
else
  interrupted_status=$?
fi
test "$interrupted_status" -eq 143
grep -Fq 'Update interrupted by TERM.' /tmp/lumi-binary-update-interrupted.log || {
  cat /tmp/lumi-binary-update-interrupted.log >&2
  exit 1
}
grep -Fq 'The previous release' /tmp/lumi-binary-update-interrupted.log
test "$(git_as_pi rev-parse HEAD)" = "$update_commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$update_commit"
test -d "$release_root/releases/$interrupt_commit"

if ! PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$interrupt_commit" \
  > /tmp/lumi-binary-update-recovered.log 2>&1; then
  cat /tmp/lumi-binary-update-recovered.log >&2
  exit 1
fi
test "$(git_as_pi rev-parse HEAD)" = "$interrupt_commit"
test "$(readlink -f "$release_root/current")" = "$release_root/releases/$interrupt_commit"

: > /tmp/lumi-systemctl.log
if ! PYTHONPATH=/tmp/lumi-test-python "$installed_admin" update \
  --user pi --project-dir "$project" --mode binary --offline "$interrupt_commit" \
  > /tmp/lumi-binary-update-repeat.log 2>&1; then
  cat /tmp/lumi-binary-update-repeat.log >&2
  exit 1
fi
! grep -Fqx 'restart lumistripe-web.service' /tmp/lumi-systemctl.log
test ! -e /tmp/lumi-apt-used

printf 'isolated binary install lifecycle passed\n'
