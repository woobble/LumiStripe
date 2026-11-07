#!/usr/bin/env bash
set -Eeuo pipefail

useradd --create-home --shell /bin/bash pi
mkdir -p /opt/lumistripe/apps/lumistripe-web/frontend /home/pi/.bun/bin /home/pi/.cargo/bin /home/pi/.rustup

mv /usr/bin/uname /usr/bin/uname.real
cat > /usr/bin/uname <<'EOF'
#!/bin/sh
if [ "${1:-}" = -m ]; then cat /tmp/lumi-machine 2>/dev/null || printf aarch64; else exec /usr/bin/uname.real "$@"; fi
EOF
cat > /usr/bin/dpkg-query <<'EOF'
#!/bin/sh
printf 'install ok installed 1.0\n'
EOF
cat > /usr/bin/apt-get <<'EOF'
#!/bin/sh
touch /tmp/lumi-apt-used
printf 'unexpected APT invocation in lifecycle test\n' >&2
exit 91
EOF
cat > /usr/bin/apt-cache <<'EOF'
#!/bin/sh
exit 1
EOF
cat > /usr/bin/curl <<'EOF'
#!/bin/sh
if [ -e /tmp/lumi-fail-provider-health ]; then
  count=0
  [ ! -f /tmp/lumi-provider-health-count ] || count=$(cat /tmp/lumi-provider-health-count)
  count=$((count + 1))
  printf '%s\n' "$count" > /tmp/lumi-provider-health-count
  if [ "$count" -le 40 ]; then exit 22; fi
  rm -f /tmp/lumi-fail-provider-health
fi
if [ -e /tmp/lumi-fail-next-health ]; then
  count=0
  [ ! -f /tmp/lumi-health-count ] || count=$(cat /tmp/lumi-health-count)
  count=$((count + 1))
  printf '%s\n' "$count" > /tmp/lumi-health-count
  if [ "$count" -le 60 ]; then exit 22; fi
  rm -f /tmp/lumi-fail-next-health
fi
exit 0
EOF

for command in sleep sudo visudo loginctl rfkill bluetoothctl; do
  cat > "/usr/bin/$command" <<'EOF'
#!/bin/sh
exit 0
EOF
done
cat > /usr/sbin/nginx <<'EOF'
#!/bin/sh
exit 0
EOF
cat > /usr/bin/mkcert <<'EOF'
#!/bin/sh
case "${1:-}" in
  -version) printf 'v1.4.4\n' ;;
  -CAROOT) printf '%s/.local/share/mkcert\n' "$HOME" ;;
  -install) mkdir -p "$HOME/.local/share/mkcert" ;;
  -cert-file)
    cert="$2"
    key="$4"
    mkdir -p "$(dirname "$cert")"
    printf 'test certificate\n' > "$cert"
    printf 'test private key\n' > "$key"
    ;;
  *) exit 0 ;;
esac
EOF

cat > /home/pi/.bun/bin/bun <<'EOF'
#!/bin/sh
if [ "${1:-}" = --version ]; then printf '1.4.4\n'; exit 0; fi
printf '%s\n' "$*" >> /tmp/lumi-bun-build.log
if [ "${1:-}" = run ] && [ "${2:-}" = build ]; then
  mkdir -p ../static/assets
  printf '<!doctype html><html><body>LumiStripe lifecycle fixture</body></html>\n' > ../static/index.html
  printf 'window.fixture=true;\n' > ../static/assets/app.js
fi
EOF
cat > /home/pi/.cargo/bin/rustup <<'EOF'
#!/bin/sh
if [ "${1:-}" = run ] && [ "${2:-}" = 1.99.1 ] && [ "${3:-}" = rustc ]; then printf 'rustc 1.99.1\n'; exit 0; fi
exit 0
EOF
cat > /home/pi/.cargo/bin/cargo <<'EOF'
#!/usr/bin/python3
import os
import pathlib
import subprocess
import sys

args = sys.argv[1:]
if args[:2] == ["+1.99.1", "--version"]:
    print("cargo 1.99.1")
    raise SystemExit(0)
name = args[args.index("--bin") + 1]
manifest = pathlib.Path(args[args.index("--manifest-path") + 1])
project = manifest.parent
commit = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
output = pathlib.Path(os.environ["CARGO_TARGET_DIR"]) / "release" / name
output.parent.mkdir(parents=True, exist_ok=True)
helper = name == "lumistripe-deploy-helper"
web = name == "lumistripe-web"
body = "#!/bin/sh\n"
body += f"printf '%s\\n' '{commit}:{name}' >> /tmp/lumi-built-binaries.log\n"
if web:
    body += "if [ \"${1:-}\" = --help ]; then printf '%s\\n' '--spotify-backend [possible values: librespot,soloist,disabled]'; exit 0; fi\n"
if helper:
    body += "case \"${1:-}\" in --help) echo 'Restricted LumiStripe deployment utilities' ;; update-state|soloist-check) exit 0 ;; esac\n"
output.write_text(body, encoding="utf-8")
output.chmod(0o755)
with open("/tmp/lumi-cargo-build.log", "a", encoding="utf-8") as log:
    log.write(f"{commit} {name}\n")
EOF

chmod 755 \
  /usr/bin/uname /usr/bin/dpkg-query /usr/bin/apt-get /usr/bin/apt-cache \
  /usr/bin/curl /usr/bin/sleep /usr/bin/sudo /usr/bin/visudo \
  /usr/bin/loginctl /usr/bin/rfkill /usr/bin/bluetoothctl /usr/bin/mkcert \
  /usr/sbin/nginx \
  /home/pi/.bun/bin/bun /home/pi/.cargo/bin/rustup /home/pi/.cargo/bin/cargo
chown -R pi:pi /home/pi

cd /opt/lumistripe
git init --quiet --initial-branch=main
git config user.name 'LumiStripe lifecycle fixture'
git config user.email 'lifecycle@example.invalid'
git add -A
git commit --quiet -m 'lifecycle fixture base'
runuser -u pi -- env HOME=/home/pi git config --global \
  'url.file:///opt/lumistripe/.insteadOf' 'https://github.com/woobble/LumiStripe.git'
chown -R root:root /opt/lumistripe
chmod -R go-w /opt/lumistripe
printf 'aarch64\n' > /tmp/lumi-machine

cat > /usr/bin/systemctl <<'EOF'
#!/bin/sh
set -eu
if [ "${1:-}" = --user ]; then
  printf 'user %s\n' "$*" >> /tmp/lumi-user-systemctl.log
  exit 0
fi
state=/tmp/lumi-systemctl-state
log=/tmp/lumi-systemctl.log
touch "$state" "$log"
action="${1:-}"
shift || true
service=
for arg do
  case "$arg" in --*) ;; *) service="$arg"; break ;; esac
done
printf '%s %s\n' "$action" "$service" >> "$log"
has() { grep -Fqx -- "$1 $2" "$state"; }
put() {
  grep -Fvx -- "$1 $2" "$state" > "$state.tmp" || true
  cat "$state.tmp" > "$state"
  printf '%s %s\n' "$1" "$2" >> "$state"
  rm -f "$state.tmp"
}
drop() {
  grep -Fvx -- "$1 $2" "$state" > "$state.tmp" || true
  cat "$state.tmp" > "$state"
  rm -f "$state.tmp"
}
case "$action" in
  is-active) has active "$service" ;;
  is-enabled) has enabled "$service" ;;
  enable)
    for arg do case "$arg" in --now) ;; *) service="$arg"; put enabled "$service" ;; esac; done
    if printf '%s\n' "$*" | grep -Fq -- '--now'; then put active "$service"; fi
    ;;
  start|restart)
    if [ "$action" = start ] && [ "$service" = lumistripe-web.service ] && [ -e /tmp/lumi-block-next-web-start ]; then
      rm -f /tmp/lumi-block-next-web-start
      touch /tmp/lumi-web-start-blocked
      printf '%s\n' "$PPID" > /tmp/lumi-web-start-parent-pid
      while [ ! -e /tmp/lumi-release-web-start ]; do
        python3 -c 'import time; time.sleep(0.02)'
      done
      rm -f /tmp/lumi-release-web-start
      rm -f /tmp/lumi-web-start-blocked
    fi
    if [ "$action" = start ] && [ "$service" = lumistripe-web.service ] && [ -e /tmp/lumi-fail-next-web-start ]; then
      rm -f /tmp/lumi-fail-next-web-start
      exit 1
    fi
    if [ "$action" = restart ] && [ "$service" = lumistripe-web.service ] && [ -e /tmp/lumi-block-next-web-restart ]; then
      rm -f /tmp/lumi-block-next-web-restart
      touch /tmp/lumi-web-restart-blocked
      printf '%s\n' "$PPID" > /tmp/lumi-web-restart-parent-pid
      while [ ! -e /tmp/lumi-release-web-restart ]; do
        python3 -c 'import time; time.sleep(0.02)'
      done
      rm -f /tmp/lumi-release-web-restart
      rm -f /tmp/lumi-web-restart-blocked
    fi
    if [ "$action" = restart ] && [ "$service" = lumistripe-web.service ] && [ -e /tmp/lumi-fail-next-web-restart ]; then
      rm -f /tmp/lumi-fail-next-web-restart
      exit 1
    fi
    put active "$service"
    case "$service" in
      user@*.service)
        uid="${service#user@}"
        uid="${uid%.service}"
        mkdir -p "/run/user/$uid"
        python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.close()' "/run/user/$uid/bus"
        chown "$uid:$uid" "/run/user/$uid" "/run/user/$uid/bus"
        ;;
    esac
    ;;
  stop) drop active "$service" ;;
  disable)
    for arg do case "$arg" in --now) ;; *) service="$arg"; drop enabled "$service" ;; esac; done
    if printf '%s\n' "$*" | grep -Fq -- '--now'; then drop active "$service"; fi
    ;;
  reset-failed|daemon-reload|reload|show) exit 0 ;;
  *) printf 'unexpected systemctl command: %s\n' "$action $*" >&2; exit 2 ;;
esac
EOF
chmod 755 /usr/bin/systemctl
: > /tmp/lumi-user-systemctl.log
chmod 666 /tmp/lumi-user-systemctl.log

mv /usr/bin/mv /usr/bin/mv.real
cat > /usr/bin/mv <<'EOF'
#!/bin/sh
if [ -e /tmp/lumi-fail-next-release-activation ]; then
  destination=
  for argument do destination="$argument"; done
  case "$destination" in
    */target/rust-deploy/current)
      rm -f /tmp/lumi-fail-next-release-activation
      printf 'injected release activation failure\n' >&2
      exit 73
      ;;
  esac
fi
exec /usr/bin/mv.real "$@"
EOF
chmod 755 /usr/bin/mv
