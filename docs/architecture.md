# LumiStripe architecture

LumiStripe's production application is native Rust with a React and TypeScript
dashboard. The Rust workspace owns the web server, real-time engine, CLI,
simulator mode, and restricted deployment helper.

## Dependency direction

```text
React / TypeScript frontend
            |
        Rust Axum web
            |
       versioned IPC
            |
      Rust real-time engine
       /      |       \
 audio DSP  renderer  SPI / GPIO
            |
       mock outputs

Rust deployment helper
   update state, NetworkManager, Spotify support
```

The web app owns HTTP/WebSocket handling, authentication, settings, and
application coordination. It sends engine commands over the versioned local
IPC protocol. The engine owns playback state, rendering, audio capture and DSP,
and configured output devices. Mock outputs are used by simulator mode and
software tests.

The deployment helper contains narrowly scoped privileged operations. The
installer and updater build the web server, engine, CLI, and helper from one
commit, store them in a release directory, and atomically activate that release.
The web launcher and supervised engine restarts stay pinned to the same
commit-matched binaries.

## REST and frontend contracts

Rust Axum request and response models are the REST contract source. The
`export-openapi` command generates
`apps/lumistripe-web/frontend/openapi.json`; the frontend generator then
updates `src/generated/api.ts`. Do not hand-maintain a second backend schema.

Frontend network boundaries use strict Zod validation for REST state, audio
telemetry, Bluetooth responses, and preview messages. The preview binary
protocol is versioned and documented in
[the preview protocol](protocols/preview.md), with shared fixtures used by Rust
and frontend tests. The WebSocket client is shared between JSON and binary
streams. Canvas and WebGPU implementations remain behind the renderer
interface.

## Engine ownership

The engine is the single writer for mutable playback, audio, controller, and
hardware state. Web handlers validate requests and enqueue typed commands;
they do not drive hardware directly. Audio callbacks do bounded buffer writes,
while DSP and native device work run on worker-owned paths.

The Rust workspace separates configuration, IPC, output drivers, runtime,
audio, animation definitions/state, rendering, compositing, effects, and the
standalone engine process. GPIO, SPI, Bluetooth, and audio-provider integrations
are optional build features where practical; simulator mode uses mock output
without opening physical LED devices.

## Deployment and stored settings

Production deployment contains no Python web app, Python CLI, Python library
packages, or native C extension build. The installer creates an environment
file without selecting an application language. If an existing file sets
`LUMI_WEB_BACKEND=python`, the installer and updater stop with migration
instructions before changing that file or application data. An administrator
must set the value to `rust` manually. Rust settings migrations preserve
existing settings fields.

The current dashboard is served from the standalone build output at
`apps/lumistripe-web/static`. The source frontend remains under
`apps/lumistripe-web/frontend`; the Rust server's default static path and
deployment launcher use the same output directory.

## Historical migration records

Earlier migration documents record the Python-to-Rust transition and its
intermediate test results. They are historical evidence, not instructions for
running a Python backend. See the
[Python retirement inventory](migrations/python-retirement-inventory.md),
[Rust web migration record](migrations/rust-web.md), and
[Rust engine migration record](migrations/rust-engine.md).
