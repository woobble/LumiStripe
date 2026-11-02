# Incremental Rust engine migration

## Goal and current boundary

LumiStripe keeps its application control plane in Python. The opt-in Rust
backend owns animation and visual-effect pixel rendering, reusable frame
buffers, final output processing, and every configured device in one Rust
process. Rust owns Cycling selection and the Dynamic selector, activity gate,
idle scene, and beat/bar/phrase switch quantization when its monotonic scheduler
is active. Rust tracks beat-clock state from Python's timestamped beat events.
Python retains playback configuration and API state, audio capture and DSP,
and music-structure classification. Python sends timestamped structure edges
through a bounded per-output replay stream; Rust acknowledges their sequences
after a successful scheduled render and uses them for Dynamic selection and
effect scheduling. Saved Dynamic
sequences run in Rust against estimated 4/4 bar boundaries, with their progress
mirrored into Python for API state and fallback. The original
Python/C renderer remains available as the default backend and rollback path.

For supported scenes, the Python worker sends typed, timestamped render inputs
through protocol v2; it does not send completed pixel frames. Rust renders
directly into reusable logical and physical buffers and passes those buffers
to its output worker. Rust applies transitions, zone mapping, color correction,
power limits, blackout, and output reversal. Unsupported scene modes can still
use the Python/C path. The dashboard preview and REST/WebSocket contracts stay
in the Python runtime, with separate playback scheduling, Rust scene-render,
and output timing diagnostics.

When a render request opts into the Rust scheduler, Rust autonomously renders
the latest submitted scene on monotonic deadlines between scene updates. The
runtime sends per-output Cycling configuration and animation settings once;
Rust owns fixed-interval and per-animation switches, seeded shuffle state,
animation restarts, and transitions. Health reports the current selections and
scene revisions, which Python mirrors for API state and fallback. Static and
Solid scenes also render repeatedly in Rust without repeated scene IPC.
Selection, activity detection, idle rendering, and beat/bar/phrase quantized
switching run in Rust for Dynamic configurations. Saved animation sequences
also run in Rust: each output waits for a confident beat clock, advances its
steps on estimated bar boundaries, and reports sequence progress through
health. Python mirrors Rust's gate, selection, and sequence state for API
compatibility. Python still classifies music structure, while Rust owns Dynamic
effect triggers, cooldowns, beat subdivisions, candidate selection, active
effect state, and drop accents. Python mirrors Rust's per-output beat clock for
dashboard telemetry; the Python tracker advances only on the fallback path.

The Python runtime sends complete scene snapshots through a bounded FIFO of
64 reliable control updates and sends audio through a separate one-slot,
latest-wins mailbox owned by `RustEngineOutputThread`. The bridge processes
scene revisions in order, then applies the newest audio snapshot after queued
scene changes. Beat and structure events remain in the audio snapshot until
Rust health confirms their sequences. This keeps the playback worker off the
control IPC path without dropping scene changes or building an audio backlog.
Scene queue overflow and asynchronous control rejection are reported back to
the runtime worker, which selects the Python/C scene fallback. The lower-level
`RustEngineProcess.render_frame()` remains synchronous for compatibility and
process integration tests.

## Architecture audit

| Area | Current owner and boundary | Migration implication |
| --- | --- | --- |
| Runtime and lifecycle | `LumiStripeRuntime` is the public façade. `RuntimeWorker` owns mutable playback, audio, topology, and output state and serializes commands. | Keep the worker and public runtime API in Python while the optional Rust process owns configured hardware. |
| Animations | `lumistripe.animation.catalog` remains canonical for names, settings schemas, defaults, and timing. `AnimationPlayer` and Python/C kernels remain the rollback renderer. | Rust imports the generated catalog and renders all 54 registered animations and 19 effect definitions into reusable buffers; differential golden fixtures check Python/C parity. |
| Playback | Python retains configuration and API state; Rust owns scheduled Cycling and supported Dynamic selection/activity/idle decisions. | Static playback state, musical timing, sequence policy, effects, and complete state ownership still need migration. |
| Audio | Capture uses bounded buffers; worker-side DSP uses typed `AudioFrame`, `MusicFeatures`, and statistics. Optional native `_audio` code accelerates processing. | Capture callbacks, audio analysis, and DSP remain Python/C. |
| Output | The Python backend keeps its existing output path. | The Rust backend owns frame finalization and output buffers, then drives configured mock, SPI, or GPIO devices on its output-owner thread. |
| API and dashboard | FastAPI routes use feature-oriented Pydantic contracts. WebSocket preview has its own versioned binary protocol. REST TypeScript types are generated from OpenAPI. | Existing endpoints and preview messages remain unchanged; additive output diagnostic fields are generated from OpenAPI. |

## Workspace and process boundary

The independent Cargo workspace is under [`rust/`](../../rust/Cargo.toml):

| Crate | Responsibility |
| --- | --- |
| `lumistripe-engine-config` | Typed per-output backend configuration and validation. |
| `lumistripe-engine-protocol` | Versioned JSON control messages and fixed-size binary frame/ack headers. |
| `lumistripe-engine-output` | Output-set lifecycle, SM16716 encoding, SPI, gpiomem, gpio-cdev, and mock output drivers. |
| `lumistripe-engine-runtime` | Private Unix socket server, bounded Rust frame queue, output owner thread, health reporting, and socket cleanup. |
| `lumistripe-engine-animation-definitions` | Generated catalog names, settings schemas, defaults, and animation timing. |
| `lumistripe-engine-animation-state` | Stateful animation instances, typed render inputs, reusable animation frames, and golden parity tests. |
| `lumistripe-engine-render-primitives` | RGBA frame buffers, audio render inputs, and reusable zone geometry. |
| `lumistripe-engine-compositing` | Effect composition and timestamped scene transitions. |
| `lumistripe-engine-effects` | Stateful visual effects, deterministic random state, layer scheduling inputs, and reusable effect buffers. |
| `lumistripe-engine` | Standalone `lumistripe-engine` process entry point. |

The Python adapter in `lumistripe_web.runtime.rust_engine` starts one process
per runtime, configures the complete topology, sends typed rendering inputs
over bounded JSON control messages, and requests graceful blackout and
shutdown. The persistent binary frame socket remains available for rollback
and compatibility paths. Protocol details and shared fixtures are in
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
`--hardware`, it uses mock outputs while Rust renders supported scenes.
Leaving `--engine-backend` unset preserves the Python/C renderer.

## Raspberry Pi deployment

The persistent Raspberry Pi CI runner uses a host-provisioned Rust toolchain
and system headers; workflow steps run as the runner account and do not invoke
`sudo` or bootstrap Rust with `curl`. Install the OS prerequisites once as root:

```bash
apt-get update
apt-get install --yes --no-install-recommends \
  build-essential ca-certificates curl libasound2-dev
```

As the runner account, install a stable Rust toolchain with rustup:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y --profile minimal --default-toolchain stable --component rustfmt
```

Confirm `rustc` and `cargo` are on that account's `PATH` when the runner service
starts, then restart the service. The Pi job verifies those tools, ARM64, the
CA bundle, audio headers, and recursive submodule checkout before building.

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
| Animation catalog and pixel rendering | Supported | All catalog animations and effects render in Rust for supported scene modes | Python/C rollback remains available |
| Rust monotonic frame scheduling | Python worker loop | Deadline-based scheduler with animation timing cap, skipped-deadline telemetry, and bounded latest render | Rust scheduler integration tests |
| Cycling selection | Python `PlaybackEngine` | Rust owns sequential/shuffle choice, fixed/per-animation deadlines, animation restart, and transition when scheduler is active | Rust runtime and Python adapter IPC tests |
| Dynamic selector, activity gate, and idle behavior | Python `DynamicSelector` and activity policy | Rust owns selection, activity hysteresis, idle blackout/solid/animation scenes, and per-output gate state; Python mirrors state for the API | Rust gate and idle integration tests plus Python worker and adapter tests |
| Beat/bar/phrase switch quantization | Python `QuantizedSwitchCoordinator` | Rust schedules Dynamic selector candidates against its per-output beat clock and honors confidence, phrase length, subdivisions, transition holds, and sync offsets | Rust controlled-clock tests plus adapter serialization tests |
| Saved Dynamic animation sequences | Python `AnimationSequenceCoordinator` | Rust schedules per-output steps against estimated 4/4 bar boundaries, with clock confidence gating, pause/reanchor behavior, repeat/completion policy, late section-edge fallback, and health telemetry; Python mirrors progress | Rust controlled-clock tests plus a three-minute 30 FPS Dynamic sequence simulation with beat jitter, gaps, duplicates, and a delayed event; Rust process and Python worker adapter tests |
| Music structure and effect scheduling | Python MusicStructureTracker | Python classifies music structure and sends timestamped, replayed section/drop edges; Rust owns Dynamic effect triggers, cooldowns, bounded beat subdivision scheduling, active effect state, and drop accents | Rust scheduler checks plus Python adapter and differential IPC validation |
| Beat clock tracking and event-history integration | Python `BeatClockTracker` for fallback playback | Rust consumes timestamped beat batches, deduplicates gaps, duplicates, jitter, and delayed events, then uses the three-minute event history for Dynamic selection and scheduled mock output | Python/Rust clock comparison over three minutes of event history, Dynamic gate and selection, output transmission, and capture-to-LED telemetry integration test |
| Audio capture and DSP | Python audio pipeline | Remain in Python by scope | Existing Python audio tests |
| Transitions, zones, correction, brightness, blackout, and power limits | Supported | Finalized in Rust against reusable frame buffers | Python path remains the rollback implementation |
| Mock output | In-memory controller | Mock output through the same queue and process used by hardware | Rust and Python process tests |
| SPI output | Optional Python spidev driver | Rust spidev, mode 0/MSB-first, one encoded transaction per strip | Mock encoder tests; Pi validation pending |
| GPIO output | Python gpiomem fast path and libgpiod fallback | Device-tree-selected BCM and RP1 gpiomem register writers with gpio-cdev fallback | Mock wire and register-map tests; Pi validation pending |
| Configuration validation | Python runtime settings and topology validation | Per-output backend and device/pin uniqueness checks | Rust tests |
| Process health and shutdown | Python worker lifecycle/watchdog | One owned process, bounded frames, output telemetry, blackout and close | Rust-backend tests cover saved startup restoration, engine-crash detection/recovery callback, owner-death cleanup, and shutdown |

The dashboard reports playback scheduling time, Rust scene-render time, frame
IPC send/ack round-trip, frame
queue-to-completion latency with p50/p95/p99, queue delay, measured sequential
transmission duration and output skew, transmitted FPS, latest-wins drops, and device/transport
errors. Interrupted, temporarily unavailable, and timed-out hardware writes get
one bounded retry; persistent errors enter health telemetry. Newly opened Rust
outputs are blacked before configuration succeeds; shutdown blackouts and
releases devices, and process exit releases kernel handles if shutdown times
out. The original Python hardware output thread also reports latency
percentiles and sequential completion skew; its IPC latency remains `null`.

## Remaining scope

Music-structure classification remains in Python and supplies Rust with
timestamped beat/audio features plus bounded section/drop edge events. The
bridge replays beat and structure events until Rust acknowledges each output's
consumed sequence. When the structure-event window fills, the worker falls back
to Python playback instead of dropping unacknowledged edges. While Rust owns a
scene, Python polls for policy inputs and mirrors API state without advancing
`AnimationPlayer`; Rust's monotonic scheduler advances animation and effect
state. The Python advancement path remains active for the explicit fallback. A
bounded, ordered scene-control queue and replaceable audio mailbox now carry
playback changes independently; health exposes their accepted revision and
sequence. Saved Dynamic sequences use timestamped section edges only as a late
fallback after the configured bar duration is exhausted; they never shorten a
step, and the configured sync offset still applies. Audio capture and DSP are
explicitly outside this migration.
Raspberry Pi 4/5 throughput measurements and physical GPIO/SPI
waveform, timing, cleanup, and failure-recovery validation also remain
outstanding; mock tests and hosted ARM64 runs do not establish electrical
timing safety.
Keep those device checks separate from software parity results.

## Validation and measurement

Run the Criterion animation-render microbenchmarks and focused Loom model for
latest-frame replacement with:

```bash
cargo bench --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-engine-animation-state --bench animation_render
cargo test --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-engine-runtime --features loom-tests \
  latest_render_slot_stays_bounded_across_producer_consumer_interleavings
```

Loom checks the runtime's bounded single-frame slot under producer/consumer
interleavings. It does not replace process-level IPC and scheduler tests.

Run the real-runtime comparison on the Raspberry Pi and save the JSON alongside
the renderer microbenchmarks. This matrix compares Python and Rust with one and
two physical outputs, static and Dynamic playback, and power limiting off/on:

```bash
cargo build --release --locked --manifest-path rust/Cargo.toml --workspace
LUMISTRIPE_BUILD_EXTENSIONS=all UV_CACHE_DIR=/tmp/lumistripe-uv-cache \
  uv sync --locked --all-extras --dev --reinstall-package lumistripe-core
mkdir -p /home/woobb/Documents/lumistripe-rust-renderer-benchmarks
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --engine-backends python,rust \
  --engine-binary rust/target/release/lumistripe-engine \
  --led-counts 300,600 --output-counts 1,2 --layouts independent \
  --zone-counts 1 --modes static,dynamic --power-limits off,on \
  --audio-source demo --warmup 10 --duration 60 \
  --output /home/woobb/Documents/lumistripe-rust-renderer-benchmarks/runtime-rpi5.json
```

The command runs 32 cases and takes about 37 minutes plus startup time. With
`--hardware`, it sends output to the configured SPI strips; omitting the flag
uses virtual outputs and measures software scheduling only. Results separate
Python and Rust process CPU, include per-thread CPU utilization when Linux
procfs is available, and report each process's RSS. The legacy
`cpu_utilization_percent` field remains the Python process measurement. Output
metrics include render, queue, transfer, and IPC latency; p50/p95/p99 frame
latency; Rust scheduler deadline misses; and accepted, pending, and replaced
scene/audio update counts. Rust-mode audio results also include capture-to-LED
feature latency p50/p95/p99 and p99-minus-p50 synchronization jitter when the
audio source provides capture timestamps.

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
