# Raspberry Pi audio setup

The LumiStripe web runtime can receive music from an iPhone over Bluetooth
while the Pi sends the same stream to the wagon sound system and analyzes it
for reactive animations.

The supported target is Raspberry Pi OS Trixie using PipeWire/WirePlumber.
The Pi should be connected to the sound system over USB, HDMI, or a wired
audio output. Bluetooth speakers are not the target for this first setup.

## One-command installation

From the LumiStripe checkout on the Pi, run:

```bash
sudo env LUMI_PAIRING_CODE=0427 ./deploy/install.sh
```

The installer provisions the complete appliance: Debian/Raspberry Pi OS
packages and native
build tools, the service user and GPIO/SPI/audio permissions, Raspberry Pi SPI,
uv with managed Python 3.12, Bun, locked Python and frontend dependencies, the
frontend production build, the Bluetooth/WirePlumber configuration, a local
mkcert CA and HTTPS certificate, Nginx, and all required systemd services. If
`LUMI_PAIRING_CODE` is omitted, a secure four-digit code is generated and
printed. An existing `/etc/lumistripe/lumistripe-web.env` is preserved.

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
user's mkcert CA path and must be installed/trusted on each iPhone or PC that
opens the HTTPS dashboard.

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

The rule disables graphical-session seat ownership for this dedicated headless
dashboard user, enables the A2DP receiver role, reconnects trusted phones, and
keeps their stream as playback audio. That lets the Pi continue routing music
to its physical sound-system sink while LumiStripe reads the corresponding
PipeWire monitor.

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

After an iPhone is connected, `pactl list short sources` may contain a
`bluez_output.*.monitor` or `bluez_input.*` source. On newer WirePlumber
versions the phone can instead appear in `wpctl status` as an active
`bluez_input.*` stream while only the physical sink's `.monitor` is listed by
`pactl`; LumiStripe detects that layout and uses the physical sink monitor for
animation analysis without changing the speaker output.

## Pair the iPhone

Start the LumiStripe web service, open Setup → Audio, and press **Scan for
phones**. Keep the iPhone’s Bluetooth settings page open, then press **Pair**
beside the phone. BlueZ stores the trusted device, so later party sessions can
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

On the iPhone, forget/remove the Pi from Bluetooth settings. Then scan and pair
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

## Troubleshooting

- No phone appears: open the iPhone Bluetooth settings and run Scan again.
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
