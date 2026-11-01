# Incremental Rust engine migration

## Goal and current boundary

LumiStripe currently runs animation playback, audio analysis, and the
application runtime in Python, with selected DSP and device paths implemented
as optional C extensions. This change adds the first Rust process boundary and
a simulated output only. It does not move animation definitions, playback
policy, audio capture or DSP, GPIO/SPI drivers, FastAPI routes, or React code.

The Python worker remains the single writer for playback, audio, topology, and
output state. With the default backend, output is unchanged. With the opt-in
Rust backend, Python still selects and renders animations, updates audio and
playback state, applies output correction and power policy, and sends each
complete RGB frame to a Rust process. Rust validates the frame and holds it in
an in-memory simulated LED output. The dashboard preview and all API responses
continue to come from the existing Python runtime.

The simulated Rust backend is deliberately unavailable with physical hardware
enabled. GPIO and SPI remain owned by the current Python output thread; no Rust
hardware path is exposed by this scaffold.

## Architecture audit

| Area | Current owner and boundary | Migration implication |
| --- | --- | --- |
| Runtime and lifecycle | `LumiStripeRuntime` is the public façade. `RuntimeWorker` owns mutable playback, audio, topology, and output state and serializes commands. The worker has bounded command queues, watchdogs, and orderly cleanup. | Keep the worker and public runtime API in Python while process lifecycle and frame transport are evaluated. |
| Animations | `lumistripe.animation.catalog` registers `AnimationDefinition` values; `AnimationPlayer` runs Python animation implementations over the core pixel buffers. | Port the catalog contract and deterministic frame semantics before moving individual animations. No animation is ported here. |
| Playback | `lumistripe.playback` contains configuration, state, rendering, selection, transitions, musical timing, and sequence policy. `PlaybackEngine` composes these with the animation player. | Treat decisions and state transitions as separate parity targets from pixel rendering. Playback remains Python. |
| Audio | `lumistripe.audio.capture` keeps capture work bounded; worker-side DSP uses typed `AudioFrame`, `MusicFeatures`, and statistics. Optional native `_audio` code accelerates processing. | Keep capture callbacks small and preserve named typed values across any future language boundary. Audio and DSP remain Python/C. |
| Output | Core output controllers implement the `Controller` contract. The web runtime applies output gates, color correction, zone mapping, brightness, and power limits. `LedOutputThread` owns physical output devices and receives complete frames through a bounded latest-wins exchange. | The first Rust controller implements the existing controller contract only for simulation. Physical device ownership and output threading stay in Python. |
| API and dashboard | FastAPI routes use feature-oriented Pydantic contracts. WebSocket preview has its own versioned binary protocol. REST TypeScript types are generated from OpenAPI and the frontend validates network data with Zod. | Rust selection is a CLI/runtime setting; it adds no REST fields, endpoints, WebSocket messages, generated OpenAPI changes, or dashboard controls. |

## Workspace and process boundary

The independent Cargo workspace is under [`rust/`](../../rust/Cargo.toml):

| Crate | Responsibility |
| --- | --- |
| `lumistripe-engine-config` | Pixel-count configuration and validation. |
| `lumistripe-engine-protocol` | Typed versioned request/response envelopes and RGB values. |
| `lumistripe-engine-output` | Output trait and in-memory `SimulatedLedOutput`. |
| `lumistripe-engine-runtime` | Unix socket server, request dispatch, and socket cleanup. |
| `lumistripe-engine` | Standalone `lumistripe-engine` process entry point. |

The Python adapter in `lumistripe_web.runtime.rust_engine` starts one process
per configured simulated output. It configures a private Unix socket, checks
readiness, sends complete RGB frames, and requests graceful shutdown. Protocol
details and shared compatibility fixtures are in
[`docs/protocols/rust-engine-ipc-v1.md`](../protocols/rust-engine-ipc-v1.md).

## Selecting the backend

The existing Python engine remains the default. The Rust process must be built
and selected explicitly:

```bash
cargo build --release --locked --manifest-path rust/Cargo.toml --bin lumistripe-engine
LUMISTRIPE_ENGINE_BINARY="$PWD/rust/target/release/lumistripe-engine" \
  uv run lumistripe-web --engine-backend rust
```

Alternatively, pass `--engine-binary PATH`. Programmatic callers can set
`RuntimeSettings(engine_backend="rust", engine_binary=Path(...))`. Selecting
Rust with `--hardware` is rejected because this version supports simulated
output only. Leaving `--engine-backend` unset preserves the Python backend.

## Feature parity matrix

| Capability | Python backend | Rust scaffold | Parity state |
| --- | --- | --- | --- |
| FastAPI routes, REST models, dashboard, WebSocket preview | Supported | Not implemented in Rust; existing Python path remains active | Preserved outside Rust |
| Animation catalog and animation frames | Supported | Receives completed RGB frames only | Python only |
| Playback modes, transitions, musical clock, sequences | Supported | Not implemented | Python only |
| Audio capture, analysis, DSP, calibration | Supported | Not implemented | Python/C only |
| Frame correction, mapping, brightness, and power policy | Supported | Receives post-policy RGB frames | Python only |
| Simulated LED output | In-memory controller | In-memory Rust output with health/configure/render IPC | Initial parity seam |
| GPIO/SPI output and device lifecycle | Supported through the Python output thread | Not implemented and rejected for Rust backend | Python only |
| Engine configuration validation | Python runtime settings and topology validation | Pixel count 1–16,384 plus frame-length checks | Partial |
| Process health and orderly shutdown | Python worker lifecycle/watchdog | IPC health, owned child process, shutdown command | Initial lifecycle scaffold |

## Next implementation phases

1. **Frame and color parity:** define shared fixtures for buffer layout,
   channel order, alpha handling, brightness, and power-limited output; capture
   the same Python frames through a Rust sink before moving render work.
2. **Pure core primitives:** select deterministic, UI-independent primitives
   with narrow inputs and outputs. Add Python/Rust golden fixtures and benchmark
   each candidate before replacing the Python implementation.
3. **Playback policy:** port state transitions and selection rules only after
   the catalog and time/beat inputs have a stable typed contract. Compare
   `PlaybackDecision` and `RenderPlan` values across both implementations.
4. **Audio boundary:** decide which DSP stages benefit from Rust, preserve
   bounded capture callbacks, and validate timestamping, calibration, and
   telemetry parity before changing the current Python/C path.
5. **Physical output:** consider GPIO/SPI only after simulation parity and
   trusted Raspberry Pi testing cover device ownership, cleanup, timing, and
   failure recovery. Hardware remains Python-owned until that phase is
   separately implemented and reviewed.

## Validation and measurement

```bash
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo test --locked --manifest-path rust/Cargo.toml --workspace
cargo build --locked --manifest-path rust/Cargo.toml --workspace
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python -m pytest -q --no-cov \
  apps/lumistripe-web/tests/test_rust_engine_adapter.py
```

The Python integration test expects the debug binary at
`rust/target/debug/lumistripe-engine` or the path in
`LUMISTRIPE_ENGINE_BINARY`. CI builds it before running that test. A simple IPC
latency/throughput probe is available in
[`scripts/benchmark_rust_engine.py`](../../scripts/benchmark_rust_engine.py);
it reports frame throughput and median/p95 request latency without imposing a
machine-specific performance threshold.

The Rust workspace is tested and built on x86_64 and hosted Linux ARM64 in CI.
The ARM64 job is separate from the self-hosted Raspberry Pi job and does not
send pull-request code to persistent device hardware.
