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
sudo env LUMI_PAIRING_CODE=0427 ./deploy/install.sh
```

To enable Spotify Connect during installation, provide the Soloist API key from
the Spotify for Developers dashboard:

```bash
sudo env LUMI_PAIRING_CODE=0427 \
  LUMI_SPOTIFY_API_KEY=YOUR_SOLOIST_API_KEY ./deploy/install.sh
```

The installer provisions the complete appliance: Debian/Raspberry Pi OS
packages and native
build tools, the service user and GPIO/SPI/audio permissions, Raspberry Pi SPI,
uv with managed Python 3.12, Bun, locked Python and frontend dependencies, the
frontend production build, the Bluetooth/WirePlumber configuration, the
official Spotify Soloist binary, a local mkcert CA and HTTPS certificate, Nginx,
and all required systemd services. If
`LUMI_PAIRING_CODE` is omitted, a secure four-digit code is generated and
printed. Existing `/etc/lumistripe/lumistripe-web.env` and
`/etc/lumistripe/lumistripe-spotify.env` files are preserved. Spotify's
official builds are downloaded on the Pi and are not bundled or redistributed
by LumiStripe.

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
pairing and TLS files are kept. The generated CA is stored at the service
user's mkcert CA path and must be installed/trusted on each phone or PC that
opens the HTTPS dashboard.

## Docker Compose installation

Docker is supported as an alternative to the systemd installation. The
Docker path builds the application locally on the Pi, so it produces native
ARM64 Python extensions without requiring a registry. It runs the LumiStripe
web runtime, Spotify Soloist, and Nginx in containers. Bluetooth/BlueZ,
PipeWire/WirePlumber, the user D-Bus session, and the physical GPIO/SPI
devices stay on the host and are exposed through explicit sockets and device
mappings.

From the checkout on the Pi, run:

```bash
sudo env LUMI_PAIRING_CODE=0427 \
  LUMI_SPOTIFY_API_KEY=YOUR_SOLOIST_API_KEY \
  ./deploy/docker-install.sh
```

The installer configures Docker's official Debian apt repository, removes
conflicting distro Docker packages when present, and installs
`docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, and
`docker-compose-plugin`. Docker's repository publishes arm64 packages for the
64-bit Raspberry Pi OS target. It also installs the host Bluetooth/PipeWire
prerequisites, enables SPI, creates the host audio configuration, builds the
ARM64-capable image, and starts the Compose stack. Set
`LUMI_DOCKER_DEBIAN_SUITE` if a Debian derivative does not expose
`VERSION_CODENAME` correctly.
The Spotify key is stored in a mode-600 local secret file readable only by the
service account and root; it is not copied into the image. If the key is
omitted from the environment, an interactive terminal prompts for it.

The Docker installer refuses to take over an existing LumiStripe systemd
installation. To explicitly stop the LumiStripe systemd services and the
LumiStripe-owned host Nginx service during migration, use:

```bash
sudo ./deploy/docker-install.sh --takeover-systemd
```

Use the following commands after updating the checkout:

```bash
sudo ./deploy/docker-update.sh
sudo ./deploy/docker-uninstall.sh
```

The Docker uninstaller removes the Compose services and local image, the
Docker deployment environment, certificates, Spotify key, and Docker-owned
host audio fragments. It preserves Docker and shared host audio/Bluetooth
packages. By default it also removes the Docker-specific LumiStripe state and
Spotify session; use `--keep-data` to retain `/var/lib/lumistripe-docker` for
a later reinstall.

The default Compose mapping expects the LED output at
`/dev/spidev0.0` and the GPIO chip at `/dev/gpiochip0`. Set
`LUMI_SPI_DEVICE` and `LUMI_GPIO_CHIP` when the host paths differ. The
configured device paths are preserved inside the containers. To use a second
SPI controller for a second strip, enable SPI1 on the host and install with:

```bash
sudo env LUMI_PAIRING_CODE=0427 \
  LUMI_SPOTIFY_API_KEY=YOUR_SOLOIST_API_KEY \
  LUMI_SPI_DEVICE=/dev/spidev0.0 \
  LUMI_SPI_DEVICE_2=/dev/spidev1.0 \
  ./deploy/docker-install.sh
```

On Raspberry Pi OS, add `dtoverlay=spi1-1cs` to
`/boot/firmware/config.txt`, reboot, and verify that both
`/dev/spidev0.0` and `/dev/spidev1.0` exist. SPI0 uses GPIO10/data and
GPIO11/clock; SPI1 uses GPIO20/data and GPIO21/clock. Then add the second
output on **Setup → Stripes**, select **SPI**, set its device to
`/dev/spidev1.0`, and choose **Save & apply**. An existing installation can
be changed by adding `LUMI_SPI_DEVICE_2=/dev/spidev1.0` to
`/etc/lumistripe/lumistripe-docker.env` and running
`sudo ./deploy/docker-update.sh`.

Optional `/dev/gpiomem` mapping can be enabled with
`LUMI_GPIOMEM_DEVICE`.

The containers use host networking because Soloist's local WebSocket API and
the existing runtime both use `127.0.0.1:9090`. Only one LumiStripe web
runtime and one host HTTPS proxy may own those ports. For troubleshooting,
inspect the stack with:

```bash
sudo docker compose --project-name lumistripe \
  --env-file /etc/lumistripe/lumistripe-docker.env \
  -f ./deploy/docker-compose.yml ps
sudo docker compose --project-name lumistripe \
  --env-file /etc/lumistripe/lumistripe-docker.env \
  -f ./deploy/docker-compose.yml logs --tail=100
```

The HTTPS certificate is generated with mkcert in the Docker deployment state
directory. Trust the printed service-user CA on each phone or PC that opens
`https://led.controller/`, just as with the systemd installer.

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
