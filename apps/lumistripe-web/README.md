# LumiStripe Web

FastAPI backend and mobile-first React dashboard for LumiStripe. The backend
starts in a safe in-memory simulation unless hardware output is explicitly
enabled.

## Development

Run the API from the repository root:

```bash
uv run lumistripe-web
```

Run the Vite development server separately:

```bash
bun --cwd apps/lumistripe-web/frontend run dev
```

Open `http://localhost:5173`, or use the development machine's LAN address from
a phone on the same trusted network. WebGPU is available on `localhost`; for a
LAN address, browsers require the dashboard page to be served over HTTPS, so
plain HTTP will intentionally use the Canvas fallback.

## Raspberry Pi 4 hardware

Physical output is opt-in and uses hardware SPI by default. Enable SPI0 with
`sudo raspi-config nonint do_spi 0` (or `dtparam=spi=on` in
`/boot/firmware/config.txt`), then wire the level shifter as follows:

- GPIO10 / physical pin 19 (MOSI) to strip data
- GPIO11 / physical pin 23 (SCLK) to strip clock
- Pi ground, level-shifter ground, strip ground, and power-supply ground together

Run the dashboard with:

```bash
uv run lumistripe-web --hardware --pixels 80 \
  --spi-device /dev/spidev0.0 --spi-speed 1000000 \
  --pairing-code 0427
```

In hardware mode, the Diagnostics → System resources card shows host CPU
temperature and usage, memory and root filesystem usage, and system uptime.
It refreshes every five seconds while the page is open. The authenticated
`GET /api/system/stats` endpoint reads Linux procfs/sysfs metrics; a metric is
returned as `null` when its source is unavailable, and CPU usage is `null`
until two samples are available.

Color calibration profiles are saved per output in
`~/.config/lumistripe/settings.json`. Override the location for a service or
read-only home directory with `--settings-file /path/to/settings.json`. The
mobile Calibration page pauses normal playback, isolates the selected output,
and restores the previous lighting state after Save or Cancel.

The Setup → Stripes page also supports software power limits. Enter the strip
voltage and full-white current per pixel (the defaults are 5 V and 60 mA), then
set an optional LED-only or per-output watt limit. For a shared supply, enable
the shared PSU limit and enter its capacity and the Pi plus buck converter draw
at the PSU input, including conversion losses. The shared supply fields start
at 200 W and a 15 W reserve, with the limit off; the remaining capacity becomes
the LED allowance. LumiStripe estimates each
rendered frame and applies one shared brightness cap when a limit would be
exceeded. Calibrate strip current and the Pi reserve against the actual hardware:
these are estimates, not current measurements.

For a mirrored second strip on a separate controller, enable SPI1 with
`dtoverlay=spi1-1cs`, wire GPIO20 / pin 38 to data and GPIO21 / pin 40 to
clock, then add `--spi-device-2 /dev/spidev1.0`. Both strips must currently
have the same pixel count. The separate devices allow independent controls to
be added later.

The previous userspace GPIO driver remains available for rollback:

```bash
uv run lumistripe-web --hardware --output-backend gpio \
  --chip /dev/gpiochip0 --data-pin 14 --clock-pin 15
```

Dynamic mode uses demo audio in simulation. On hardware, the default `auto`
source prefers a connected Bluetooth phone and falls back to the selected
microphone. Override that policy with `--audio-source off|demo|mic|bluetooth`
and optionally select a microphone with `--audio-device PATTERN`.

Spotify Connect can also run with simulated output and no Bluetooth adapter.
Enable it with `--enable-spotify --audio-source spotify`; a PipeWire/PulseAudio
session must expose the `lumistripe_spotify` sink and monitor. The local Docker
workflow creates that sink and is documented in [`deploy/README.md`](../../deploy/README.md#local-docker-development-with-spotify-connect).

For phone music over Bluetooth, set up BlueZ and PipeWire/WirePlumber using
[`deploy/README.md`](../../deploy/README.md). The Pi remains the Bluetooth
A2DP receiver, sends the incoming stream to its configured speaker output, and
uses the PipeWire monitor source for the Stripe animation. Pair phones from
Setup → Audio in the dashboard, then choose the Pi as the phone's audio output.

Only one process may own the hardware and audio runtime. Do not start the headless
CLI alongside the web backend.

## Production build

```bash
bun --cwd apps/lumistripe-web/frontend run build
uv build --package lumistripe-web
uv run lumistripe-web
```

The frontend build is written into the Python package and served by FastAPI.
Set `--pairing-code` to exactly four digits to protect dashboard APIs and live
WebSocket updates. After entering the code, each browser receives an opaque
HttpOnly session cookie. Five incorrect pairing attempts within 60 seconds
block further attempts with HTTP 429 and a `Retry-After` header. The limit is
shared across clients in one process and is cleared when that process restarts;
it does not protect multiple independent worker processes. A four-digit code
remains weak, so expose the dashboard only on a trusted local network,
including when pairing is enabled.

Sessions expire after 30 days. Live streams close with code `4401` after logout
or expiry, within their next polling interval (at most 250 ms). Authentication
prunes expired sessions during requests, at most once per minute. Storage is
capped at 1,024 sessions; new sessions revoke the oldest session at capacity.
Pairing-disabled operation does not retain sessions.

Malformed saved stripe settings are ignored with a warning while other valid
settings are retained. Settings files with invalid UTF-8 fall back to defaults.
When any settings cannot be read or validated, partial updates are refused to
avoid overwriting the original file with defaults. Back up the file and repair
it or restore a compatible backup before saving dashboard settings again.

To serve the production dashboard over HTTPS directly from Uvicorn, provide a
certificate and private key. This enables WebGPU for supported browsers on the
LAN (the certificate must be trusted by each client device):

```bash
uv run lumistripe-web --ssl-certfile /path/to/cert.pem \
  --ssl-keyfile /path/to/key.pem --pairing-code 0427
```

## USB microphone setup

Select **MIC** and your capture device in **Setup → Audio**, then open Audio
tuning. PCM2902 devices use the same measurement workflow as other USB inputs;
saved tuning stays associated with the device name.

1. Start microphone calibration and keep the room quiet for eight seconds.
2. Play music at your usual listening volume, then select **Measure music**.
3. Review the measured room and music levels, then select **Apply**. Discard or
   cancel leaves the saved profile unchanged. The capture duration is selectable.

MIC processing applies a continuous 30 Hz high-pass filter, a soft input gate
with hysteresis and a 120 ms hold, and bounded software normalization (up to
24× for weak inputs, with fast peak headroom protection). The gate opens at twice the calibrated input noise floor
and closes below 70% of that opening level. Gain returns toward unity when the
gate closes. Bass beat detection includes 40–180 Hz to pick up kick drums beyond
the lowest spectrum band. Voices and sudden room sounds can still trigger it.

The input noise threshold measures RMS **before software gain and gating**, after
high-pass filtering. It is separate from the existing processed-signal cutoff.
Calibration measures distinct, fresh frames; it rejects clipping, insufficient
input, music RMS below `0.003`, and music levels less than three times the room noise. Peaks and clipping
are measured on the original capture samples, before filtering or gain.

Hardware trim stays fixed throughout calibration and is not automatically changed
when applying a recommendation. If clipping appears, discard calibration, lower
hardware trim, save, and repeat both steps. If music is too close to room noise,
move the microphone closer to the speakers or raise playback volume. Unsupported
hardware trim does not prevent software conditioning or calibration.

Existing profiles load with an input noise floor of `0.003`; their other tuning
values are retained. Bluetooth and Spotify retain their existing processing and
single-stage calibration. Raw core processing leaves microphone conditioning off.
The CLI and simulator share the microphone processing defaults; the two-step
wizard is available in the web dashboard.

The calibration API retains its single-stage default. Clients can opt into the
microphone workflow with `two_step: true` on
`POST /api/audio/calibration/session`. After quiet capture the response has
`status: "waiting"` and `phase: "waiting_music"`. Start music capture with
`POST /api/audio/calibration/session/{session_id}/music`, then poll the existing
status endpoint until complete or failed. Finish with `apply: true` only after
review; `apply: false` cancels at any phase. Audio source and tuning changes are
blocked until the session is discarded or applied.
