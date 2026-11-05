# LumiStripe

[![CI](https://github.com/woobble/LumiStripe/actions/workflows/ci.yml/badge.svg)](https://github.com/woobble/LumiStripe/actions/workflows/ci.yml)

**Native Rust LED controller with a React dashboard and browser simulator.**

LumiStripe runs its web application, playback engine, CLI, and deployment
helpers from the Rust workspace. The dashboard is a React/TypeScript app whose
REST types are generated from the Rust OpenAPI contract.

## Features

- Raspberry Pi SPI and GPIO LED output
- Static, Cycling, and music-driven Dynamic playback
- Rust audio capture, DSP, and calibration
- Bluetooth and Spotify Connect audio routing through PipeWire
- A dashboard and simulator backed by the same Rust engine
- A CLI that controls the engine over versioned local IPC

## Raspberry Pi installation

For Raspberry Pi OS Trixie, install from a reviewed checkout:

```bash
sudo ./deploy/install.sh
```

The installer builds the frontend and commit-matched Rust web, engine, CLI,
and deployment-helper binaries. It configures the systemd services, SPI,
Bluetooth, PipeWire, Nginx, and HTTPS. Set `LUMI_SERVICE_USER` and
`LUMI_PROJECT_DIR` when the service account or checkout path differs. The
installer prompts for a four-digit pairing code and an optional Spotify API
key, or accepts them from the provisioning environment.

Open `https://led.controller/` from a trusted network and pair from **Setup →
Audio**. Install the generated mkcert CA on each client. See the
[deployment guide](deploy/README.md) for trusted checkout setup, network/audio
configuration, updates, recovery, and uninstallation.

To update an installed checkout:

```bash
sudo ./deploy/update.sh origin/main
```

The updater builds and activates a complete release for one Git commit. If the
web service fails its health check, it attempts to restore the previous
release. To remove the appliance while preserving the checkout, shared system
packages, and service account:

```bash
sudo ./deploy/uninstall.sh
```

## Rust CLI

The installed `lumistripe-cli` command controls the matching engine release.
For example:

```bash
lumistripe-cli list-animations
lumistripe-cli run --backend mock --pixels 80 --mode cycling
lumistripe-cli run --backend mock --mode dynamic --audio-source demo
```

See the [CLI guide](rust/apps/lumistripe-cli/README.md) for playback,
calibration, audio diagnostics, and hardware options.

## Browser simulator

The simulator runs the Rust web application with mock LED outputs and serves
the same dashboard as production. It works without Raspberry Pi hardware:

```bash
cd apps/lumistripe-web/frontend
bun install --frozen-lockfile
bun run build
cd ../../..
cargo build --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-engine -p lumistripe-web
cargo run --locked --manifest-path rust/Cargo.toml -p lumistripe-web -- \
  --simulate --pixels 80 --pixels-2 24 \
  --engine-binary rust/target/debug/lumistripe-engine \
  --static-dir apps/lumistripe-web/static
```

Open the local URL printed by the command. The
[simulator guide](docs/simulator.md) covers settings isolation, demo audio,
and microphone input.

## Development

Requirements are Rust 1.88 and the Bun version pinned in `.bun-version`.
Clone without submodules:

```bash
git clone https://github.com/woobble/LumiStripe.git
cd LumiStripe
```

Run the Rust checks:

```bash
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo clippy --locked --manifest-path rust/Cargo.toml --workspace --all-targets
cargo test --locked --manifest-path rust/Cargo.toml --workspace
cargo build --locked --manifest-path rust/Cargo.toml --workspace
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

Python applications, Python package distributions, and native C extensions
are retired. A saved `LUMI_WEB_BACKEND=python` setting is rejected with a
migration instruction; the installer and updater leave that file and
application data unchanged. See the
[Python retirement inventory](docs/migrations/python-retirement-inventory.md)
for the removed paths, retained fixtures, and validation boundaries.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow.
