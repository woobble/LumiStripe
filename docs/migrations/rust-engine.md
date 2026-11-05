# Incremental Rust engine migration

> **Current deployment status:** this guide covers the Rust engine adapter in
> the legacy Python compatibility application. New installations use the
> native Rust web application, engine, and CLI by default. Python remains for
> explicit rollback, differential checks, and migration work; see the
> [Python retirement inventory](python-retirement-inventory.md) for the current
> deployment state.

## Goal and current boundary

The Python compatibility application's control plane can use the opt-in Rust
engine for animation and visual-effect pixel rendering, reusable frame
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
mirrored into Python for API state and fallback. Within that compatibility
application, the original Python/C renderer remains available as its default
backend and rollback path.

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

Within the Python compatibility application, the Python engine remains the
default. Build and select its Rust engine adapter explicitly:

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

The standalone Axum web binary reads the Python-compatible versioned settings
file at `~/.config/lumistripe/settings.json`. Override it with
`--settings-file PATH` or `LUMISTRIPE_SETTINGS_FILE`. It rejects invalid or
unsupported settings rather than replacing them with defaults. Its actor owns
settings writes and atomically persists identity, topology, startup, playback,
audio-profile, calibration, transition, music-idle, and sequencer changes while
preserving unrelated fields. Scene and topology updates are applied only after
the engine acknowledges them; a failed persistence or reconfiguration restores
the previous acknowledged scene. Saved sequences are sent through the existing
engine scene protocol to the Rust scheduler. Stripe topology and calibration
sessions run through the same bounded application actor.
The engine supervisor snapshots the latest acknowledged configuration, scene,
and active Rust audio input. Each new engine process receives that audio request
once after its configuration and scene; an unavailable input leaves rendering
available and is reported through the engine audio status.

## Raspberry Pi deployment

The trusted Raspberry Pi CI job verifies its native build packages first and
installs only when something is missing. It uses `sudo` when available or runs
`apt-get` directly when the runner is root. A non-root runner without `sudo`
downloads signed Debian packages into the job's temporary directory and uses
that private header/library/pkg-config overlay for the remainder of the job;
it does not change the persistent runner's system packages. The provisioning
step is implemented in
[`scripts/provision_rpi_ci_native_deps.sh`](../../scripts/provision_rpi_ci_native_deps.sh).
The job uses a host-provisioned Rust toolchain and does not bootstrap Rust with
`curl`.
The job only runs for pushes to `main` and manual dispatches from `main`; pull
request code stays on hosted runners. Outside CI, install the OS prerequisites
once as root:

```bash
apt-get update
apt-get install --yes --no-install-recommends \
  build-essential ca-certificates curl pkg-config libasound2-dev libclang-dev \
  libdbus-1-dev libpipewire-0.3-dev libpulse-dev pulseaudio-utils
```

As the runner account, install a stable Rust toolchain with rustup:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y --profile minimal --default-toolchain stable --component rustfmt
```

Confirm `rustc` and `cargo` are on that account's `PATH` when the runner service
starts, then restart the service. The Pi job verifies those tools, ARM64, the
CA bundle, ALSA/PipeWire/PulseAudio headers and pkg-config metadata, and
recursive submodule checkout before building. The available Pi 5 has the
packages installed, and the provisioner confirmed that path without changing
system packages. The no-root overlay path is covered by
`deploy/tests/test_rpi_ci_provisioning.py`; the trusted Pi workflow has not yet
run with the new provisioning step, so end-to-end runner validation remains
open.

To include live microphone or USB capture in the Pi job, set the repository
variable `LUMISTRIPE_RUST_AUDIO_DEVICE` to a CPAL selector or matching device
name. The job records a 20-second Rust audio-to-virtual-LED capture sample in
its benchmark artifact. Run once per physical input. Bluetooth and Spotify
monitor routes require an active source-manager session and should be measured
separately with `--audio-source bluetooth` or `--audio-source spotify`.

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
| FastAPI routes, REST models, dashboard, WebSocket preview | Supported for rollback | The standalone Axum server is the new-install default and exposes compatible state, control, topology, calibration, animation, startup, audio-tuning, transition, and sequencer routes. The Python runtime remains the default only within this compatibility application. | Rust HTTP/OpenAPI/WebSocket tests and Python/Rust mock-output API parity test |
| Versioned dashboard settings | Full v1–v12 loader and atomic persistence | Rust reads Python-generated v1–v12 files and transactionally persists identity, topology, startup, playback, animation, transition, audio-profile, calibration, and sequencer settings while preserving unrelated fields | Rust settings compatibility, persistence, acknowledgement, and rollback tests |
| Startup playback restoration | Supported | Rust restores remembered mode, color, animation, brightness, and blackout; global playback updates refresh remembered state when restoration is enabled | Rust startup-scene and HTTP persistence tests |
| Animation settings | Supported for all catalogued settings | Rust validates against the generated catalog, supports shared and independent-output values, applies values to scenes, and resets to catalog defaults | Rust animation-settings HTTP and persistence tests |
| Sequencer settings and saved sequences | Supported | Rust persists the library and sends the selected sequence to the existing Rust scheduler; the Axum API supports list, create, update, delete, activate, and deactivate | Rust scene and sequence-library HTTP tests; scheduler timing tests |
| Stripe topology API | Python runtime | Rust implements `GET/PUT /api/stripes` and `POST /api/stripes/test`; validates layout, output uniqueness, backend resources, zones, and power settings, then waits for engine acknowledgement before persistence with rollback on failure | Rust mock-engine topology acknowledgement and persistence rollback tests; physical SPI/GPIO test patterns require target hardware |
| Color and microphone calibration | Python runtime | Rust color-calibration sessions apply test patterns and restore saved corrections on cancel/expiry; microphone calibration uses Rust audio telemetry and applies recommended profiles through acknowledged audio/scene updates | Rust calibration lifecycle and profile persistence tests; microphone capture was not tested against physical audio hardware |
| Audio tuning and synchronization | Python runtime | Rust exposes microphone device/profile selection, reset, source mode (`auto`, `demo`, `mic`, `off`), per-route sync offsets, and microphone calibration. The demo source feeds synthetic frames through the Rust audio worker and render scheduler. Bluetooth/Spotify management remains delegated; active output-tone sync calibration needs the later PipeWire output provider | Rust mock-engine audio/API tests; Bluetooth, Spotify, and tone-output calibration remain unavailable in the Rust application |
| Application identity | Python runtime | Rust reads and persists application and Spotify device names; it reports `spotify_restart_requested: false` because the external Spotify service remains delegated | Rust identity HTTP and settings persistence test |
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

## Production-readiness acceptance checklist

Keep the Rust engine opt-in until every hardware acceptance item below has
current, reproducible evidence. Automated coverage confirms software behavior;
it does not substitute for measurements on the target boards and devices.

### Preserved behavior and software gates

- [x] Python/native-C remains the default and rollback backend.
- [x] Rust remains explicitly selectable for rendering and audio. The Python
  application retains unported APIs and Bluetooth/Spotify service management;
  the standalone Axum application owns migrated state, settings, topology,
  playback, sequencer, and calibration workflows.
- [x] The workspace includes Python/C audio fixtures, Rust differential DSP
  tests, animation/effect goldens, bounded-queue tests, and process-level IPC
  tests.
- [x] Hosted x86_64 and ARM64 jobs build and test the Rust workspace.
- [x] `cargo-audit` reports no known vulnerabilities in the 201 locked Rust
  dependencies (advisory database updated 2026-11-02).
- [x] Local Python 3.12 full suite: 7,371 passed and 170 skipped, with Rust
  process differential tests enabled. The full Rust
  workspace tests pass (323 passed, 5 live-service tests ignored), and the
  focused Loom bounded-slot check also passes. The Rust scheduler startup test
  passed five consecutive focused runs with the more tolerant five-second
  deadline.
- [x] Frontend REST contract regeneration is clean; frontend lint, 180 tests,
  and production build pass. `uv build --all-packages` builds all four Python
  workspace distributions after retirement of the standalone Tk app.
- [ ] Rerun the hosted build/test matrix on this workflow revision. In the
  latest run on 2026-11-02, Rust x86_64/ARM64, Python 3.13/3.14, frontend, and
  all four distribution jobs passed. Python 3.12 failed in the Rust scheduler
  startup test before its first output frame; the local suite now passes with
  the longer startup deadline and an explicit transmitted-frame assertion.
- [ ] The Pi 5 workflow must pass end to end. Its latest run stopped at runner
  verification because `pkg-config` was absent. The workflow now installs
  packages into a temporary user-owned overlay when the persistent runner has
  neither `pkg-config` nor `sudo`; unit tests cover both the already-provisioned
  and no-root overlay paths. A Pi workflow run is still needed to validate the
  bootstrap and native build together.

### Audio capture acceptance

- [x] Software coverage exercises bounded capture queues, overflow accounting,
  discontinuity propagation, DSP reference fixtures, and the Rust-to-Python
  calibration contract.
- [x] CPAL's selected input sample rate is passed into the Rust DSP processor.
- [ ] On Pi 4 and Pi 5, verify CPAL capture from a microphone and USB input.
  Record host/device selector, actual sample rate, callback duration, callback
  jitter, queue drops, and at least 20 minutes of stable capture. The Pi 5 job
  can run a 20-second sample when `LUMISTRIPE_RUST_AUDIO_DEVICE` is configured.
- [ ] Verify Bluetooth and Spotify monitor routing through the existing source
  manager with Python and CPAL capture. Include disconnect/reconnect, recovery,
  and calibration measurements for each route.
- [ ] Compare Python and Rust calibration outputs against the same recorded
  source and record the accepted measurement tolerance.

### Performance acceptance

- [x] Synthetic x86_64 Python/native-C versus Rust DSP results are recorded in
  [`../performance/rust-audio-dsp-comparison.md`](../performance/rust-audio-dsp-comparison.md).
- [x] A current x86-64 virtual-output sample records runtime initialization,
  process CPU/RSS, supervisor and engine-output cadence, IPC/frame latency, and
  synthetic audio-feature-to-LED latency in the local runtime section below.
  Live capture, physical output, and production web-server startup remain
  separate validation items.
- [ ] Historical Pi 5 renderer-only evidence is available from [workflow run
  41421435690](https://github.com/woobble/LumiStripe/actions/runs/41421435690),
  source revision `767a41b71d58504677fcde39a85bf8f4510de801`. On a Pi 5 Model B
  Rev 1.0 (`aarch64`, Linux 7.0.0-1020-raspi, `ondemand`, 56.75 °C at capture),
  the artifact measured 54 animation cases at 300 LEDs on one output, with 200
  warmup and 1,000 measured frames per case. In the C-Neon profile, median
  per-case p95 render time was 7.65 µs for LLVM Rust and 8.15 µs for scalar
  Rust; the slowest case was Aurora at 42.76/42.82 µs respectively. Native C's
  slowest case was Twinkle at 139.15 µs, and Python's was BeatTunnel at
  8.04 ms. A separate 54-animation/19-effect-layer report recorded maximum
  Rust p95 of 52.09 µs for the Confetti effect layer and zero measured Rust
  allocations per frame. This is per-kernel renderer evidence from an older
  source revision; it does not include live CPAL capture, Python supervision,
  IPC, physical SPI/GPIO timing, or full audio-to-LED latency, and does not
  satisfy the current hardware matrix.
- [ ] Run the audio-to-LED matrix on Pi 4 and Pi 5 with real audio and
  representative LED counts. Retain source revision, selected Dynamic
  animations, p50/p95/p99 frame and capture-to-LED latency, synchronization
  jitter, missed/dropped frames, per-thread CPU, RSS/allocation data, CPU
  governor, temperature, and output configuration.
- [ ] Explain Dynamic FPS changes by separating Python policy, Rust render,
  IPC, audio, and physical output time. Profile before optimizing, then repeat
  the same matrix.
- [ ] Compare Rust against Python/native-C and scalar C on both Pi models.
  Synthetic x86_64 and hosted ARM64 results do not satisfy this gate.

### Physical output and failure recovery

- [x] Mock tests cover SPI/GPIO encoding, multi-output finalization, blackout,
  bounded retries, shutdown, owner death, and process crash recovery.
- [ ] On SPI and GPIO outputs, measure clock/data timing and inter-output skew
  with a logic analyzer at supported pixel counts. Verify blackout on startup,
  explicit blackout, graceful shutdown, and forced termination.
- [ ] Inject device-open, short-write, transient transfer, permanent transfer,
  worker panic, and process-kill failures. Confirm bounded recovery, safe output
  state, released device handles, and successful restart.
- [ ] Run overnight playback with topology/configuration changes and record
  memory, queue bounds, frame loss, and output health.

### Concurrency and safety review

- [x] Mutable playback and hardware state have explicit worker ownership;
  capture callbacks use a bounded SPSC queue and render updates use bounded or
  latest-wins queues.
- [x] A Loom model covers the bounded latest-frame slot.
- [x] Reviewed unsafe blocks in the Rust audio and output crates: GPIO MMIO
  pins and register layouts are validated before access, mappings and file
  descriptors have paired cleanup, and the MMIO writer is moved to one worker.
  Capture and benchmark syscalls use initialized result storage; the benchmark
  allocator delegates the allocation contract to `System`.
- [x] Reviewed IPC and queue limits: 1 MiB control requests, 128 KiB frame
  payloads, an eight-batch CPAL ring, bounded work queues, and one retry for
  transient hardware writes. Oversized control/frame requests are rejected
  without stopping the engine; queue replacement, invalid packets, retries,
  shutdown, and recovery have automated coverage.
- [x] Run available Miri-compatible tests. Nightly Miri passed all 15 tests in
  `lumistripe-engine-output`. A broader renderer run was stopped during its
  golden-frame test because it did not finish in practical local time, so
  renderer-crate Miri coverage remains unverified. Physical GPIO/SPI recovery
  checks remain separate from this software review.
- [ ] Run the overnight physical stress matrix and inspect memory, queue bounds,
  frame loss, device recovery, shutdown ordering, and long-duration counters.

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

### Local x86-64 runtime smoke sample

On 2026-11-04, a five-second simulated 300-pixel Dynamic run on an AMD Ryzen 9
9900X (Linux x86-64, Python 3.12.16) compared the Python engine with the Rust
engine using Python's synthetic demo-audio path. It used one mirrored virtual
output, no power cap, one second of warmup, and no physical GPIO/SPI device.
The runtime startup value is one sample per case, from runtime object
construction through `start()` and the initial Dynamic-mode acknowledgement; it
does not include Python interpreter or web-server startup.

| Engine | Runtime startup | Supervisor loop | Engine output | p95 output frame latency | p95 audio-feature-to-LED | CPU | RSS baseline → end |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Python | 8.5 ms | 50.0 FPS; 0 missed deadlines | — | — | — | 4.1% | Python 74.1 → 78.5 MiB |
| Rust | 23.7 ms | 20.0 FPS; 0 missed deadlines | 50.6 FPS; 0 drops | 0.250 ms | 42.7 ms | 3.0% combined | Python 78.7 → 79.1 MiB; engine 7.5 → 7.9 MiB |

For the Rust case, the 20 Hz supervisor loop and 50.6 Hz engine output are
separate cadences. The audio-feature-to-LED latency includes the synthetic
Python audio path and IPC; it does not represent live capture or native Rust
audio. Python RSS spans case initialization through the end of the case; Rust
engine RSS spans the measured interval. These short virtual-output measurements
are an x86 software sample, not a production web-server startup benchmark,
hardware timing result, or soak test.
Reproduce the run with:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run --locked python scripts/benchmark_runtime_pipeline.py \
  --led-counts 300 --output-counts 1 --layouts mirrored --zone-counts 1 \
  --modes dynamic --power-limits off --engine-backends python,rust \
  --audio-backends python --engine-binary rust/target/release/lumistripe-engine \
  --audio-source demo --warmup 1 --duration 5 \
  --output /tmp/lumistripe-runtime-x86.json
```

On the same x86_64 host, the release Rust CLI and engine were also run directly
with an empty `PATH`, 80 mock pixels, Dynamic mode, and the engine's synthetic
demo source for eight seconds. Across repeated runs, steady output ranged from
49.8 to 55.3 FPS; the engine reported no dropped or failed frames and no missed
scheduler deadlines. Frame-latency p95 ranged from 0.18 to 0.22 ms, and the
reported audio-feature-to-LED p95 ranged from 14 to 35 ms. A separate five-
second Static run held 60 FPS with no missed deadlines. Process RSS samples
during the Dynamic run were about 6.3 MiB for the CLI and 7.5 MiB for the
engine; `ps` reported approximately 0.0% and 0.6% lifetime-average CPU at the
sample points. These are short mock-output samples; synthetic audio is not a
capture-latency measurement, and process RSS samples are not peak-memory
measurements.

Reproduce the Python-free CLI runs with:

```bash
cargo build --release --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-cli -p lumistripe-engine
mkdir -p /tmp/lumistripe-empty-path
PATH=/tmp/lumistripe-empty-path rust/target/release/lumistripe \
  --engine-binary "$PWD/rust/target/release/lumistripe-engine" run \
  --backend mock --pixels 80 --mode dynamic --audio-source demo \
  --duration-seconds 8 --stats --stats-interval-ms 1000
PATH=/tmp/lumistripe-empty-path rust/target/release/lumistripe \
  --engine-binary "$PWD/rust/target/release/lumistripe-engine" run \
  --backend mock --pixels 80 --mode static --audio-source off \
  --duration-seconds 5 --stats --stats-interval-ms 1000
```

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

### Synthetic Rust CPU benchmarks on x86_64 and Pi 5 (2026-11-04)

The release audio and renderer benchmarks ran with Rust 1.88.0 on the current
migration worktree (`edd8304` plus uncommitted changes). The Rust source tree
fingerprint, computed from sorted per-file SHA-256 hashes while excluding
`target/`, was
`048ec17dd888c765118ce7e1cc18888381e7b92a9ca7ddd73f1fd37c31f6cd10` on both
machines.

The audio benchmark processed 4,096 calls per batch size over a deterministic
44.1 kHz synthetic two-tone signal. The renderer benchmark used `rainbow`, two
144-pixel outputs, 200 warm-up frames, 2,000 measured frames, and synthetic
audio features.

| Measurement | Raspberry Pi 5 aarch64 | x86_64 Ryzen 9 9900X |
| --- | ---: | ---: |
| 512-sample DSP hop p99 | 0.0423 ms | 0.0117 ms |
| 512-sample DSP plus structure tracking p99 | 0.0442 ms | 0.0129 ms |
| DSP plus structure real-time headroom | 294.5× | 1,061.6× |
| Audio benchmark process peak RSS | 74.9 MiB | 150.6 MiB |
| DSP steady-state allocations per call | 0 | 0 |
| Two-output renderer p50 / p95 / p99 | 19.2 / 19.3 / 21.6 µs | 6.0 / 6.1 / 7.9 µs |
| Renderer allocations per frame | 0 | 0 |

The Pi 5 used the `ondemand` CPU governor; observed frequency ranged from 2.2
to 2.4 GHz and temperature from 46.85 °C before the audio run to 57.30 °C
after the renderer run. The benchmark executables used the release profile.
The Pi and x86 measurements use the same source fingerprint and toolchain.

These are synthetic, software-only measurements. They do not measure live
capture, audio-to-LED latency, service scheduling, SPI/GPIO transfer timing,
frame drops under hardware output, or long-duration stability. Those checks
remain open; the earlier physical Pi matrix is separately reported above.

Reproduce with:

```bash
cargo +1.88.0 run --locked --release --manifest-path rust/Cargo.toml \
  -p lumistripe-engine-audio --bin benchmark_audio -- --iterations 4096
cargo +1.88.0 run --locked --release --manifest-path rust/Cargo.toml \
  -p lumistripe-engine-animation-state --example benchmark_render -- \
  --animation rainbow --pixels 144 --outputs 2 --frames 2000 --warmup 200 --audio
```

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
