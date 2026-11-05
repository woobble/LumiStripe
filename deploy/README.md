# Raspberry Pi installation and audio setup

The LumiStripe web runtime can receive music from a phone or other source over Bluetooth,
or use Spotify Connect through Spotify's Soloist player. The Pi sends the selected
stream to the sound system and analyzes it for reactive animations.

Supported targets are Raspberry Pi OS Trixie and Ubuntu on ARM64, using
PipeWire/WirePlumber. Prebuilt releases are available on both; source builds
remain available through `LUMI_DEPLOY_MODE=source`.
The Pi can send audio to USB, HDMI, wired, or Bluetooth speaker outputs. It
can also receive music from phones and other Bluetooth audio sources; the
same routed stream drives the music-reactive animation.

## Network prerequisites and commissioning

The installer does not create a Wi-Fi hotspot or DNS records. Before opening
the dashboard, connect the Pi and clients to a trusted network and configure
its DNS to resolve `led.controller` to the Pi's address. Alternatively, provide
an independently configured hotspot with the same DNS record. Keep SSH or a
local console available for initial setup and recovery. The dashboard uses a
four-digit code and limits failed pairing attempts across all clients. Restrict
access to trusted clients: the short code remains unsuitable for an exposed
public service, and an attacker can temporarily block pairing for everyone.
The in-memory limit resets when the web service restarts and is per process.

After installation, install and trust the service user's mkcert CA on each
client, then open `https://led.controller/` and pair. Verify that port 8000 is
not reachable from another host, and confirm the dashboard, LED output and
selected audio source work. Reboot and repeat these checks, including Bluetooth
or Spotify reconnects if used. Before an update, keep console access and a
backup of appliance configuration and settings. Afterward, check the dashboard
and hardware again; if recovery fails, inspect the updater and web service
journals and restore the previous checkout before rebuilding dependencies and
restarting services.

Change the dashboard title and Spotify Connect player name from
**Devices → Startup → Names**. Saving a Spotify name updates the Soloist
service configuration and requests a service restart so Connect advertises it.
On appliances installed before this naming control was added, rerun the
installer from a trusted checkout as described below to install its restricted
restart helper and the Soloist service override path.

## Install from a trusted checkout

The installer copies deployment helpers, systemd units, sudoers rules, and the
Nginx configuration into privileged locations. Run it from a reviewed checkout
under a directory that the service account cannot modify. For example, from
the repository root:

```bash
sudo install -d -o root -g root -m 755 /opt/lumistripe-install
sudo cp -a "$PWD"/. /opt/lumistripe-install/
sudo chown -R root:root /opt/lumistripe-install
sudo chmod -R go-w /opt/lumistripe-install
sudo env LUMI_SERVICE_USER=pi LUMI_PROJECT_DIR=/opt/lumistripe-install \
  /opt/lumistripe-install/deploy/install.sh
```

Replace `pi` with the non-root account that should run LumiStripe. The
installer checks that the invoking and service accounts cannot write the
checkout, its parent directories, or its deployment files. It installs the
root-owned deployment snapshot before making the runtime checkout writable by
the service account. It refuses to import privileged files from a live,
service-user-writable checkout.

In a terminal, the installer asks for a four-digit pairing code and the
Spotify Soloist API key. Leave the pairing-code prompt blank to generate a
random code. Leave the Spotify-key prompt blank to install without Spotify.
For unattended installation, provide `LUMI_PAIRING_CODE` and the optional
Spotify key through the provisioning process environment. Keep their values
out of shell history and command arguments.

The installer defaults to a commit-matched ARM64 binary release for Debian or
Raspberry Pi OS Trixie, or Ubuntu on ARM64. It downloads the GitHub release for
the exact checkout commit, verifies its asset digests and GitHub Actions
provenance, validates the manifest and runtime dependencies, then stages the web server, engine, CLI,
deployment helper, and frontend together. Binary installs do not compile Rust
or build the frontend on the Pi. They do not install Rustup, Cargo, Bun, or
native build headers. Python's standard library is used for bounded archive
validation; no Python application packages, extensions, or uv are installed.

Choose a deployment mode with `LUMI_DEPLOY_MODE`:

| Mode | Behavior |
| --- | --- |
| `binary` (default) | Require a published, verified release for the checked out commit. Missing or unsupported releases stop the operation. |
| `source` | Install Rust 1.88.0 and Bun, build the frontend and Rust applications locally, and activate that complete release. |
| `auto` | Prefer a verified binary, then build locally only when the artifact is missing or the target is unsupported. Integrity or provenance failures stop without a source fallback. |

For example, use source mode during installation with:

```bash
sudo env LUMI_DEPLOY_MODE=source LUMI_SERVICE_USER=pi \
  LUMI_PROJECT_DIR=/opt/lumistripe-install \
  /opt/lumistripe-install/deploy/install.sh
```

The installer also configures operating-system packages, GPIO/SPI/audio
permissions, Raspberry Pi SPI, NetworkManager, Bluetooth/WirePlumber,
Spotify Soloist when an API key is provided, a local mkcert CA and HTTPS
certificate, Nginx, and systemd services. Existing
`/etc/lumistripe/lumistripe-web.env` and
`/etc/lumistripe/lumistripe-spotify.env` files are preserved. Spotify's
official builds are downloaded on the Pi and are not bundled or redistributed
by LumiStripe. Verified archives are cached under
`/var/cache/lumistripe/releases/<commit>`; the cache retains the active and
previous releases for rollback.

The installer uses the non-root account that invoked `sudo` (or the checkout
owner when run from a root shell). To install for another account, set
`LUMI_SERVICE_USER`. `LUMI_PROJECT_DIR` must point to a reviewed,
administrator-controlled checkout as described above; a normal checkout in a
user's home directory is intentionally rejected:

```bash
sudo env LUMI_SERVICE_USER=woobb LUMI_PROJECT_DIR=/opt/lumistripe-install \
  /opt/lumistripe-install/deploy/install.sh
```

`LUMI_PROJECT_DIR` must point to the top-level LumiStripe checkout. The
installer resolves symlinks and validates the checkout before it changes
ownership or installs services.
Because installation runs as root and installs privileged deployment files
from that checkout, use a trusted administrator-controlled copy. If the
service account may have been compromised, do not rerun the installer from its
writable checkout; restore a trusted copy first.

The pairing code is stored in `/etc/lumistripe/lumistripe-web.env` with mode
`600`. The systemd service passes it through the process environment, not its
command-line arguments.

## Rust backend and rollback

The Rust application is the only supported backend. New environment files do
not contain a backend selection. If an existing
`/etc/lumistripe/lumistripe-web.env` sets `LUMI_WEB_BACKEND=python`, the
installer and updater stop with a migration instruction before changing that
file or application data. The web launcher also rejects that setting. The
configuration remains untouched; an administrator must set
`LUMI_WEB_BACKEND=rust` manually, then rerun the installer or updater.
Existing settings are migrated and preserved by the Rust application.

The CI publisher builds the ARM64 release on a hosted ARM64 runner in a Debian
Trixie container after the hosted deployment, frontend, Rust, and ARM64 checks
pass. The release tag is `build-<full source commit>` and contains a
deterministic archive, manifest, SHA-256 file, and signed provenance bundle.
The Pi verifies the release belongs to that commit and the pinned CI workflow
before it stages anything. The frontend is stored inside the release and is
served from the same commit-matched directory as its Rust binaries.

Before enabling release publication, enable GitHub's repository-level release
immutability setting. The publisher checks that each release is marked
immutable, and the installer, updater, and dashboard reject releases that are
not. GitHub locks the release tag and attached assets when an immutable release
is published.

Published GitHub releases are never automatically deleted or replaced. Keep
commit-tagged archives, manifests, checksums, and provenance bundles
indefinitely; local cache cleanup retains the active and previous releases for
rollback.

To build a release bundle locally for inspection, start from a clean checkout
and a machine with Docker Buildx and `uv` installed. Local bundles are not
attested and cannot be installed by binary mode; production releases must be
published by CI.

```bash
COMMIT="$(git rev-parse HEAD)"
OUT_DIR="$(mktemp -d /tmp/lumistripe-release.XXXXXX)"
test -z "$(git status --porcelain)"
docker buildx build \
  --platform linux/arm64 \
  --file deploy/docker/Dockerfile \
  --target release-output \
  --build-arg "BUN_VERSION=$(cat .bun-version)" \
  --build-arg "LUMI_RELEASE_COMMIT=$COMMIT" \
  --output "type=local,dest=$OUT_DIR" \
  .
ARCHIVE="lumistripe-linux-aarch64-$COMMIT.tar.gz"
uv run --no-project --python 3.12 python deploy/release_artifact.py package \
  --release-dir "$OUT_DIR/release" \
  --output "$OUT_DIR/$ARCHIVE" \
  --commit "$COMMIT" \
  --project-root "$PWD" \
  --build-info "$OUT_DIR/release-build-info.json"
cp "$OUT_DIR/release/manifest.json" "$OUT_DIR/$ARCHIVE.manifest.json"
(cd "$OUT_DIR" && sha256sum "$ARCHIVE" > "$ARCHIVE.sha256")
```

This produces the deterministic `.tar.gz` bundle plus its manifest and SHA-256
sidecar. CI adds and verifies the signed provenance bundle before publication.

The web server owns its engine in process and does not need an engine binary
or Unix socket to start; the standalone engine remains deployed for the CLI
and other IPC clients. Systemd restarts the complete web and engine owner if a
native worker fails. The updater switches the checkout and release pointer,
restarts services, and checks dashboard health. If health fails, it restores
the previous checkout and release pointer without rebuilding dependencies.
Both releases remain available locally for rollback.

Run the installed CLI as the service user, for example:

```bash
sudo -H -u pi /home/pi/.local/bin/lumistripe-cli --help
```

The installer selects the available `libgpiod` runtime package and supports
the `libasound2` and `libasound2t64` package names used by Debian-based
systems.

The installer needs internet access for apt, the matching GitHub release and
provenance checks, mkcert, and optional Spotify Soloist downloads. Source mode
also downloads Rustup and Bun. After installation the runtime checkout is
service-user-writable, so it is no longer a trusted source for another root
installer run. For a
repair or reinstall, provide a fresh reviewed checkout under an
administrator-controlled path; existing pairing and complete TLS files are
kept. If either TLS file is missing or empty, the installer recreates the
certificate and key using the existing CA. The generated CA is stored at the
service user's mkcert CA path and must be installed and trusted on each phone
or PC that opens the HTTPS dashboard.

## Connect the internet Wi-Fi adapter

Open Setup → Network in the dashboard to select a Wi-Fi adapter, scan for
nearby open or WPA personal networks, and connect. NetworkManager stores the
profile and reconnects automatically. Adapters currently running as access
points are marked as hotspots and cannot be used for an internet connection,
so the internal LumiStripe hotspot stays available to dashboard clients.

The installer provides NetworkManager, `iw`, and a root-owned helper that
accepts requests only from the paired dashboard service. Passwords are sent to
the helper over standard input and are stored only in NetworkManager's
system-managed connection profile. After updating an existing appliance to a
version that adds this feature, rerun the installer from a trusted checkout as
described above to install the helper permission and adapter tools.

## Systemd updates

The systemd installation checks LumiStripe's public GitHub `main` branch every
six hours. Opening the paired dashboard while online, or reconnecting the phone
to the Pi while the dashboard is open, also starts a check. A failed check
while offline is recorded quietly and retried later. The dashboard shows an
update as ready only after CI has published the matching release and its
manifest identifies the same source commit and Trixie runtime. While CI is
building, after a failed build, or when no release is available, the dashboard
shows that state and withholds the install action. Selecting **Install update**
starts installation only after the artifact is ready. The updater verifies and
stages the release, restarts services, and checks dashboard health. A failed
health check restores the previous checkout and release pointer.

The updater is installed by `deploy/install.sh`. To add it to an existing
systemd installation, prepare a reviewed administrator-controlled checkout as
described above. This preserves existing pairing and Spotify settings.
Existing installations must replace the old updater once because it could
install privileged files from the service-user-writable checkout. During that
migration, prevent the old dashboard trigger and updater from running while the
trusted installer replaces them:

```bash
sudo mv /etc/sudoers.d/lumistripe-update /etc/lumistripe/lumistripe-update.sudoers.disabled
sudo systemctl stop lumistripe-web.service lumistripe-update.service lumistripe-update-check.timer
sudo env LUMI_SERVICE_USER=pi LUMI_PROJECT_DIR=/opt/lumistripe-install \
  /opt/lumistripe-install/deploy/install.sh
```

Before running the last command, copy the reviewed checkout to
`/opt/lumistripe-install` and make it root-owned and non-writable by the
service account, following the trusted-checkout instructions above. Replace
`pi` with the account used by the appliance.

The installer reinstalls the validated sudoers file and starts the services.
If the updater sudoers file is absent on an older installation, omit the first
command. The updater thereafter uses the root-owned
deployment snapshot installed by that command; changes to privileged helper,
service, and Nginx files take effect only when an administrator reruns the
installer from a trusted checkout. If the TLS certificate or key is missing,
the updater stops and the administrator must rerun the installer to repair it.
For a manual update, run this from the checkout root so the installed updater
receives the checkout path:

```bash
sudo env LUMI_PROJECT_DIR="$PWD" \
  /usr/local/libexec/lumistripe/deploy/update.sh
```

For an explicit source update, use `LUMI_DEPLOY_MODE=source` with that command.
Source updates require Bun, Cargo, Rust 1.88.0, and the native development
headers to have been installed already; run the installer in source mode first
on a binary-only appliance. `auto` also accepts a missing or unsupported
artifact as a reason to use a local build, but it does not bypass an integrity,
digest, or provenance failure.

To update offline from a previously verified local bundle, the full commit
must exist in the checkout and its root-protected cache entry must still be
present under `/var/cache/lumistripe/releases/<commit>`. Keep the system on
ARM64 Debian or Raspberry Pi OS Trixie, or ARM64 Ubuntu, with the runtime
packages already installed, then run the normal updater in binary mode with
that commit:

```bash
PROJECT_DIR=/opt/lumistripe-install
SERVICE_USER=pi
COMMIT=FULL_CACHED_COMMIT_SHA
sudo -u "$SERVICE_USER" git -C "$PROJECT_DIR" cat-file -e "$COMMIT^{commit}"
sudo env LUMI_SERVICE_USER="$SERVICE_USER" \
  LUMI_PROJECT_DIR="$PROJECT_DIR" \
  LUMI_OFFLINE_COMMIT="$COMMIT" \
  /usr/local/libexec/lumistripe/deploy/update.sh
```

The offline path skips GitHub fetching, accepts only the verified cache, and
uses the same checkout/release activation, health check, and rollback flow. It
also leaves the existing Spotify Soloist installation unchanged. It fails
closed if the commit, cache entry, or runtime packages are missing.

To reinstall from the same machine's verified cache, prepare a fresh trusted
checkout at the cached commit and run:

```bash
PROJECT_DIR=/opt/lumistripe-install
SERVICE_USER=pi
COMMIT=FULL_CACHED_COMMIT_SHA
sudo env LUMI_SERVICE_USER="$SERVICE_USER" \
  LUMI_PROJECT_DIR="$PROJECT_DIR" \
  LUMI_OFFLINE_COMMIT="$COMMIT" \
  "$PROJECT_DIR/deploy/install.sh"
```

Offline installation requires the checkout and root-protected verified cache
to exist locally, along with all runtime packages and `mkcert`. If Spotify is
configured, the existing Soloist executable must also be present. The
installer checks these prerequisites and skips GitHub and apt access; if any
are missing, use the normal online installation path.

For offline recovery, use the previous commit ID retained under
`target/rust-deploy/releases/`. Replace the example values with the service
account and project directory on the Pi:

```bash
PROJECT_DIR=/home/pi/LumiStripe
SERVICE_USER=pi
PREVIOUS_COMMIT=FULL_PREVIOUS_COMMIT_SHA
sudo systemctl stop lumistripe-web.service
sudo -u "$SERVICE_USER" git -C "$PROJECT_DIR" cat-file -e "$PREVIOUS_COMMIT^{commit}"
sudo -u "$SERVICE_USER" git -C "$PROJECT_DIR" switch --detach "$PREVIOUS_COMMIT"
sudo ln -s "releases/$PREVIOUS_COMMIT" "$PROJECT_DIR/target/rust-deploy/.current.rollback"
sudo mv -Tf "$PROJECT_DIR/target/rust-deploy/.current.rollback" \
  "$PROJECT_DIR/target/rust-deploy/current"
sudo systemctl restart lumistripe-web.service
```

The updater's automatic rollback performs this operation and restarts the web
and configured Spotify services after a failed health check. Keep the previous
commit ID and a trusted checkout available before doing a manual recovery.

Check background updater logs with:

```bash
sudo journalctl -u lumistripe-update-check.service \
  -u lumistripe-update.service --since today
```

## Local Docker development with Spotify Connect

The local development stack builds the Rust web app and engine with simulated
LED output. It uses Spotify Connect as its audio source and plays Spotify through
the host speakers. It does not start Bluetooth, mount GPIO/SPI devices, or
require a microphone. This profile targets Linux hosts with Docker Compose and
a PipeWire PulseAudio-compatible server in the active desktop session.

Create a local Spotify Soloist API key file and export the current user IDs:

```bash
mkdir -p .docker-dev/data/web .docker-dev/data/soloist \
  .docker-dev/data/soloist-cache .docker-dev/data/spotify-home
umask 077
printf 'Spotify Soloist API key: '
read -r -s SPOTIFY_API_KEY
printf '\n'
printf '%s' "$SPOTIFY_API_KEY" > .docker-dev/spotify-api-key
unset SPOTIFY_API_KEY
export LUMI_DEV_UID="$(id -u)"
export LUMI_DEV_GID="$(id -g)"
```

Start the local stack from the repository root:

```bash
docker compose --project-name lumistripe-dev \
  -f deploy/docker-compose.dev.yml --profile spotify up --build
```

The Spotify service is optional and runs in its own container. Omit
`--profile spotify` to start only the web simulator. Open
`http://127.0.0.1:8000/`. In Spotify on a phone or computer, select the
**LumiStripe** Connect device. The web runtime captures the Spotify monitor
while Soloist routes it to the host's default speaker output. LumiStripe
settings and Soloist session/cache data persist under `.docker-dev/data/`;
the local API key and all development state are ignored by Git.

Stop the stack with `Ctrl+C` or:

```bash
docker compose --project-name lumistripe-dev \
  -f deploy/docker-compose.dev.yml down
```

The Rust image builds the web, engine, CLI, and restricted deployment helper
with the pinned Rust toolchain and builds the dashboard with Bun. Its normal
build, runtime, and Soloist archive setup do not use Python, `pip`, or `uv`.
The Compose file uses host networking so the web runtime can connect to
Soloist's local WebSocket on `127.0.0.1:9090`. It binds the dashboard to
`127.0.0.1:8000`, runs both containers as the current user's UID/GID with a
read-only root filesystem and no Linux capabilities, and mounts the active
desktop audio runtime directory read-only. Web settings and Soloist state use
separate bind mounts. If Docker cannot access the host audio socket, confirm that
`XDG_RUNTIME_DIR` points to the logged-in desktop session and that
`$XDG_RUNTIME_DIR/pulse/native` exists. The Spotify container creates a
temporary `lumistripe_spotify` virtual sink and removes it when the stack stops.
Its shutdown allows Soloist to close its audio stream, then unloads the sink
before the container exits. If audio is still distorted after stopping the
stack, check `pactl list short sinks` for a leftover `lumistripe_spotify` sink
and find its module ID with
`pactl list short modules | awk '$2 == "module-null-sink" && $0 ~ /sink_name=lumistripe_spotify/ {print $1}'`.
Unload that ID with `pactl unload-module MODULE_ID`; restarting the user
PipeWire services is a recovery step, not normally required.

## Uninstallation

To remove the LumiStripe appliance files and services while preserving the
checkout, shared Debian packages, PipeWire/Bluetooth/Nginx services, the
service account, Rust and Bun toolchains, mkcert, and
the mkcert CA, run:

```bash
sudo ./deploy/uninstall.sh
```

The script asks for confirmation. Use `--yes` for an unattended removal:

```bash
sudo ./deploy/uninstall.sh --yes
```

The uninstaller removes both LumiStripe systemd units, environment files,
Soloist, LumiStripe's PipeWire/WirePlumber fragments, Nginx site and TLS
files, generated frontend dependencies, and Soloist's default data and
cache directories. It does not remove the project checkout, shared operating
system packages, the service user, or custom Soloist data/cache paths. Existing
non-empty backup directories are retained.

## Manual audio-stack recovery

The following commands are already performed by `deploy/install.sh`. They are
useful only when repairing an installation or configuring a Pi by hand.

Install the packages on the Pi:

```bash
sudo apt update
sudo apt install bluez bluetooth pipewire pipewire-pulse wireplumber \
  libspa-0.2-bluetooth pipewire-alsa libasound2-plugins libportaudio2
sudo usermod -aG audio "$USER"
sudo loginctl enable-linger "$USER"
```

Log out and back in after changing the `audio` group. Enable the user audio
services:

```bash
systemctl --user enable --now pipewire pipewire-pulse wireplumber
sudo systemctl enable --now bluetooth
```

Install the LumiStripe WirePlumber rule in the same user account that runs the
dashboard:

```bash
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
cp deploy/90-lumistripe-bluetooth.conf \
  ~/.config/wireplumber/wireplumber.conf.d/90-lumistripe-bluetooth.conf
systemctl --user restart wireplumber pipewire pipewire-pulse
```

Install the Spotify virtual sink in PipeWire's configuration directory as
well:

```bash
mkdir -p ~/.config/pipewire/pipewire.conf.d
cp deploy/90-lumistripe-spotify.conf \
  ~/.config/pipewire/pipewire.conf.d/90-lumistripe-spotify.conf
systemctl --user restart pipewire pipewire-pulse wireplumber
```

The rule disables graphical-session seat ownership for this dedicated headless
dashboard user, enables both A2DP receiver and transmitter roles, reconnects
trusted devices, and keeps received streams as playback audio. That lets the
Pi route phone music to a wired or Bluetooth speaker while LumiStripe reads the
corresponding PipeWire monitor.

Choose the physical output sink once. `wpctl status` lists the available sink
IDs; select the USB/HDMI/analog output that feeds the sound system:

```bash
wpctl status
wpctl set-default SINK_ID
```

Confirm that the PipeWire Pulse compatibility server is available:

```bash
pactl info
pactl list short sinks
pactl list short sources
```

After a phone is connected, `pactl list short sources` may contain a
`bluez_output.*.monitor` or `bluez_input.*` source. On newer WirePlumber
versions the phone can instead appear in `wpctl status` as an active
`bluez_input.*` stream while only the physical sink's `.monitor` is listed by
`pactl`; LumiStripe detects that layout and uses the physical sink monitor for
animation analysis without changing the speaker output.

## Pair a Bluetooth device

Start the LumiStripe web service, open Setup → Audio, and press **Scan for
devices**. Keep the device’s Bluetooth settings page open, then press **Pair**
beside it. BlueZ stores the trusted device, so later party sessions can
reconnect automatically.

For a service installation, keep the existing `audio` supplementary group in
`lumistripe-web.service`. The service must run as the same user that owns the
PipeWire session. The runtime derives `XDG_RUNTIME_DIR` from that service user
when the environment does not provide it.

If a previous attempt left the phone trusted but not paired, clear that stale
state on both sides before retrying. On the Pi, replace the address below and
run:

```bash
sudo bluetoothctl remove F4:39:A6:8F:81:3D
```

On the phone, forget/remove the Pi from Bluetooth settings. Then scan and pair
again from the dashboard. The pairing operation registers BlueZ's automatic
agent for the complete transaction and enables the controller's pairable mode.

Start or restart the installed service with `sudo systemctl restart
lumistripe-web.service`. Choose the input source from Setup → Audio in the
dashboard. Automatic source selection prefers active Spotify Connect playback,
then a live Bluetooth stream, and finally the selected microphone; it returns
to a higher-priority source when that source becomes active.

## Spotify Connect

The installer runs Spotify Soloist as the separate
`lumistripe-spotify.service`. Soloist writes to the dedicated
`lumistripe_spotify` PipeWire sink. When Spotify is selected as the LumiStripe
audio source, the runtime captures that sink's monitor for analysis and creates
a loopback to the current default output. Changing the output in Setup → Audio
therefore also moves Spotify to a wired, HDMI, USB, or Bluetooth speaker.
On systems where PortAudio exposes only the shared `pulse` or `pipewire`
capture device, LumiStripe temporarily selects the Spotify monitor as that
device's default source and restores the previous source when Spotify is
deselected.

After installation, open Setup → Audio, choose **Spotify Connect**, and use the
playback controls. Pair the device by opening Spotify on a phone or computer
and choosing the configured device name (default: `LumiStripe`). The dashboard
shows the connection state, current track, position, volume, shuffle, and
repeat controls.

Useful service checks:

```bash
sudo systemctl status lumistripe-spotify.service
sudo journalctl -u lumistripe-spotify.service -f
pactl list short sinks | grep lumistripe_spotify
```

Soloist's local WebSocket control endpoint is bound to `127.0.0.1:9090` by
default and is used only by the local LumiStripe web service. Do not expose
that port outside the Pi.

## Troubleshooting

- No phone appears: open the phone's Bluetooth settings and run Scan again.
- Connected but no music: check that the phone is playing audio and that the
  PipeWire source has an A2DP profile.
- Music plays but the Stripe is quiet: inspect `wpctl status` for an active
  `bluez_input.*` stream and `pactl list short sources` for the physical sink's
  `.monitor`; then refresh the Audio page.
- Music does not reach the speakers: check the default sink with `wpctl
  status` and select the physical sound-system output with `wpctl set-default`.
- `bluetoothctl` or PipeWire is unavailable: install the packages above and
  restart the user audio services and LumiStripe.
- The Audio page says no PipeWire/Pulse capture device is visible: install
  `pipewire-alsa` and `libasound2-plugins`, restart LumiStripe, select
  `Automatic` or `Bluetooth`, and press **Save audio source**.
- Spotify is unavailable: check that `/etc/lumistripe/lumistripe-spotify.env`
  contains a valid `LUMI_SPOTIFY_API_KEY`, then restart
  `lumistripe-spotify.service`. If the Soloist build has expired, rerun
  `deploy/update.sh` to download the current official build. The service checks
  the installed build timestamp before startup and reports missing, malformed,
  or expired builds in `journalctl -u lumistripe-spotify.service`.
- Spotify plays but the Stripe is quiet: confirm that the dedicated sink and
  monitor are present with `pactl list short sinks` and
  `pactl list short sources`, then reselect Spotify in Setup → Audio.
