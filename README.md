# LumiStripe

[![CI](https://github.com/woobble/LumiStripe/actions/workflows/ci.yml/badge.svg)](https://github.com/woobble/LumiStripe/actions/workflows/ci.yml)

**Native Rust LED controller with a React dashboard and browser simulator.**

LumiStripe runs its web application, playback engine, CLI, and deployment
helpers from the Rust workspace. The dashboard is a React/TypeScript app whose
REST types are generated from the Rust OpenAPI contract.

See the [documentation index](docs/README.md) for setup, configuration,
architecture, protocols, and contribution guides.

## Features

- Raspberry Pi SPI and GPIO LED output
- Static, Cycling, and music-driven Dynamic playback
- Rust audio capture, DSP, and calibration
- Bluetooth and Spotify Connect audio routing through PipeWire
- A dashboard and simulator backed by the same Rust engine
- A CLI that controls the engine over versioned local IPC

## Raspberry Pi installation and maintenance

Prepare and review a checkout in an administrator-controlled directory. The
installer copies privileged deployment files from this checkout, so it must
not be writable by the service account or by the account invoking `sudo`.

```bash
git clone https://github.com/woobble/LumiStripe.git
cd LumiStripe
# Review the checkout before installing it.
sudo install -d -o root -g root -m 755 /opt/lumistripe-install
sudo cp -a "$PWD"/. /opt/lumistripe-install/
sudo chown -R root:root /opt/lumistripe-install
sudo chmod -R go-w /opt/lumistripe-install
cd /opt/lumistripe-install
sudo ./deploy/lumistripe-admin install --user pi
```

New installations use the native librespot Spotify Connect receiver and do not
ask for a Soloist API key. Pair LumiStripe from Spotify on the same local
network; Spotify Premium is generally required for Connect playback. The
installer prompts for the dashboard pairing code and installs the
administrative command at
`/usr/local/sbin/lumistripe-admin`, then configures the Rust services, SPI,
Bluetooth, PipeWire, Nginx, and HTTPS.

Existing installations keep their selected provider. Legacy installations
without `LUMI_SPOTIFY_BACKEND` remain on Soloist, and ordinary updates never
switch providers. Check or change the provider explicitly:

```bash
sudo lumistripe-admin spotify status
sudo lumistripe-admin spotify use librespot
sudo lumistripe-admin spotify use soloist
sudo lumistripe-admin spotify disable
```

Switching providers does not migrate or delete credentials. Librespot uses
local Spotify Connect pairing and keeps its device identity and credential
cache. Soloist requires its existing protected API key. See the
[Spotify provider guide](docs/spotify.md) for migration details, limitations,
and the separate manual acceptance checklist.

Use the same command for routine maintenance:

```bash
sudo lumistripe-admin update
sudo lumistripe-admin status
sudo lumistripe-admin update --mode source
sudo lumistripe-admin update --refresh-soloist
```

Repair from a fresh, reviewed trusted checkout. The source directory becomes
the configured runtime project directory after repair:

```bash
sudo lumistripe-admin repair --source /opt/lumistripe-repair --user pi
```

To remove LumiStripe while preserving shared OS packages, toolchains, the
service account, and the project checkout:

```bash
sudo lumistripe-admin uninstall
```

Existing installations need one trusted installer upgrade to install the new
command and record their project directory. See the
[deployment guide](deploy/README.md) for that migration, advanced update
options, recovery, and hardware setup. The separate `lumistripe-cli` remains
the playback and diagnostics command.

After installation, open `https://led.controller/` from a trusted network and
pair from **Setup → Audio**. Install the generated mkcert CA on each client.

## Rust CLI

The installed `lumistripe-cli` command controls the matching engine release.
For example:

```bash
lumistripe-cli list-animations
lumistripe-cli run --backend mock --pixels 80 --mode cycling
lumistripe-cli run --backend mock --mode dynamic --audio-source demo
```

See the [CLI guide](apps/lumistripe-cli/README.md) for playback,
calibration, audio diagnostics, and hardware options.

## Browser simulator

The simulator runs the Rust web application with mock LED outputs and serves
the same dashboard as production. It works without Raspberry Pi hardware:

```bash
cd apps/lumistripe-web/frontend
bun install --frozen-lockfile
bun run build
cd ../../..
cargo build --locked -p lumistripe-web
cargo run --locked -p lumistripe-web -- \
  --simulate --pixels 80 --pixels-2 24 \
  --static-dir apps/lumistripe-web/static
```

Open the local URL printed by the command. The
[simulator guide](docs/simulator.md) covers settings isolation, demo audio,
and microphone input.

## Development

Requirements are Rust 1.99.1 and the Bun version pinned in `.bun-version`.
Clone without submodules:

```bash
git clone https://github.com/woobble/LumiStripe.git
cd LumiStripe
```

Run the Rust checks:

```bash
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets
cargo test --locked --workspace
cargo build --locked --workspace
```

Run frontend checks from `apps/lumistripe-web/frontend/`:

```bash
bun install --frozen-lockfile
bun run generate:api
git diff --exit-code -- openapi.json src/generated/api.ts
bun run lint
bun run test
bun run build
```

The frontend build writes ignored static assets to
`apps/lumistripe-web/static`. The Python-based deployment regression harness
is development-only; run it with Python and uv:

```bash
uv run --no-project --with pytest python -m pytest -q deploy/tests
```

The Python web backend and package distributions are retired. A saved
`LUMI_WEB_BACKEND=python` setting is rejected with a migration instruction;
the installer and updater leave that file and application data unchanged.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow.
