# Architecture

LumiStripe is a Rust application with a React and TypeScript dashboard. The
single Cargo workspace at the repository root builds the web server, playback
engine, CLI, and deployment helper.

## Workspace layout

```text
apps/lumistripe-web/       Axum server, REST API, WebSockets, React frontend
apps/lumistripe-engine/    Engine process entry point
apps/lumistripe-cli/       CLI entry point
apps/lumistripe-deploy-helper/  Restricted privileged operations
crates/                    Shared engine, protocol, audio, and output libraries
tests/fixtures/             Shared protocol and regression fixtures
```

The web server owns HTTP and WebSocket handling, pairing, settings, and
application coordination. It sends typed commands to the engine over a local
Unix socket. The engine process owns playback scheduling, rendering, audio
capture and DSP, and configured output devices. Web handlers enqueue commands
and read published snapshots; they do not drive hardware directly.

## Rendering and output

The engine output worker owns the configured device set and performs rendering,
compositing, power finalization, and physical writes. Its frame queue is
bounded and latest-wins: a fresh pending frame can replace stale work instead
of building an unbounded backlog behind slow output. Audio capture callbacks
write fixed batches to a bounded queue; a separate audio worker runs DSP and
passes features to playback. SPI and GPIO integrations are optional. Mock
outputs support the browser simulator and software tests without physical
devices.

The Rust CLI controls the same engine over IPC. The deployment helper exposes
narrow operations for NetworkManager, update state, and related privileged
tasks; it does not own playback or settings policy.

## Web and frontend contracts

Rust Axum models are the REST contract source. From the repository root,
`cargo run --locked -p lumistripe-web --bin export-openapi` writes
`apps/lumistripe-web/frontend/openapi.json`. Run `bun run generate:api` in the
frontend directory to regenerate `src/generated/api.ts`. Network responses
are validated at the frontend boundary. The JSON and binary WebSocket payloads
have their own documented contracts.

## Build output and deployment

Builds use the root `target/` directory. The web server serves the frontend
from `apps/lumistripe-web/static` by default; the frontend source and build
configuration live beside the Rust application under
`apps/lumistripe-web/frontend`.

The installer and updater stage the web server, engine, CLI, and deployment
helper together under `target/rust-deploy/releases/<commit>/`. A release is
activated only after its binaries are validated and share the same source
commit. The active release pointer and previous release support rollback.
Installations using the former `rust/target/rust-deploy` layout are migrated
by copying and validating release state before the new updater uses it. Settings
and application data remain outside the release tree.

## Audio and hardware

The audio crate owns capture, configuration, DSP, and telemetry. The web layer
selects sources and profiles, persists route-specific synchronization offsets,
and sends audio commands to the engine. Source details and operator guidance
are in the [audio guide](audio.md).

Production hardware use is documented in the [deployment guide](../deploy/README.md).
The [browser simulator](simulator.md) uses mock outputs and does not verify
physical SPI, GPIO, Bluetooth, microphone, or LED timing.
