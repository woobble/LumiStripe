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
a phone on the same trusted network.

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

Color calibration profiles are saved per output in
`~/.config/lumistripe/settings.json`. Override the location for a service or
read-only home directory with `--settings-file /path/to/settings.json`. The
mobile Calibration page pauses normal playback, isolates the selected output,
and restores the previous lighting state after Save or Cancel.

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

Dynamic mode uses demo audio in simulation and microphone audio with hardware.
Override that policy with `--audio-source off|demo|mic` and optionally select a
microphone with `--audio-device PATTERN`.

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
HttpOnly session cookie. Five failed attempts from one client trigger a
temporary one-minute lockout. Without this flag, the dashboard remains
unprotected and must only be exposed on a trusted local network.
