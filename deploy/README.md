# Raspberry Pi installation and audio setup

The LumiStripe web runtime can receive music from a phone or other source over Bluetooth,
or use Spotify Connect through the native librespot provider, which is the
default for new installations, or the retained Spotify Soloist migration
provider. The Pi sends the selected stream to the
sound system and analyzes it for reactive animations.

Supported binary targets are 64-bit Raspberry Pi OS Trixie and Ubuntu Server
26.04 LTS (Resolute Raccoon) on ARM64, using PipeWire/WirePlumber. Ubuntu
24.04 LTS is not currently qualified for the Debian Trixie release ABI; use
`lumistripe-admin install --mode source` or `lumistripe-admin update --mode
source` there. CI checks the Debian Trixie userspace used by the release
builder; that does not independently verify Raspberry Pi OS packaging or
hardware behavior, which remain manual acceptance items.
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
**Devices → Startup → Names**. The selected provider advertises the saved name;
librespot refreshes local discovery and Soloist requests its existing service
restart. On appliances installed before this naming control was added, rerun
the trusted `lumistripe-admin install` command as described below to install
the restricted Soloist restart helper when that provider is selected.

## Administrative command quick start

The primary installation and maintenance interface is `lumistripe-admin`.
Use this sequence for a new appliance:

1. **Prepare and review a trusted checkout.** The service account and invoking
   account must not be able to write the checkout, its parent directories, or
   deployment files.

    ```bash
    git clone https://github.com/woobble/LumiStripe.git
    cd LumiStripe
    # Review the checkout before installing it.
    sudo install -d -o root -g root -m 755 /opt/lumistripe-install
    sudo cp -a "$PWD"/. /opt/lumistripe-install/
    sudo chown -R root:root /opt/lumistripe-install
    sudo chmod -R go-w /opt/lumistripe-install
    ```

2. **Install.** Replace `pi` with the LumiStripe service account.

    ```bash
    cd /opt/lumistripe-install
    sudo ./deploy/lumistripe-admin install --user pi
    ```

3. **Update.** Updates use GitHub main and verified precompiled releases by
   default.

    ```bash
    sudo lumistripe-admin update
    ```

4. **Check status.** This reads saved update state and service information
   without changing the installation.

    ```bash
    sudo lumistripe-admin status
    ```

5. **Repair.** Prepare a fresh trusted checkout. Repair makes it the configured
   runtime project directory, so do not use the current service-writable
   checkout as its source.

    ```bash
    sudo lumistripe-admin repair --source /opt/lumistripe-repair --user pi
    ```

6. **Uninstall.** Confirmation is required by default; pass `--yes` to skip it.

    ```bash
    sudo lumistripe-admin uninstall
    ```

The installer installs a root-owned deployment snapshot before making the
runtime checkout writable by the service account. Repeating `sudo
lumistripe-admin install` runs the idempotent installer from that root-owned
snapshot. Do not run initial installation or repair from an unreviewed or
service-user-writable checkout. Ordinary maintenance needs no internal paths or
environment-variable assignments.

The root-owned `/var/lib/lumistripe/install-state` file stores the service user,
canonical project directory, deployment mode, and existing Nginx migration
flag. It remains root-owned with mode `600`, is updated atomically, and is
parsed as data rather than sourced by a shell. On older installations without
`project_dir`, the command safely reads the value from the root-owned rendered
web service unit or accepts an explicit `--project-dir`; a successful install
or update persists the migrated value. No current working directory is used
to discover an installation.

Systems installed before `lumistripe-admin` existed need one trusted installer
upgrade. The installer keeps the existing service state and credentials while
installing the root-owned command and deployment snapshot. Older updater
migrations that still allow privileged reads from the runtime checkout must
disable that old trigger during this upgrade; see
[Existing updater migration](#existing-updater-migration).

Interactive installation asks for a four-digit dashboard pairing code. Leave
it blank to generate a random code. New installations select librespot, so no
Soloist API key is requested and no Soloist executable is downloaded or
started. Pair LumiStripe from Spotify Connect on the same local network;
Spotify Premium is generally required for Connect playback. The initial
installation must use a reviewed checkout in an administrator-controlled
directory. For unattended installation, `LUMI_PAIRING_CODE` remains available;
keep secrets out of shell history and command arguments.

Check and switch the provider with the dedicated administrative command:

```bash
sudo lumistripe-admin spotify status
sudo lumistripe-admin spotify use librespot
sudo lumistripe-admin spotify use soloist
sudo lumistripe-admin spotify disable
```

`spotify status` reads provider configuration and service state without making
changes. A switch checks prerequisites, shows the authentication differences,
asks for confirmation, and restores the prior configuration and service state
if it fails. Normal updates never change providers. Existing explicit provider
settings remain selected; legacy configurations without a provider setting
continue to use Soloist. Switching does not migrate, rewrite, or delete either
provider's credentials. A Soloist switch requires its existing protected API
key and validated executable. See the [Spotify provider guide](../docs/spotify.md)
for pairing, audio routing, limitations, policy notes, and manual acceptance.

### Advanced deployment options

The command defaults to verified precompiled releases. Select a deployment
strategy or use an already verified cached commit with readable flags:

```bash
sudo lumistripe-admin update --mode source
sudo lumistripe-admin update --mode auto
sudo lumistripe-admin update --offline 0123456789abcdef0123456789abcdef01234567
sudo lumistripe-admin update --refresh-soloist
sudo lumistripe-admin install --mode source --user pi
```

`binary` requires the published release for the target commit. `source` builds
locally using the installed Rust and Bun toolchains. `auto` prefers a verified
binary and falls back only when the artifact is missing or the platform is
unsupported; integrity or provenance failures stop the operation. Offline
updates use only a previously verified local release. Soloist is not refreshed
during routine updates unless `--refresh-soloist` is supplied.

The installer configures operating-system packages, GPIO/SPI/audio
permissions, Raspberry Pi SPI, NetworkManager, Bluetooth/WirePlumber, optional
Spotify Soloist, mkcert TLS, Nginx, and systemd. Existing
`/etc/lumistripe/lumistripe-web.env` and
`/etc/lumistripe/lumistripe-spotify.env` files are preserved. Spotify's
official builds are downloaded on the Pi and are not bundled or redistributed
by LumiStripe. Verified archives are cached under
`/var/cache/lumistripe/releases/<commit>`; the active and previous releases
remain available for rollback.

The binary-first path supports ARM64 Ubuntu 26.04 LTS and Debian-family Trixie
targets.
It validates release asset digests, GitHub Actions provenance, the manifest,
and runtime dependencies, then stages the web server, engine, CLI, deployment
helper, and frontend as one commit-matched release. Binary installs do not
compile Rust or build the frontend on the Pi. Source mode installs Rust
1.99.1, Bun, and build requirements as needed.

The normal installer and updater use Bash, `jq`, and the Rust deployment helper;
they do not install or invoke Python. On a first online binary install, before
the Rust helper is already present, the root-owned `release_artifact.sh`
downloads the helper asset from the requested immutable commit release. It
checks the GitHub asset digest and size, verifies the attached Sigstore bundle
against the pinned CI workflow and source commit with GitHub CLI 2.67 or newer,
checks the AArch64 ELF type, then atomically installs a root-owned helper
snapshot. The helper independently verifies the release archive, provenance,
manifest, and cache before staging it. Offline cached installs require an
already trusted Rust helper. `LUMI_RELEASE_ARTIFACT_PYTHON_COMPAT=1` explicitly
selects the retained Python verifier for controlled compatibility recovery; it
is not set by the installer or updater.

`lumistripe-admin` delegates to the existing `deploy/install.sh`,
`deploy/update.sh`, and `deploy/uninstall.sh` implementations. Those scripts,
the release verifier, update lock, transactional activation, health check, and
automatic rollback remain the deployment internals. The dashboard's
fixed, argument-free update trigger remains separate, and the service account
does not receive permission to run the administrative command.

## Advanced maintenance reference

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
deterministic archive, manifest, SHA-256 file, archive provenance bundle, and a
separately attested deployment helper with its Sigstore bundle. The Pi verifies
the helper belongs to that commit and the pinned CI workflow before using it;
the helper then verifies the release archive and provenance before staging.
The frontend is stored inside the release and is served from the same
commit-matched directory as its Rust binaries.

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
and a machine with Docker Buildx and ARM64 container execution support. Local
bundles are not attested and cannot be installed by binary mode; production
releases must be published by CI.

```bash
COMMIT="$(git rev-parse HEAD)"
OUT_DIR="$(mktemp -d /tmp/lumistripe-release.XXXXXX)"
test -z "$(git status --porcelain)"
docker buildx build \
  --platform linux/arm64 \
  --file deploy/docker/Dockerfile \
  --target release-smoke \
  --build-arg "BUN_VERSION=$(cat .bun-version)" \
  --build-arg "LUMI_RELEASE_COMMIT=$COMMIT" \
  --load \
  --tag "lumistripe-release-local:$COMMIT" \
  .
container="$(docker create "lumistripe-release-local:$COMMIT")"
docker cp "$container:/release" "$OUT_DIR/"
docker cp "$container:/release-build-info.json" "$OUT_DIR/release-build-info.json"
docker rm "$container"
ARCHIVE="lumistripe-linux-aarch64-$COMMIT.tar.gz"
docker run --rm --network none --read-only \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$OUT_DIR/release,dst=/release" \
  --mount "type=bind,src=$OUT_DIR,dst=/out" \
  --mount "type=bind,src=$PWD,dst=/src,readonly" \
  --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
  --entrypoint /release/lumistripe-deploy-helper \
  "lumistripe-release-local:$COMMIT" \
  release-artifact package \
    --release-dir /release \
    --output "/out/$ARCHIVE" \
    --commit "$COMMIT" \
    --project-root /src \
    --build-info /out/release-build-info.json
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

## Repeat setup and refresh Spotify Soloist

The updater can be rerun safely; rerun the installer from a fresh,
administrator-controlled checkout when repairing or reinstalling. Both skip
APT when required packages are already present, preserve existing environment
files and service-owned data, and only rewrite changed managed configuration.
The source installer checks its Rust and Bun pins and reuses matching
toolchains. Unchanged application and Spotify files do not trigger service
restarts. Each successful run prints numbered phases and monotonic timings for
package setup, configuration, Soloist, and service health; source builds are
timed separately.

Spotify Soloist is reused during routine setup. To ask the updater to check the
official Spotify archive without advancing the checkout, request its current
full commit with the administrative command:

```bash
sudo lumistripe-admin update --offline FULL_CACHED_COMMIT_SHA --refresh-soloist
```

The updater compares the verified candidate with the installed executable and
replaces it only when the candidate is newer. Replacement is an atomic rename,
so a failed download, archive check, or executable check leaves the existing
binary intact. A valid existing executable is also retained if an explicit
refresh cannot complete. The internal updater still accepts
`LUMI_SOLOIST_REFRESH=1` for legacy maintenance workflows.
Spotify documents the official architecture-specific archives and the build
metadata reported by `soloist --version` in its [downloads and updates
reference](https://developer.spotify.com/documentation/soloist/reference/downloads-and-updates).
The Pi self-hosted native validation workflow is separate from normal CI and
runs only when manually dispatched for `main`. Hosted CI remains responsible
for routine pull request and push validation. Dispatch the **Raspberry Pi
Validation** workflow from the repository Actions page with the branch set to
`main` when trusted hardware validation is needed.

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

### Existing updater migration

The administrative command and its root-owned deployment snapshot are
installed by the trusted installer. Existing installations must run it once to
record the project directory and add `lumistripe-admin`; existing pairing and
Spotify settings are preserved. An older updater may still install privileged
files from the service-user-writable runtime checkout. During migration,
prevent its dashboard trigger and updater from running until the trusted
installer replaces them:

```bash
sudo mv /etc/sudoers.d/lumistripe-update /etc/lumistripe/lumistripe-update.sudoers.disabled
sudo systemctl stop lumistripe-web.service lumistripe-update.service lumistripe-update-check.timer
sudo /opt/lumistripe-install/deploy/lumistripe-admin install --user pi
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
Normal manual updates use the administrative command:

```bash
sudo lumistripe-admin update
```

For an explicit source update, use `--mode source`.
Source updates require Bun, Cargo, Rust 1.99.1, and the native development
headers to have been installed already; run the installer in source mode first
on a binary-only appliance. `auto` also accepts a missing or unsupported
artifact as a reason to use a local build, but it does not bypass an integrity,
digest, or provenance failure.

Direct calls to `deploy/install.sh`, `deploy/update.sh`, and
`/usr/local/libexec/lumistripe/deploy/update.sh` are retained for advanced
recovery and legacy maintenance. They are not needed for ordinary operation.

To update offline from a previously verified local bundle, the full commit
must exist in the checkout and its root-protected cache entry must still be
present under `/var/cache/lumistripe/releases/<commit>`. Keep the system on
ARM64 Debian or Raspberry Pi OS Trixie, or Ubuntu Server 26.04 LTS ARM64, with the runtime
packages already installed, then pass that commit to the admin command:

```bash
COMMIT=FULL_CACHED_COMMIT_SHA
sudo lumistripe-admin update --offline "$COMMIT"
```

The offline path skips GitHub fetching, accepts only the verified cache, and
uses the same checkout/release activation, health check, and rollback flow. It
also leaves the existing Spotify Soloist installation unchanged. It fails
closed if the commit, cache entry, or runtime packages are missing.

To reinstall from the same machine's verified cache, prepare a fresh trusted
checkout at the cached commit and run:

```bash
COMMIT=FULL_CACHED_COMMIT_SHA
sudo /opt/lumistripe-install/deploy/lumistripe-admin install \
  --user pi --offline "$COMMIT"
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
LED output. It defaults to the native librespot receiver and plays Spotify
through the host speakers. It does not start Bluetooth, mount GPIO/SPI devices,
or require a microphone. This profile targets Linux hosts with Docker Compose
and a PipeWire PulseAudio-compatible server in the active desktop session.

Export the current user IDs:

```bash
export LUMI_DEV_UID="$(id -u)"
export LUMI_DEV_GID="$(id -g)"
```

Start the local stack from the repository root:

```bash
docker compose --project-name lumistripe-dev \
  -f deploy/docker-compose.dev.yml up --build
```

The native receiver runs inside the web simulator. Open
`http://127.0.0.1:8000/` and select the **LumiStripe** Connect device in Spotify
on a phone or computer. LumiStripe settings and native credentials persist
under `.docker-dev/data/web/`; development state is ignored by Git.

Set `LUMI_SPOTIFY_BACKEND=librespot` before the same Compose command to select
the native receiver explicitly; this is also the default when the variable is
omitted.

To exercise the legacy Soloist sidecar, create its local API-key secret and
storage directories:

```bash
mkdir -p .docker-dev/data/soloist .docker-dev/data/soloist-cache \
  .docker-dev/data/spotify-home
umask 077
printf 'Spotify Soloist API key: '
read -r -s SPOTIFY_API_KEY
printf '\n'
printf '%s' "$SPOTIFY_API_KEY" > .docker-dev/spotify-api-key
unset SPOTIFY_API_KEY
```

Then select Soloist explicitly and start its profile:

```bash
LUMI_SPOTIFY_BACKEND=soloist docker compose --project-name lumistripe-dev \
  -f deploy/docker-compose.dev.yml --profile spotify up --build
```

Compose passes the provider selection to both services. The Soloist sidecar
refuses to start unless `LUMI_SPOTIFY_BACKEND=soloist`, so it cannot run at the
same time as the embedded native receiver.

Stop the stack with `Ctrl+C` or:

```bash
docker compose --project-name lumistripe-dev \
  -f deploy/docker-compose.dev.yml down
```

The default Rust image builds the web, engine, CLI, and restricted deployment
helper with the pinned Rust toolchain and builds the dashboard with Bun. It
contains the native provider and does not download Soloist. The optional
development-only `runtime-soloist` target downloads the Soloist archive for the
separate legacy sidecar. These build paths do not use Python, `pip`, or `uv`.
The Compose file uses host networking so the web runtime can connect to
Soloist's local WebSocket on `127.0.0.1:9090` when the legacy profile is
selected. It binds the dashboard to
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
sudo lumistripe-admin uninstall
```

Use `--yes` to skip the confirmation:

```bash
sudo lumistripe-admin uninstall --yes
```

The uninstaller removes LumiStripe systemd units, the web environment file,
Soloist's executable, LumiStripe's PipeWire/WirePlumber fragments, Nginx site
and TLS files, and generated frontend dependencies. It preserves the Soloist
API-key file at `/etc/lumistripe/lumistripe-spotify.env`, native librespot state
at `$HOME/.local/share/lumistripe/spotify` or its configured location, and
Soloist data and cache at their default or custom locations. It does not remove
the project checkout, shared operating-system packages, or service account.
There is no credential purge option. Existing non-empty backup directories are
retained.

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

Both providers use the dedicated `lumistripe_spotify` PipeWire sink. When
Spotify is selected as the LumiStripe audio source, the runtime captures that
sink's monitor and routes sound to the selected physical output. Changing the
output in Setup → Audio therefore also moves Spotify to a wired, HDMI, USB, or
Bluetooth speaker. On systems where PortAudio exposes only the shared `pulse`
or `pipewire` capture device, LumiStripe temporarily selects the Spotify
monitor and restores the previous source when Spotify is deselected.

Open Setup → Audio, choose **Spotify Connect**, and select LumiStripe in
Spotify on a phone or computer on the same local network. The dashboard shows
connection state, current track, position, volume, shuffle, and repeat
controls. With the native backend, no separate Spotify service is required.
With Soloist, inspect its service and local WebSocket with:

```bash
sudo systemctl status lumistripe-spotify.service
sudo journalctl -u lumistripe-spotify.service -f
```

For either provider, inspect the audio route with:

```bash
pactl list short sinks | grep lumistripe_spotify
pactl list short sources | grep lumistripe_spotify
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
- Spotify is unavailable with Soloist: check that
  `/etc/lumistripe/lumistripe-spotify.env` contains a valid
  `LUMI_SPOTIFY_API_KEY`, then restart `lumistripe-spotify.service`. If the
  Soloist build has expired, rerun
  `sudo lumistripe-admin update --refresh-soloist` to download the current
  official build. The service checks its build timestamp and reports missing,
  malformed, or expired builds in its journal.
- Spotify is unavailable with librespot: check the web service journal and the
  configured protected data directory. Confirm Spotify and LumiStripe share a
  local network that permits Connect discovery, then select LumiStripe again
  to pair or restore credentials.
- Spotify plays but the Stripe is quiet: confirm that the dedicated sink and
  monitor are present with `pactl list short sinks` and
  `pactl list short sources`, then reselect Spotify in Setup → Audio.
