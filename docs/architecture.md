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
crates/                    Shared engine, audio, output, and Spotify libraries
tests/fixtures/             Shared protocol and regression fixtures
```

The web process owns HTTP and WebSocket handling, pairing, settings,
application coordination, and one in-process `EngineRuntime`. The runtime
exposes a cloneable typed `EngineHandle` with a bounded command queue. Web
handlers enqueue typed commands and read health and audio snapshots; they do
not drive hardware directly.

Rendering, scheduling, audio capture, DSP, and configured output devices remain
on dedicated native threads. Tokio handles application and network work only.
The runtime owns these workers and shuts them down in order, including output
blackout. A terminal worker failure marks the runtime unhealthy, rejects new
commands, attempts cleanup, and exits the web process so systemd can restart
the complete hardware owner. A native crash affects the web application too;
the engine is no longer isolated in a child process.

## Rendering and output

The engine output worker owns the configured device set and performs rendering,
compositing, power finalization, and physical writes. Its frame queue is
bounded and latest-wins: a fresh pending frame can replace stale work instead
of building an unbounded backlog behind slow output. Audio capture callbacks
write fixed batches to a bounded queue; a separate audio worker runs DSP and
passes features to playback. SPI and GPIO integrations are optional. Mock
outputs support the browser simulator and software tests without physical
devices.

The standalone `lumistripe-engine` executable and versioned Unix-socket IPC
adapter remain available for the Rust CLI and other local tools. The adapter
translates the existing protocol into the same `EngineRuntime` operations used
by the web application; it does not contain a separate renderer or audio
implementation. The deployment helper exposes narrow operations for
NetworkManager, update state, and related privileged tasks; it does not own
playback or settings policy.

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

The installer and updater stage the web server, standalone engine, CLI, and
deployment helper together under `target/rust-deploy/releases/<commit>/`. The
web server embeds its runtime; the standalone engine executable remains in the
release for CLI and IPC compatibility. A release is activated only after its
binaries are validated and share the same source commit. The active release
pointer and previous release support rollback.
Installations using the former `rust/target/rust-deploy` layout are migrated
by copying and validating release state before the new updater uses it. Settings
and application data remain outside the release tree.

Deployment orchestration is Bash. The Rust deployment helper owns release
manifest and checksum validation, bounded archive extraction, GitHub asset
digest and Sigstore verification, protected caching, and atomic staging. On a
first binary install, `deploy/release_artifact.sh` bootstraps that helper from
the immutable commit release after checking the helper asset digest, pinned CI
workflow and source-commit attestation, hosted-runner provenance, and AArch64
ELF type. It installs the verified helper under a root-owned snapshot before
the helper performs release operations. The normal install and update path does
not require Python; an explicit compatibility flag remains for recovery from
older installations until the published bootstrap path has appliance evidence.

## Audio and hardware

The deployment layer persists `LUMI_SPOTIFY_BACKEND` in the protected web
environment file. Fresh installs select the embedded librespot receiver;
existing Soloist, librespot, and disabled selections remain in effect. Legacy
hardware installations with no backend setting retain the Rust runtime's
Soloist compatibility fallback. The administrative Spotify command changes
providers explicitly and retains each provider's credential store.

The audio crate owns device capture, the bounded decoded-PCM ingress, DSP, and
telemetry. The optional native Spotify Connect provider taps decoded PCM while
continuing playback to the dedicated Spotify PipeWire sink. The audio worker
uses the same DSP and renderer for direct PCM and capture sources; a stalled
native stream can fall back to sink-monitor capture. Soloist continues to use
monitor capture. The web layer selects sources and profiles, persists
route-specific synchronization offsets, and sends audio commands to the
engine. See the [audio guide](audio.md) and
[Spotify provider guide](spotify.md).

Production hardware use is documented in the [deployment guide](../deploy/README.md).
The [browser simulator](simulator.md) uses mock outputs and does not verify
physical SPI, GPIO, Bluetooth, microphone, or LED timing.
