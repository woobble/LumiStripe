# Raspberry Pi audio setup

The LumiStripe web runtime can receive music from a phone or other source over Bluetooth,
or use Spotify Connect through Spotify's Soloist player. The Pi sends the selected
stream to the sound system and analyzes it for reactive animations.

The supported target is Raspberry Pi OS Trixie using PipeWire/WirePlumber.
The Pi can send audio to USB, HDMI, wired, or Bluetooth speaker outputs. It
can also receive music from phones and other Bluetooth audio sources; the
same routed stream drives the music-reactive animation.

## One-command installation

From the LumiStripe checkout on the Pi, run:

```bash
sudo ./deploy/install.sh
```

In a terminal, the installer asks for a four-digit pairing code and the
Spotify Soloist API key. Leave the pairing-code prompt blank to generate a
secure code. Leave the Spotify-key prompt blank to install without Spotify.
For unattended installation, provide the pairing code and optional API key in
the environment:

```bash
sudo env LUMI_PAIRING_CODE=0427 \
  LUMI_SPOTIFY_API_KEY=YOUR_SOLOIST_API_KEY ./deploy/install.sh
```

The installer provisions the complete appliance: Debian/Raspberry Pi OS
packages and native
build tools, the service user and GPIO/SPI/audio permissions, Raspberry Pi SPI,
uv 0.13.0 with managed Python 3.12, the native audio, GPIO, and SPI extensions,
Bun, NetworkManager and Wi-Fi adapter tools, locked Python and frontend
dependencies, the frontend production build,
and the Bluetooth/WirePlumber configuration. It installs the official Spotify
Soloist binary when configured, plus a local mkcert CA and HTTPS certificate,
Nginx, and the required systemd services. Without a Spotify key the Spotify
service remains disabled. Existing
`/etc/lumistripe/lumistripe-web.env` and
`/etc/lumistripe/lumistripe-spotify.env` files are preserved. Spotify's
official builds are downloaded on the Pi and are not bundled or redistributed
by LumiStripe.

The systemd service uses the same Python version selected by the installer and
syncs only production dependencies. Set `LUMI_PYTHON_VERSION` when installing
or updating to choose a different supported interpreter version.

The installer uses the non-root account that invoked `sudo` (or the checkout
owner when run from a root shell). For another account or checkout, set
`LUMI_SERVICE_USER` and/or `LUMI_PROJECT_DIR`:

```bash
sudo env LUMI_SERVICE_USER=pi LUMI_PROJECT_DIR=/home/pi/lumistripe \
  ./deploy/install.sh
```

On Trixie the installer selects `libgpiod3`; older supported Debian-based Pi
images are handled with `libgpiod2` when that is the available runtime package.

The installer needs internet access for apt, uv, Bun, mkcert, and package
downloads. It can be run again safely after updating the checkout; existing
pairing and complete TLS files are kept. If either TLS file is missing or
empty, the installer recreates the certificate and key using the existing CA.
The generated CA is stored at the service
user's mkcert CA path and must be installed/trusted on each phone or PC that
opens the HTTPS dashboard.

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
version that adds this feature, rerun `sudo ./deploy/install.sh` once to
install the helper permission and adapter tools.

## Systemd updates

The systemd installation checks LumiStripe's public GitHub `main` branch every
six hours. Opening the paired dashboard while online, or reconnecting the phone to
the Pi while the dashboard is open, also starts a check. A failed check while
offline is recorded quietly and retried later. When an update is available,
the dashboard shows its commit and an **Install update** button; installation
starts only after you select it. The Pi rebuilds the app, restarts the services,
and checks that the dashboard is healthy. The updater attempts to roll back if
that check fails.

The updater is installed by `deploy/install.sh`. To add it to an existing
systemd installation, first update the checkout to a version containing these
files, then rerun:

```bash
sudo ./deploy/install.sh
```

This is safe to rerun; it preserves the existing pairing and Spotify settings.
For a manual update, use `sudo ./deploy/update.sh`. Check background updater
logs with:

```bash
sudo journalctl -u lumistripe-update-check.service \
  -u lumistripe-update.service --since today
```

## Local Docker development with Spotify Connect

The local development stack builds the web app with simulated LED
output. It uses Spotify Connect as its audio source and plays Spotify through
the host speakers. It does not start Bluetooth, mount GPIO/SPI devices, or
require a microphone. This profile targets Linux hosts with Docker Compose and
a PipeWire PulseAudio-compatible server in the active desktop session.

Create a local Spotify Soloist API key file and export the current user IDs:

```bash
mkdir -p .docker-dev/data
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
  -f deploy/docker-compose.dev.yml up --build
```

Open `http://127.0.0.1:8000/`. In Spotify on a phone or computer, select the
**LumiStripe** Connect device. The web runtime captures the Spotify monitor
while Soloist routes it to the host's default speaker output. LumiStripe
settings and Soloist session/cache data persist under `.docker-dev/data/`;
the local API key and all development state are ignored by Git.

Stop the stack with `Ctrl+C` or:

```bash
docker compose --project-name lumistripe-dev \
  -f deploy/docker-compose.dev.yml down
```

The Compose file uses host networking so the web runtime can connect to
Soloist's local WebSocket on `127.0.0.1:9090`. It binds the dashboard to
`127.0.0.1:8000` and mounts the active desktop audio runtime directory into the
two containers. If Docker cannot access the host audio socket, confirm that
`XDG_RUNTIME_DIR` points to the logged-in desktop session and that
`$XDG_RUNTIME_DIR/pulse/native` exists. The Spotify container creates a
temporary `lumistripe_spotify` virtual sink and removes it when the stack stops.

## Uninstallation

To remove the LumiStripe appliance files and services while preserving the
checkout, shared Debian packages, PipeWire/Bluetooth/Nginx services, the
service account, uv, Bun, mkcert, and the mkcert CA, run:

```bash
sudo ./deploy/uninstall.sh
```

The script asks for confirmation. Use `--yes` for an unattended removal:

```bash
sudo ./deploy/uninstall.sh --yes
```

The uninstaller removes both LumiStripe systemd units, environment files,
Soloist, LumiStripe's PipeWire/WirePlumber fragments, Nginx site and TLS
files, generated Python/frontend dependencies, and Soloist's default data and
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

Start LumiStripe with the default automatic source policy:

```bash
uv run lumistripe-web --hardware --audio-source auto --pairing-code 0427
```

Automatic source selection prefers a live Bluetooth stream, falls back to the
selected microphone, and returns to Bluetooth when the phone reconnects. Use
`--audio-source bluetooth` to require Bluetooth, or choose the source from the
dashboard.

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
  `deploy/update.sh` to download the current official build.
- Spotify plays but the Stripe is quiet: confirm that the dedicated sink and
  monitor are present with `pactl list short sinks` and
  `pactl list short sources`, then reselect Spotify in Setup → Audio.
