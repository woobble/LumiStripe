# Incremental Rust engine migration

## Goal and current boundary

LumiStripe keeps its application control plane in Python. The opt-in Rust
backend owns animation and visual-effect pixel rendering, reusable frame
buffers, final output processing, and every configured device in one Rust
process. Rust owns Cycling selection and the Dynamic selector, activity gate,
idle scene, and beat/bar/phrase switch quantization when its monotonic scheduler
is active. Rust tracks beat-clock state from timestamped beat events. Python
retains playback configuration and API state. Audio stays on the Python/native-C
path by default; an opt-in Rust audio worker owns capture, DSP, beat detection,
and music-structure classification. In Rust audio mode, PCM stays inside the
Rust process and feeds the scheduler directly. Python polls typed health
snapshots for existing dashboard, settings, and calibration flows. The Rust
worker replays timestamped structure and beat events through bounded per-output
history until the scheduler acknowledges them. Saved Dynamic
sequences run in Rust against estimated 4/4 bar boundaries, with their progress
mirrored into Python for API state and fallback. The original
Python/C renderer remains available as the default backend and rollback path.

For supported scenes, the Python worker sends typed, timestamped render inputs
through protocol v3; it does not send completed pixel frames. Rust renders
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
compatibility. Rust audio mode classifies music structure in Rust; Python keeps
the classifier active only for the Python audio fallback. Rust owns Dynamic
effect triggers, cooldowns, beat subdivisions, candidate selection, active
effect state, and drop accents. Python mirrors Rust's per-output beat clock for
dashboard telemetry.

The Python runtime sends complete scene snapshots through a bounded FIFO of
64 reliable control updates. Python-driven audio uses a separate one-slot,
latest-wins mailbox owned by `RustEngineOutputThread`; native Rust audio submits
features and replayable events directly to the Rust scheduler. The bridge
processes scene revisions in order, then applies the newest Python audio
snapshot after queued scene changes. Beat and structure events remain pending
until Rust health confirms their sequences. This keeps the playback worker off
the control IPC path without dropping scene changes or building an audio
backlog. Scene queue overflow and asynchronous control rejection are reported
back to the runtime worker, which selects the Python/C scene fallback. The lower-level
`RustEngineProcess.render_frame()` remains synchronous for compatibility and
process integration tests.

## Architecture audit

| Area | Current owner and boundary | Migration implication |
| --- | --- | --- |
| Runtime and lifecycle | `LumiStripeRuntime` is the public façade. `RuntimeWorker` owns mutable playback, audio, topology, and output state and serializes commands. | Keep the worker and public runtime API in Python while the optional Rust process owns configured hardware. |
| Animations | `lumistripe.animation.catalog` remains canonical for names, settings schemas, defaults, and timing. `AnimationPlayer` and Python/C kernels remain the rollback renderer. | Rust imports the generated catalog and renders all 54 registered animations and 19 effect definitions into reusable buffers; differential golden fixtures check Python/C parity. |
| Playback | Python retains configuration and API state; Rust owns scheduled Cycling and supported Dynamic selection/activity/idle decisions. | Static playback state, musical timing, sequence policy, effects, and complete state ownership still need migration. |
| Audio | Bounded PortAudio callbacks feed worker-side native-C DSP and typed audio models. | Optional CPAL capture uses a bounded, preallocated SPSC queue; Rust owns DSP, beat events, music structure, and scheduler updates. Python polls the same telemetry and calibration models. |
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
| `lumistripe-engine-audio` | CPAL Linux input discovery and capture, bounded PCM handoff, preallocated DSP, beat events, and music-structure tracking. |
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
[`rust-engine-ipc-v3.md`](../protocols/rust-engine-ipc-v3.md). Version 3 adds
audio lifecycle and telemetry commands while preserving the v2 render and LSF2
binary-frame formats. Existing v1 and v2 clients remain supported for their
original commands.

## Selecting the backend

The Python engine remains the default. Build and select Rust explicitly:

```bash
cargo build --release --locked --manifest-path rust/Cargo.toml --bin lumistripe-engine
LUMISTRIPE_ENGINE_BINARY="$PWD/rust/target/release/lumistripe-engine" \
  uv run lumistripe-web --engine-backend rust --audio-backend rust
```

Alternatively, pass `--engine-binary PATH`. Programmatic callers can set
`RuntimeSettings(engine_backend="rust", engine_binary=Path(...))`. With
`--hardware`, Rust opens the configured SPI or GPIO devices. Without
`--hardware`, it uses mock outputs while Rust renders supported scenes.
`--audio-backend rust` requires `--engine-backend rust`; it starts one Rust
capture/DSP session and leaves Python capture inactive. Linux microphone, USB,
Bluetooth-source, and Spotify-related routing go through the existing source
manager. `--audio-device` selects a Rust-discovered host/device selector or
matching device name. Leaving `--audio-backend` unset preserves Python/native-C
capture and DSP.

## Raspberry Pi deployment

The persistent Raspberry Pi CI runner uses a host-provisioned Rust toolchain
and system headers; workflow steps run as the runner account and do not invoke
`sudo` or bootstrap Rust with `curl`. Install the OS prerequisites once as root:

```bash
apt-get update
apt-get install --yes --no-install-recommends \
  build-essential ca-certificates curl pkg-config libasound2-dev \
  libpipewire-0.3-dev libpulse-dev
```

As the runner account, install a stable Rust toolchain with rustup:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y --profile minimal --default-toolchain stable --component rustfmt
```

Confirm `rustc` and `cargo` are on that account's `PATH` when the runner service
starts, then restart the service. The Pi job verifies those tools, ARM64, the
CA bundle, ALSA/PipeWire/PulseAudio headers and pkg-config metadata, and
recursive submodule checkout before building.

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
| Music structure and effect scheduling | Python MusicStructureTracker | Rust audio mode owns beat and structure state and submits replayed edges directly; fallback Python audio sends timestamped section/drop events. Rust owns Dynamic effect scheduling and drop accents in both modes | Python/C-to-Rust PCM fixtures plus Rust scheduler and Python adapter tests |
| Beat clock tracking and event-history integration | Python `BeatClockTracker` for fallback playback | Rust consumes timestamped beat batches, deduplicates gaps, duplicates, jitter, and delayed events, then uses the three-minute event history for Dynamic selection and scheduled mock output | Python/Rust clock comparison over three minutes of event history, Dynamic gate and selection, output transmission, and capture-to-LED telemetry integration test |
| Audio capture, DSP, beat detection, and music structure | Python PortAudio plus native-C DSP | Optional CPAL capture, Rust DSP and structure tracker, direct bounded scheduler handoff | Python/C differential PCM fixtures, runtime IPC tests, and synthetic benchmarks; live-device validation pending |
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

When Rust audio is disabled, Python continues to classify music structure and
sends timestamped beat/audio features plus bounded section/drop events. When
Rust audio is active, Rust is the sole beat and structure authority and sends
those events directly to the scheduler. In both modes, Rust acknowledges
consumed event sequences. While Rust owns a scene, Python polls for policy
inputs and mirrors API state without advancing `AnimationPlayer`; Rust's
monotonic scheduler advances animation and effect state. A bounded, ordered
scene-control queue and replaceable audio mailbox carry Python-driven playback
changes independently. Saved Dynamic sequences use timestamped section edges
only as a late fallback after the configured bar duration is exhausted; they
never shorten a step, and the configured sync offset still applies.

The software path is covered by deterministic Python/native-C reference PCM
fixtures and x86_64/ARM64 CI builds. The current development host has no
`/dev/snd` devices, so CPAL-versus-PortAudio compatibility, callback
latency/jitter, disconnect recovery, and live audio-to-LED latency have not been
measured on hardware. Raspberry Pi 4/5 audio benchmarks remain outstanding.
The supplied Pi 5 renderer run reported no Rust scheduler deadline misses, but
Dynamic output drops peaked at four frames in 600-LED cases and output cadence
was 34.8–40.6 FPS. The JSON did not include selected animation names or a
detailed render-stage profile, so the reduced Dynamic FPS cannot yet be
attributed to rendering, IPC, DSP, or physical transmission. Re-run the
documented matrix with a source revision, selected animations,
temperature/governor data, and stage timings before making an optimization
claim. Physical GPIO/SPI waveform, timing, cleanup, and failure-recovery checks
also remain outstanding; mock tests and hosted ARM64 runs do not establish
electrical timing safety.

## Validation and measurement

Run the Criterion animation-render and audio DSP microbenchmarks and focused
Loom model for latest-frame replacement with:

```bash
cargo bench --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-engine-animation-state --bench animation_render
cargo test --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-engine-runtime --features loom-tests \
  latest_render_slot_stays_bounded_across_producer_consumer_interleavings
cargo bench --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-engine-audio --bench audio_pipeline -- \
  --sample-size 10 --warm-up-time 1 --measurement-time 1
```

Loom checks the runtime's bounded single-frame slot under producer/consumer
interleavings. It does not replace process-level IPC and scheduler tests.

For a directly comparable Python/native-C versus Rust DSP run, use the same
host and CPU governor. Both harnesses process a 44.1 kHz, 80 Hz + 1.6 kHz input
at one, four, and sixteen 512-sample hops per call:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_audio_dsp.py \
  --iterations 4096
CARGO_HOME=/tmp/lumistripe-cargo-home cargo run --release \
  --manifest-path rust/Cargo.toml -p lumistripe-engine-audio \
  --bin benchmark_audio -- --iterations 4096
```

The reports include per-call p50/p95/p99 latency, real-time headroom, CPU,
resident-memory peak, and steady-state Rust allocator counts. Python's
`tracemalloc` values cover Python-managed memory only; allocations inside the
native C extension are not visible. Run
`scripts/benchmark_audio_capture.py --warmup 2 --duration 20` with a live input
to measure PortAudio callback health. Rust `AudioStatus` reports callback
duration p50/p95/p99, capture-to-callback latency p50/p95/p99, and p95
callback-interval jitter; those metrics require a live input and are not
represented by synthetic DSP benchmarks.

The measured synthetic x86_64 results and their limits are recorded in
[`rust-audio-dsp-comparison.md`](../performance/rust-audio-dsp-comparison.md).

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

To compare the live PortAudio and CPAL capture paths through Dynamic playback,
use a microphone source and request both audio backends. Python-rendered cases
use the Python audio path; Rust-rendered cases run once with Python audio and
once with native Rust audio:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --engine-backends python,rust --audio-backends python,rust \
  --engine-binary rust/target/release/lumistripe-engine \
  --led-counts 300,600 --output-counts 1,2 --layouts independent \
  --zone-counts 1 --modes dynamic --power-limits off \
  --audio-source mic --warmup 10 --duration 60 \
  --output /home/woobb/Documents/lumistripe-rust-renderer-benchmarks/audio-rpi5.json
```

This adds `audio_capture` callback count/rate, callback duration and interval
jitter p50/p95/p99 where available, queue/drop counts, processed samples, and
audio-feature-to-LED latency. The Python callback profile is opt-in and bounded
to 512 samples; the Rust status window is bounded to 64 callbacks. The synthetic
demo source cannot exercise native capture, so use a real microphone, Bluetooth
source, or Spotify monitor for this comparison.

### Supplied Raspberry Pi 5 runtime matrix

A user-supplied run identified a Raspberry Pi 5 Model B Rev 1.0 running
Linux 7.0.0-1020-raspi on `aarch64`. It completed all 32 matrix cases with
physical SPI output enabled and demo audio. Rust reported no output failures
and no scheduler deadline misses. Static output ran at about 60 FPS; Dynamic
output ranged from 34.8 to 40.6 FPS. Rust output drops peaked at four frames in
the 600-LED Dynamic cases. Measured SPI transmission time was about 7.9–8.1 ms
for 300 LEDs and 15.7–15.9 ms for 600 LEDs. Rust process RSS stayed between
11.5 and 11.7 MiB, and combined Python/Rust process CPU use ranged from 5.8%
to 19.1%. In Dynamic Rust cases, audio-feature-to-LED latency p50 ranged from
27.7 to 39.4 ms, p95 from 53.3 to 71.2 ms, and p99-minus-p50 jitter from 20.5
to 34.7 ms.

In Rust cases, the generic `frames`, `effective_fps`, `expected_interval_ms`,
`actual_frame_interval_ms`, and `missed_deadlines` fields describe the Python
runtime supervisor's `_step` loop. That loop ran at about 20 Hz in this run;
Rust's independently scheduled physical output cadence is reported by
`output_effective_fps`, with scheduler misses in
`output_scheduler_missed_deadlines`. Do not use the generic frame fields as
Rust renderer measurements. The supplied JSON did not include a source
revision, thermal/governor readings, or selected Dynamic animation names, so
those details still need to be recorded for a reproducible comparison. Pi 4
runtime measurements and physical waveform, shutdown/blackout, and crash
recovery checks also remain outstanding.

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
