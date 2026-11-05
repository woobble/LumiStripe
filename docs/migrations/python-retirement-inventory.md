# Python retirement inventory

Snapshot: 2026-11-05. This inventory tracks Python responsibilities that must
be replaced, retained as migration evidence, or retired before a Rust-only
LumiStripe installation can be declared complete.

## Status definitions

1. **Replaced by Rust** — a Rust implementation exists. Python may still be
   kept as a rollback or parity reference until the relevant cutover gate passes.
2. **Still required in production** — the current installer, service, updater,
   or supported command invokes Python for normal operation.
3. **Development or differential only** — Python remains useful for tests,
   benchmarks, migration comparison, or development, but is not required by
   the intended Rust production runtime.
4. **Retired or obsolete** — the component is intentionally removed or no
   longer part of the supported product.

## Validation snapshot (2026-11-05)

The locked Python suite, after removing the retired Tk app, passed: 7,371
passed and 170 skipped in 239.08 seconds, with the Rust process differential
enabled. Ruff passed for the repository and
mypy passed for 176 Python source files.
The current x86_64 Rust 1.88 workspace suite passes 323 tests with 5
live-service tests ignored. The web package passes 106 tests with 5
live-service tests ignored, plus all 5 web process/integration tests. The
deployment, installer, systemd, and updater acceptance tests pass (70 tests).
The frontend contract generator is reproducible; frontend lint, 180 tests, and
the production build pass. `uv build --all-packages` builds all 4 remaining
Python workspace distributions.
The CLI's `test-engine` profile also passes 34 unit tests and 3
Python-unavailable process tests. Separately, the web package with
`bluez,pipewire` passes 105 tests with 9 live-service tests ignored.
One web integration keeps two authenticated state-WebSocket clients connected
through a brightness update and verifies both receive the same revision.
The Python/Rust API process differential passes against binaries rebuilt with
Rust 1.88 and the `bluez,pipewire` web features, including animation list/reset,
pairing success/failure, session-cookie attributes, protected-route access,
logout revocation, rate limiting, and matching 4401 close semantics for
unauthenticated WebSockets plus 1008 policy closes for invalid origins;
dashboard and audio-settings reads, and audio-calibration missing-session
errors. Every one of the 39 REST
operations with a request body is also sent a
malformed top-level JSON array and both processes must reject it with HTTP 422.
The differential caught and drove fixes for two HTTP 404-versus-409 mismatches.
The Rust release workspace and native-provider web
release builds pass.
The default-feature Rust web library suite passed 104 tests with 5 ignored,
and the Python/Rust process differential passed 3 tests against binaries
rebuilt from this worktree. On 2026-11-05, the full Rust workspace suite passed
again; the `bluez,pipewire` web suite passed 107 tests with 9 live-service tests
ignored, plus all 5 web integration tests. This run caught a race in the
NetworkManager helper-cancellation test, which could read the helper PID file
before its contents were written. The test now waits for a valid PID, and the
focused test and full native-feature suite pass. The current Python/Rust process
differential passed all 3 tests against the workspace-built Rust web binary.
The differential now also exercises duplicate and missing animation/sequence
resources, the saved-sequence limit, unknown and ineligible Dynamic sequence
animations, topology zone and power validation, duplicate stripe IDs, duplicate
SPI devices, and GPIO pin collisions. It caught a Rust response that surfaced
an invalid sequence as a settings validation error instead of the Python 422,
and differences in topology validation details and collision errors. Rust now
rejects invalid sequence entries at the request boundary and matches the
Python response semantics for those cases. The refreshed process differential
passes all 3 tests; `cargo test --locked -p lumistripe-web` passes 106 unit and
5 integration tests with 5 live-service tests ignored.
The control differential now also compares unknown stripe IDs, stripe-scoped
brightness, blackout, animation, and music-idle errors across mirrored and
independent layouts, playback changes during color calibration, stale
calibration completion, and missing or unknown music-idle animations. It
caught additional Rust/Python status and detail mismatches; Rust now matches
the Python responses for these cases. All 3 process differential tests pass,
and the web package still passes 106 unit tests and 5 integration tests with
5 live-service tests ignored.
The current x86_64 Python-free CLI process suite passed all 3 tests, and the
deployment-helper process suite passed all 4 tests; both launch native
executables with `PATH` excluding Python.
The Rust 1.88 release workspace build and the `bluez,pipewire` release build
for `lumistripe-web` both pass. The 4 Python workspace distributions build,
and inspection confirms the default-profile core wheel has no native
extensions while its source distribution contains the required KISS FFT
sources. The process differential passes against both debug and release Rust
web binaries. OpenAPI and generated TypeScript regeneration leave their files
byte-identical.
The installer, updater/rollback, Rust helper, and Pi-CI provisioning test group
passed 67 tests. Three systemd service checks passed, including offline
`systemd-analyze verify` for rendered web, Spotify, updater, and timer units.
An earlier Pi 5 source snapshot built the engine, web, deployment helper, and
CLI release binaries. Its Python-free helper tests and four web application
integration tests passed. A separate web-library test invocation failed three
tests because that command did not build or set
`LUMISTRIPE_ENGINE_TEST_BINARY`; the CI workflow builds the debug engine before
running that package test. Do not count that invocation as a passing Pi web
library suite or as evidence for the current dirty worktree.
For the current CLI parser change, its `main.rs` was copied into that Pi
validation checkout and the ARM64 CLI tests passed: 31 unit tests and 2
Python-unavailable process tests. Those process tests use mock output; this
validates the CLI change only and does not cover physical devices or the full
current worktree.
Read-only PipeWire and BlueZ snapshots and the provider-backed audio-status API
test passed on the Pi with mock LED output. The only listed PipeWire source was
a Spotify monitor; no microphone device was available. Software-only
Rust DSP and two-output renderer measurements for the Pi 5 and x86_64 are
recorded in `docs/migrations/rust-engine.md`; physical I/O is not covered.
The Raspberry Pi CI dependency provisioner passed tests for both its already
provisioned and no-root overlay paths; on the Pi 5 it detected the installed
native packages and exited without changing system packages.

## User-directed hardware validation deferrals (2026-11-04)

Keep production cutover gated on these unverified checks:

- Real microphone capture and calibration. The Pi's only listed audio source
  was a Spotify monitor, not a microphone.
- Bluetooth pairing and playback, PipeWire routing and recovery, and live
  Spotify playback.
- Physical LED waveform and blackout behavior, plus long-duration recovery.
- Raspberry Pi 4 validation; no Pi 4 is available.

A previously supplied Pi 5 matrix reported physical SPI output but has no
source or thermal provenance. Do not treat it as verified physical-output or
performance evidence.

## Application and library inventory

| Component | Current responsibility | Classification and evidence | Retirement gate |
| --- | --- | --- | --- |
| `apps/lumistripe-web/src/lumistripe_web/` | FastAPI REST and WebSocket API, auth, settings, runtime worker, Bluetooth, Wi-Fi, Spotify, stats, and update routes | **1 + 3.** The Rust Axum app in `rust/apps/lumistripe-web/` implements the application API and is the OpenAPI source. The Python app remains the rollback and differential reference. The cross-process differential suite compares OpenAPI across all 64 operations, exercises invalid inputs for every documented mutation with a request body (with identity validation also checked through its custom 409 response), and covers Spotify controls, dashboard/audio streams, and preview frames. It checks Pydantic boolean coercions for startup, topology, and audio sync-calibration requests. Captured real microphone, Bluetooth, and Spotify audio parity, and exhaustive behavior/error coverage remain open. | Complete API/runtime differential coverage, production cutover, and a tested rollback window. |
| `apps/lumistripe-web/scripts/export_openapi.py` | Exports the legacy FastAPI OpenAPI document | **3.** Rust's `export-openapi` binary is canonical for generated frontend contracts. Keep the Python exporter while rollback/differential checks depend on it. | Retire with the FastAPI implementation after all consumers are removed. |
| `apps/lumistripe-cli/` | `lumistripe` command, playback policy, hardware and mock outputs, encoder inputs, audio selection/configuration/calibration/diagnostics/recording, and performance reporting | **1 + 2.** `rust/apps/lumistripe-cli/` starts the existing engine over versioned IPC and supports Static, Cycling, and Dynamic modes through the shared renderer; SPI remains the default, with mock/mock-SPI/GPIO options and one or two outputs. It maps legacy cycle order/timing, interval, mic target/noise tuning, activity gate, idle brightness, and Dynamic selector flags into engine scene configuration; supports animation settings, brightness, power budgets, microphone/demo/off source selection, device listing, one-shot/automatic calibration, device-matched profiles with JSON load/write, bounded engine-backed JSONL diagnostics/recording/analysis, performance reports, and GPIO rotary encoder controls. Legacy secondary-output flags mirror the primary pixel count unless `--pixels-2` is set, and mic profiles can be exported without opening a microphone. Dynamic selector reason, switch intent, top scores with contributing factors, timing, and recent history are exposed through engine health and CLI diagnostics. Python's top-level analyze/list/calibrate/profile/debug actions and `--spi-speed-2` are retained; parser tests cover the aliases and action precedence. Python-free playback and diagnostic process tests pass. The Python `lumistripe` command remains the installed production entry point until Rust deployment and hardware cutover gates pass. | Validate GPIO input, microphone, and output parity on Raspberry Pi; then switch the installed command after cutover validation. |
| `apps/lumistripe-sim/` | Retired standalone Tk simulator package and command | **4.** Removed from the Python workspace, distribution, and test configuration by product direction. The only supported simulator is `lumistripe-web --simulate`, which uses the Rust engine, React dashboard, mock outputs, and isolated settings. Python-free integration and process tests cover simulator launch, core playback controls, demo audio, preview, frontend serving, and shutdown. | No standalone simulator or Tk UI parity work is planned. |
| `packages/lumistripe-core/src/lumistripe/` | Python animation/effect rendering, playback/selection, audio capture and DSP, calibration, topology/output, GPIO/SPI, and power limiting | **1 + 3; currently 2 for Python deployments.** Rust crates under `rust/crates/lumistripe-engine-*` implement the real-time engine, audio pipeline, outputs, and rendering. Python remains required by the current Python CLI and default web backend, and remains the differential oracle. | Replace remaining CLI and deployment dependencies; finish scene/audio/hardware parity and golden comparisons; remove Python deployment rollback. |
| `packages/lumistripe-app-support/` | Python configuration builders and policy shared by Python CLI and web runtime | **3; currently 2 for Python deployments.** Equivalent Rust configuration types and policy live in the engine/config and Rust web crates, but the Python applications still import this package. | Remove all production Python application consumers and preserve settings migration fixtures. |
| `packages/lumistripe-core/setup.py`, native C sources and `.pyi` declarations | Build/load Python audio, GPIO, SPI, and renderer extensions | **3; currently 2 for Python deployments.** Rust audio, GPIO/SPI output, and rendering crates cover parts of this surface; the Python build still explicitly compiles these extensions. | Prove replacement on x86_64 and Pi ARM64, then remove Python wheel/build requirements with the Python apps. |
| `packages/lumistripe-core/benchmarks/*.py` | Python-side render, DSP, and SPI benchmark/reference harnesses | **3.** These are not invoked by production services. Some compare Python/native C against Rust. | Keep only benchmarks still used for migration or performance evidence; remove obsolete comparisons after results are archived. |
| Python test suites under `apps/`, `packages/`, and `deploy/tests/` | Legacy behavior tests, differential references, and deployment helper tests | **3.** They are development and migration tooling, not a runtime dependency. | Retain differential/security fixtures until Rust equivalents establish parity; then remove tests that only exercise retired code. |
| Root `conftest.py` | Pytest configuration and shared fixtures | **3.** Test-only. | Retire with the Python test suites after migration evidence is preserved. |

## Deployment and helper inventory

| Component | Production caller / responsibility | Classification | Replacement and remaining work |
| --- | --- | --- | --- |
| `deploy/lumistripe_network.py` | Differential reference for the NetworkManager JSON protocol | **3.** The production wrapper now runs the `network` subcommand from a root-owned copy of the commit-matched `lumistripe-deploy-helper`; the Python script is no longer installed. Rust tests cover request parsing, scan parsing, SSID/security validation, credential-free `nmcli` arguments, private keyfile writes, failed connection cleanup, and a process-level status request with Python absent from `PATH`. | Keep the reference and differential tests through Raspberry Pi network validation, then remove it with the Python rollback backend. Do not move NetworkManager authority into the web process. |
| `deploy/spotify_device_name.py` | Legacy reference for writing the Soloist device-name override and requesting a service restart | **3.** The production web apps call `spotify-device-name` in `lumistripe-deploy-helper` as the service user, then use a sudoers rule that permits only restarting `lumistripe-spotify.service`. The override remains a non-secret EnvironmentFile under the service user's home. A process-level test writes it with Python absent from `PATH`. | Retain as a migration reference; remove after Python rollback support and parity checks are retired. |
| `deploy/soloist_diagnostics.py` | Legacy Soloist binary validator retained as a Python reference and test target | **3.** The Spotify service and Soloist installer now use `lumistripe-deploy-helper` from the commit-matched Rust release. The Python implementation is no longer installed by `update-support.sh`. A process-level preflight test validates a local executable fixture with Python absent from the helper's `PATH`. | Retain differential/security tests while deployment cutover is validated; remove the Python implementation only after the Rust validator has passed deployment validation. |
| `deploy/update_state.py` | Python reference for update status JSON, source revision checks, and install lifecycle transitions | **3.** Periodic checks, updater transitions, and initial status creation use the `update-state` subcommand in the commit-matched `lumistripe-deploy-helper`. Rust tests cover status shape, atomic writes, ownership, symlinks, locks, stale install recovery, and offline checks; a process-level test initializes status with Python absent from `PATH`. | Retain for differential testing until deployment parity is confirmed; then remove the Python reference and its tests. |
| `deploy/extract_soloist.py` | Python differential reference for bounded Soloist downloads and archive extraction; the Docker development image still uses it | **3.** The Raspberry Pi installer and updater now call `soloist-download` in `lumistripe-deploy-helper`, which enforces the official HTTPS origin, redirect rules, download and decompression limits, and archive member checks. | Retain the Python reference tests while validating Rust downloads and extraction against official archives; then migrate the Docker development image before removing the reference. |
| `deploy/install.sh` | Installs Python, `uv`, Python wheels/native extensions or the Rust-only web/engine/CLI/helper release, plus frontend, services, TLS, and hardware packages | **2.** Python remains the default until Pi cutover gates pass. `LUMI_WEB_BACKEND=rust` skips Python system packages, `uv`, application packages, and native extensions. A process test executes the installer package/dependency functions with Python absent from `PATH`, verifies the package list, and confirms the frontend still uses Bun. | Make Rust the default only after gates pass; preserve identities and settings while validating Rust-only installation and reboot on Pi. |
| `deploy/update.sh` and `deploy/update-support.sh` | Update, build, validate, activate, and roll back application releases; persist update state | **2.** The updater selects dependencies from the configured backend: Python installs/synchronizes Python for the current rollback backend; Rust-only updates skip `uv` and Python, and build/activate/roll back commit-matched web, engine, CLI, and deployment-helper releases. The Python-free process test also exercises the Rust update dependency skip. Soloist downloading and extraction use Rust. Shell integration tests exercise checkout of the prior revision, release rebuild steps, service restart, optional Spotify restart, and failed rollback reporting. The Rust rollback harness executes the updater's actual sync and build functions with Python absent, verifies the version-matched builder invocation and the bounded systemd/API health check, and stubs privileged service and build subprocesses. | Test Rust-only update and rollback on Pi, including startup failure recovery, before removing the Python deployment backend. Keep privileged operations constrained. |
| `deploy/lumistripe-web-launcher.sh` and `deploy/lumistripe-web.service` | Select Python or Rust server backend and establish service environment | **2.** The service currently defaults to Python and carries Python environment settings. A launcher integration test selects a commit-matched web/engine fixture with Python absent from `PATH` and verifies both binaries come from the same release. A separate process test confirms that an unset backend still selects the Python rollback path. | Point the production unit directly at a version-matched Rust release after cutover gates; preserve a deliberate administrator rollback procedure during migration. |
| `deploy/lumistripe-update-check.service` | Periodic update check | **1.** Its `ExecStart` uses `update-state check` from the active commit-matched Rust deployment helper, with the existing service identity, state path, timeout, and sandboxing. The Python FastAPI rollback backend also starts this Rust helper for manual checks. | Validate the unit and web API check path on a deployed Pi, then retire the Python reference after differential coverage. |
| `deploy/lumistripe-spotify.service` | Optional Spotify Soloist service preflight | **1.** Its `ExecStartPre` invokes `lumistripe-deploy-helper soloist-check` from the active commit-matched Rust release. The checker clears the child environment. | Validate the installed systemd unit and Soloist install/update path on x86_64 and Pi, then retire the Python reference after differential coverage. |
| `deploy/lumistripe-network` and `deploy/lumistripe-network.sudoers` | Fixed root-owned NetworkManager command entry point | **1.** The wrapper invokes the root-owned Rust helper with the fixed `network` command; requests remain bounded JSON on standard input and output. Spotify naming uses an unprivileged Rust file writer and an exact systemctl sudoers command. | Validate NetworkManager scan and connect behavior on the Pi while retaining the narrow privilege boundary. |
| `deploy/docker/Dockerfile`, `deploy/docker-compose.dev.yml`, and `deploy/docker/spotify-entrypoint.sh` | Local Docker development with Python web runtime and Soloist setup | **3.** This is a development profile and currently builds a Python image. | Decide whether Docker remains a supported Rust development profile; it must not be mistaken for evidence that the Pi install is Python-free. |

## Build, configuration, and data tooling

| Component | Classification | Notes |
| --- | --- | --- |
| Root and package `pyproject.toml`, `uv.lock`, Python workspace package metadata | **3; currently 2 for Python releases.** | Required by the existing Python application/test/build workflows. Retire only after Python apps and Python packaging are intentionally removed; keep the Rust toolchain and Bun versions pinned. |
| `apps/lumistripe-web/frontend/scripts/*` and Rust `export-openapi` | **1.** | Frontend contract generation is Rust OpenAPI → `frontend/openapi.json` → generated TypeScript. Do not restore Python as the contract source. |
| `scripts/benchmark_*.py` and `scripts/validate_music_sequencer.py` | **3.** | Development benchmark and validation drivers; several launch Rust binaries or compare Rust output with Python references. They are not part of installed services. |
| `scripts/export_rust_*_golden.py`, `scripts/generate_audio_reference.py`, and `scripts/generate_audio_sync_track.py` | **3.** | Generate migration fixtures and deterministic audio/animation/layout/power/transition reference data. Preserve checked-in fixtures; replace the Python generators only if maintaining them still requires Python after Python retirement. |
| Python fixtures, golden JSON, and Rust-vs-Python differential tests | **3.** | Golden outputs are migration evidence, not runtime dependencies. Preserve useful fixtures after Python implementations are retired. |
| `deploy/rust-toolchain.sh` and `deploy/build-rust-backend.sh` | **1.** | The installer and updater now build the Rust web, engine, CLI, and deployment helper binaries as one commit-matched release. Python dependencies and the Python default web backend remain during migration. |
| Shell scripts, systemd units, sudoers, Docker files, and CI workflows | **2 or 3 according to caller above.** | The remaining Python invocations are enumerated by the production callers above; development/test invocations may continue to use `uv` until Python retirement is complete. |

## Cutover blockers

The current repository does not meet the Rust-only production target:

1. The Rust CLI now drives engine-owned static, Cycling, and Dynamic playback and covers audio calibration/profiles, JSONL diagnostics, selector scores/reasons, live performance reports, and GPIO encoder controls. GPIO, microphone, and output parity still needs Raspberry Pi validation. The standalone Tk simulator is retired; no UI or diagnostics parity with it is required. The only simulator is `lumistripe-web --simulate`. Its Python-free x86 integration workflows cover demo audio, all three playback modes, remembered mode/animation/brightness after restart, two outputs with zones and power limiting, preview streaming, and the actual web process path, including frontend serving and graceful shutdown. Real microphone capture and calibration remain hardware-deferred.
2. The installer supports a Rust-only profile, but Python remains the default
   backend until hardware cutover gates pass. Existing Python deployments still
   use `uv`, Python packages, and native Python extensions.
3. Production network, Spotify-name, update-state, Soloist-preflight, and
   Soloist-download operations use Rust helpers. The updater skips Python for
   Rust-only deployments; Python deployments retain synchronization for
   rollback. The Docker development image still uses the Python Soloist
   extractor.
4. Successful Rust/Python runtime comparisons remain scenario-based even though
   every OpenAPI operation, request-body validation path, and strict schema is
   checked. Real Pi hardware, service recovery, Bluetooth, Wi-Fi, and Spotify
   validation remains required.
5. Apart from the retired standalone simulator, Python still supports the
   installed CLI, default rollback backend, tests, and migration references.
   Those components remain until their respective parity and cutover evidence
   passes.

This inventory is a current-state audit, not a declaration that the required
cutover gates have passed. Update it as replacements land and cite the tests,
runtime checks, or hardware evidence that justify each status transition.
