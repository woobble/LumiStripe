# Incremental Rust engine migration

## Goal and current boundary

LumiStripe runs animation playback, audio analysis, and the application runtime
in Python, with selected DSP and device paths implemented as optional native
extensions. The opt-in Rust backend now owns every configured output in one
Rust process and includes native Linux SPI and GPIO drivers plus a mock output.
Animation definitions, playback policy, audio capture and DSP, FastAPI routes,
and React code remain in Python.

The Python worker remains the single writer for playback, audio, topology, and
output policy. The default Python backend is unchanged. With the Rust backend,
Python still selects and renders animations, applies color correction and
power limits, and publishes complete RGBA output sets through a bounded
latest-wins exchange. A dedicated bridge sends compact binary frames to one
Rust process. Rust owns all device handles and a second bounded latest-wins
frame slot, then drives every configured output from its output thread. The
dashboard preview and REST/WebSocket contracts continue to come from the
existing Python runtime. Dashboard state adds optional output p50/p95/p99,
frame IPC round-trip, and sequential output-skew diagnostics; older clients
retain explicit compatibility defaults.

## Architecture audit

| Area | Current owner and boundary | Migration implication |
| --- | --- | --- |
| Runtime and lifecycle | `LumiStripeRuntime` is the public façade. `RuntimeWorker` owns mutable playback, audio, topology, and output state and serializes commands. | Keep the worker and public runtime API in Python while the optional Rust process owns configured hardware. |
| Animations | `lumistripe.animation.catalog` registers `AnimationDefinition` values; `AnimationPlayer` runs Python animation implementations over the core pixel buffers. | Animations remain Python-owned. |
| Playback | `lumistripe.playback` contains configuration, state, rendering, selection, transitions, musical timing, and sequence policy. | Playback policy remains Python-owned. |
| Audio | Capture uses bounded buffers; worker-side DSP uses typed `AudioFrame`, `MusicFeatures`, and statistics. Optional native `_audio` code accelerates processing. | Capture callbacks, audio analysis, and DSP remain Python/C. |
| Output | The web runtime applies output gates, color correction, zone mapping, brightness, and power limits before publishing complete frames. | Rust owns physical device handles, transfers, blackout, and device lifecycle. Python retains render buffers and policy. |
| API and dashboard | FastAPI routes use feature-oriented Pydantic contracts. WebSocket preview has its own versioned binary protocol. REST TypeScript types are generated from OpenAPI. | Existing endpoints and preview messages remain unchanged; additive output diagnostic fields are generated from OpenAPI. |

## Workspace and process boundary

The independent Cargo workspace is under [`rust/`](../../rust/Cargo.toml):

| Crate | Responsibility |
| --- | --- |
| `lumistripe-engine-config` | Typed per-output backend configuration and validation. |
| `lumistripe-engine-protocol` | Versioned JSON control messages and fixed-size binary frame/ack headers. |
| `lumistripe-engine-output` | Output-set lifecycle, SM16716 encoding, SPI, gpiomem, gpio-cdev, and mock output drivers. |
| `lumistripe-engine-runtime` | Private Unix socket server, bounded Rust frame queue, output owner thread, health reporting, and socket cleanup. |
| `lumistripe-engine` | Standalone `lumistripe-engine` process entry point. |

The Python adapter in `lumistripe_web.runtime.rust_engine` starts one process
per runtime, configures the complete topology, sends contiguous RGBA buffers
over one persistent frame socket, and requests graceful blackout and shutdown.
Control messages use bounded JSON lines; frame sets use a fixed-size header
and raw bytes. Protocol details and shared fixtures are in
[`rust-engine-ipc-v2.md`](../protocols/rust-engine-ipc-v2.md).
Existing version 1 single-output clients still work for health, simulated
configuration, RGB frames, and shutdown; multi-output hardware uses version 2.

## Selecting the backend

The Python engine remains the default. Build and select Rust explicitly:

```bash
cargo build --release --locked --manifest-path rust/Cargo.toml --bin lumistripe-engine
LUMISTRIPE_ENGINE_BINARY="$PWD/rust/target/release/lumistripe-engine" \
  uv run lumistripe-web --engine-backend rust
```

Alternatively, pass `--engine-binary PATH`. Programmatic callers can set
`RuntimeSettings(engine_backend="rust", engine_binary=Path(...))`. With
`--hardware`, Rust opens the configured SPI or GPIO devices. Without
`--hardware`, it uses mock outputs while Python continues to render frames.
Leaving `--engine-backend` unset preserves the Python backend.

## Raspberry Pi deployment

Build the engine for the target Pi and install the standalone binary outside
the checkout so application updates do not replace it:

```bash
cargo build --release --locked --manifest-path rust/Cargo.toml --bin lumistripe-engine
sudo install -D -o root -g root -m 0755 \
  rust/target/release/lumistripe-engine \
  /usr/local/libexec/lumistripe/lumistripe-engine
```

To opt the systemd service in, inspect its installed `ExecStart` with
`systemctl cat lumistripe-web.service`, then add a drop-in with
`systemctl edit lumistripe-web.service`. Reset and replace `ExecStart` with the
current command plus these arguments:

```text
--engine-backend rust --engine-binary /usr/local/libexec/lumistripe/lumistripe-engine
```

Run `systemctl daemon-reload` and restart the service. The service user must
have the existing SPI/GPIO device permissions. Remove the drop-in and reload
systemd to return to the default Python output backend. Keep the Python/C
implementation installed as the rollback path.

## Feature parity matrix

| Capability | Python backend | Rust backend | Validation state |
| --- | --- | --- | --- |
| FastAPI routes, REST models, dashboard, WebSocket preview | Supported | Existing Python path remains active | Preserved |
| Animation catalog, playback, audio, and DSP | Supported | Receives completed RGBA frames | Python-owned |
| Frame correction, mapping, brightness, and power policy | Supported | Receives post-policy frames | Python-owned |
| Mock output | In-memory controller | Mock output through the same queue and process used by hardware | Rust and Python process tests |
| SPI output | Optional Python spidev driver | Rust spidev, mode 0/MSB-first, one encoded transaction per strip | Mock encoder tests; Pi validation pending |
| GPIO output | Python gpiomem fast path and libgpiod fallback | Device-tree-selected BCM and RP1 gpiomem register writers with gpio-cdev fallback | Mock wire and register-map tests; Pi validation pending |
| Configuration validation | Python runtime settings and topology validation | Per-output backend and device/pin uniqueness checks | Rust tests |
| Process health and shutdown | Python worker lifecycle/watchdog | One owned process, bounded frames, output telemetry, blackout and close | Process integration tests |

The dashboard reports Python render time, frame IPC send/ack round-trip, frame
queue-to-completion latency with p50/p95/p99, queue delay, measured sequential
transmission duration and output skew, transmitted FPS, latest-wins drops, and device/transport
errors. Interrupted, temporarily unavailable, and timed-out hardware writes get
one bounded retry; persistent errors enter health telemetry. Newly opened Rust
outputs are blacked before configuration succeeds; shutdown blackouts and
releases devices, and process exit releases kernel handles if shutdown times
out. The original Python hardware output thread also reports latency
percentiles and sequential completion skew; its IPC latency remains `null`.

## Work beyond output migration

1. Select deterministic, UI-independent core primitives with narrow inputs and
   outputs. Add Python/Rust golden fixtures and benchmarks before replacing a
   Python implementation.
2. Port playback policy only after catalog and time/beat inputs have a stable
   typed contract. Compare `PlaybackDecision` and `RenderPlan` values across
   both implementations.
3. Consider audio DSP only after preserving bounded capture callbacks and
   validating timestamping, calibration, and telemetry parity.
4. Run trusted Raspberry Pi validation for GPIO/SPI waveforms, transfer timing,
   cleanup, and failure recovery. This checkout has no attached LED hardware
   for electrical validation.

## Validation and measurement

```bash
CARGO_HOME=/tmp/lumistripe-cargo-home cargo fmt --manifest-path rust/Cargo.toml --all -- --check
CARGO_HOME=/tmp/lumistripe-cargo-home cargo test --locked --manifest-path rust/Cargo.toml --workspace
CARGO_HOME=/tmp/lumistripe-cargo-home cargo build --locked --manifest-path rust/Cargo.toml --workspace
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python -m pytest -q --no-cov \
  apps/lumistripe-web/tests/test_rust_engine_adapter.py
```

The Python integration test expects the debug binary at
`rust/target/debug/lumistripe-engine` or the path in
`LUMISTRIPE_ENGINE_BINARY`. CI builds it before running that test. The local
Python SM16716 encoder with a mock spidev sink versus Rust binary IPC, output
worker, and mock SPI encoder comparison is available in
[`scripts/benchmark_rust_engine.py`](../../scripts/benchmark_rust_engine.py).
Use `--require-c-encoder` to require the optional native C encoder. It reports
median/p95 Python update and Rust IPC-ack times, plus Rust output-worker frame
latency percentiles and dropped frames. Both encoders run in software and the
mock sinks discard transfers; neither side measures physical SPI/GPIO timing.

The Rust workspace is tested and built on x86_64 and hosted Linux ARM64 in CI.
The ARM64 job is separate from the self-hosted Raspberry Pi job and does not
send pull-request code to persistent device hardware.
