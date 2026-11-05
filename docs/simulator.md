# Browser simulator

The supported LumiStripe simulator is the Rust web application running with
mock LED outputs. It serves the same React dashboard and uses the same Rust
engine as the production web application. There is no standalone simulator.

From the repository root, install and build the frontend, then build the Rust
web server and engine:

```bash
cd apps/lumistripe-web/frontend
bun install --frozen-lockfile
bun run build
cd ../../../rust
cargo build --locked -p lumistripe-engine -p lumistripe-web
cd ..
```

Launch one or two simulated outputs:

```bash
cargo run --locked --manifest-path rust/Cargo.toml -p lumistripe-web -- \
  --simulate --pixels 80 --pixels-2 24 \
  --engine-binary rust/target/debug/lumistripe-engine \
  --static-dir apps/lumistripe-web/src/lumistripe_web/static
```

Open the URL printed by the command. The simulator binds to `127.0.0.1` and
stores settings separately at
`${XDG_CONFIG_HOME:-$HOME/.config}/lumistripe/simulator-settings.json`. Choose
Static, Cycling, or Dynamic mode and select demo or microphone input in the
dashboard. The `--settings-file` option can select a temporary settings file
for isolated runs.

Simulator mode rejects hardware output selections and keeps saved topology on
mock outputs. Microphone capture and calibration require a host with a working
microphone; use demo audio for hardware-free runs.
