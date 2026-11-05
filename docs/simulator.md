# Browser simulator

The supported LumiStripe simulator is the Rust web application running with
mock LED outputs. It serves the production React dashboard and uses the same
Rust engine as the appliance.

From the repository root, install and build the frontend, then build the Rust
web server:

```bash
cd apps/lumistripe-web/frontend
bun install --frozen-lockfile
bun run build
cd ../../../
cargo build --locked \
  -p lumistripe-web
```

Launch one or two simulated outputs:

```bash
cargo run --locked -p lumistripe-web -- \
  --simulate --pixels 80 --pixels-2 24 \
  --static-dir apps/lumistripe-web/static
```

Open the URL printed by the command. Simulator settings are stored separately
at `${XDG_CONFIG_HOME:-$HOME/.config}/lumistripe/simulator-settings.json`.
Use `--settings-file` to choose a temporary settings file. Select Static,
Cycling, or Dynamic mode in the dashboard and choose demo or microphone input.
Microphone capture and calibration require a working host audio device; demo
audio is available without a microphone.

Simulator mode rejects hardware output selections and keeps saved topology on
mock outputs. It does not require a Raspberry Pi, GPIO permissions, or SPI
devices.
