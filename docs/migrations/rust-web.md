# Native Rust web migration

> Historical migration and validation record. This report captures an earlier
> stage of the Python-to-Rust transition; some commands and deployment details
> below describe retired tools. Keep those results as evidence for their
> recorded revisions, and use the current README, architecture, and simulator
> guides for supported workflows.

> **Current status:** Rust Axum is the only maintained web application, and
> Python application packages and production fallback paths have been retired.
> The Rust CLI and `--simulate` mode are the supported command-line and
> simulation interfaces. See the
> [Python retirement inventory](python-retirement-inventory.md) for the
> retained development-only Python tools.

## Goal 7 boundary

The Rust web application is an independent Axum server in
[`rust/apps/lumistripe-web`](../../rust/apps/lumistripe-web). It starts and
supervises a separate `lumistripe-engine` process, serves the built React app,
and exposes the first authenticated dashboard API slice. The Python FastAPI
application was the default deployment and complete feature fallback when this
migration began. It remains available for explicit rollback and differential
checks.
Only one server may own physical outputs at a time; use mock outputs when
running both servers for differential checks.

The web process uses Tokio for HTTP, WebSockets, IPC, command dispatch, engine
process supervision, cancellation, and graceful shutdown. The engine remains
the sole owner of SPI, GPIO, audio devices, rendering, and frame deadlines.
Audio capture callbacks, DSP, the LED scheduler, and physical writes stay on
the engine's dedicated threads with their existing bounded buffers and queues.
The asynchronous client in
[`lumistripe-engine-client`](../../rust/crates/lumistripe-engine-client) uses
protocol v3, one Unix socket per request, request IDs, a two-second deadline,
and a semaphore capped at eight in-flight operations. It does not retry an
ambiguous command. The web control actor accepts at most 64 queued commands,
commits state only after a matching scene revision acknowledgement, and
publishes immutable snapshots through latest-value watch channels.

If the engine exits, the supervisor reports the failure, restarts with a
250 ms to 5 s backoff, reapplies configuration and the newest desired scene,
then restores ready health. Shutdown sends the engine shutdown command and
waits for the owned child before joining web tasks. Each dashboard client
receives latest-value state, audio, or preview updates; a blocked socket send
times out after two seconds and closes that client without holding engine
work.

## Migrated behavior

| Surface | Rust implementation | Compatibility reference |
| --- | --- | --- |
| Health and dashboard state | `GET /api/health`, `GET /api/state` | FastAPI returns the same health status shape and the `DashboardState` fields in `frontend/src/lib/api/client.ts`. |
| Animation catalog and selection | `GET /api/animations`, `PUT /api/animation` | Names and metadata come from the generated Rust animation catalog; the catalog parity export still checks it against Python. |
| Playback controls | `PUT /api/mode`, `PUT /api/brightness`, `POST /api/blackout` | Commands target one stripe or all stripes, validate values, and return a dashboard snapshot after the engine acknowledges the scene revision. |
| Host and system operations | `GET /api/system/stats`, `GET /api/system/update`, `POST /api/system/update/check`, `POST /api/system/update/install` | The stats route returns host metrics plus nullable process RSS and thread count from a supervised five-second sampler. Disk usage follows Python `shutil.disk_usage` semantics, including reserved blocks in the used-space value. Rust checks the fixed upstream Git ref with bounded subprocesses, reads and writes the existing status file, and starts installation through the restricted sudo trigger. |
| Wi-Fi | `GET /api/network/status`, `POST /api/network/scan`, `POST /api/network/connect` | Calls the fixed root-owned NetworkManager helper with bounded JSON, serialized operations, credential-free process arguments, a 55-second timeout, and a 1 MiB output cap. |
| Bluetooth, Spotify, and audio sources | `/api/audio/bluetooth/*`, `/api/audio/spotify*`, `PUT /api/audio/source`, and dashboard state | Injected BlueZ/PipeWire providers and a local Soloist client expose status and controls. Source switching routes microphone, Bluetooth, and Spotify capture through the engine and restores the previous PipeWire source when leaving an external source. |
| Settings and control | `/api/audio/*`, `/api/stripes/*`, `/api/startup`, `/api/transitions/*`, `/api/sequencer/*`, and playback routes | Rust settings are validated and atomically persisted; hardware topology and scene changes wait for engine acknowledgements and use rollback paths. |
| Pairing | `GET /api/auth/status`, `POST /api/auth/pair`, `POST /api/auth/logout` | Four-digit code, opaque 30-day session, HttpOnly/SameSite Strict cookie, optional Secure cookie, five failures per 60-second window, logout revocation, and same-origin mutation checks. |
| State stream | `/ws/state` | Authenticated latest snapshot, sent on revision change at a 30 Hz maximum. |
| Audio stream | `/ws/audio` | Authenticated engine telemetry at 15 Hz using the existing strict Zod schema. |
| Preview stream | `/ws/preview` | Authenticated binary preview protocol v1, emitted when the engine frame sequence changes. |
| Frontend | Axum `ServeDir` and SPA fallback | Uses the existing built React output from the configured static directory or the package default. |

The preview encoder is checked against the shared v1 golden packet. Dashboard
and audio WebSocket tests compare the emitted JSON key sets to the strict
frontend schemas so added or missing fields are visible. REST controls accept
the same legacy ignored fields as their current Pydantic request models.
Injected-provider tests cover Bluetooth operation serialization, terminal
results, shutdown cancellation and gate release, PipeWire loopback retargeting,
Soloist reconnect and expired-command handling, playback-clock behavior,
bounded command queues, repeated Bluetooth connect/disconnect cycles, network
helper cancellation, timeouts and oversized output under pipe backpressure,
and updater guards, launch errors, output redaction, Git-check timeout, and
shutdown cancellation of an in-flight check. A mock-engine regression test
holds an injected BlueZ disconnect open while verifying that scheduled LED
frames continue rendering. A web integration test verifies that active Soloist
playback remains available across an engine child restart and that application
shutdown closes the provider connection and reaps the engine. An app-level
shutdown test starts a pending Bluetooth disconnect through HTTP and confirms
that shutdown cancels the provider operation.
These tests do not require BlueZ, PipeWire, Wi-Fi, or update services to be
running on the test host.

The Rust web app now also owns settings persistence, topology, audio
configuration and calibration, sequencing, transitions, startup preferences,
identity, cached system statistics, Wi-Fi routes, Spotify controls, Bluetooth
operations, PipeWire source routing, and system update routes. With its
`bluez` feature enabled on Linux, the Bluetooth service uses the `bluer` D-Bus
bindings for BlueZ status, pairing, and device operations. With its `pipewire`
feature enabled, it uses `pipewire-rs` to enumerate audio nodes and set default
routes through PipeWire metadata; PulseAudio compatibility operations such as
volume, mute, module management, and moving existing streams still use the
bounded `pactl` provider. Both providers remain injectable, Bluetooth calls
have deadlines, and route changes are serialized. BlueZ pairing registers a
temporary default agent for the requested device and restores the adapter's
previous pairable state after success, failure, or cancellation. The Spotify
client uses a bounded command queue and reconnecting local Soloist WebSocket.
Automatic audio source selection prefers active Spotify, then a Bluetooth
input, then the configured microphone on hardware. Rust deployments can set
`LUMI_BLUETOOTH_MAX_INPUTS` and `LUMI_BLUETOOTH_MAX_OUTPUTS`; both default to
one and the limits are reflected in Bluetooth capabilities and connection
admission. Rust `/api/state` and `/ws/state` snapshots also carry current typed
Spotify and Bluetooth status, refreshed independently of LED control revisions.

The `SpotifyProvider` boundary covers provider lifecycle, status snapshots, and
playback controls; the current `SoloistBackend` remains the production provider.
Each control waits for Soloist's `command_result` acknowledgement, while the
playback snapshot remains event-driven. Soloist defines that acknowledgement as
command acceptance, not confirmation that playback has changed. A rejected
command returns an error, and a missing acknowledgement times out as ambiguous
and reconnects before another command can be sent. This follows Spotify's
[Soloist WebSocket contract](https://developer.spotify.com/documentation/soloist/reference/websocket-api).
Future librespot PCM output should enter the engine audio worker, not the web
actor: CPAL currently writes fixed-size mono batches into the bounded
`CapturedAudioBatch` queue, and the engine worker feeds those batches to
`AudioProcessor::feed_samples_with_observer`. A future engine-side input adapter
can provide PCM at that boundary with the same sample format, monotonic capture
timestamps, bounded buffering, and discontinuity accounting. This keeps PCM
and DSP out of Tokio service tasks and avoids changing the Spotify control
provider contract. No librespot dependency or authentication implementation is
part of this migration.

On hardware, changing the Spotify device name invokes only the fixed
`/usr/bin/sudo -n /usr/local/sbin/lumistripe-spotify-device-name` helper. Rust
waits at most 30 seconds and persists the setting only after a successful
helper result; software mode and installations without the helper save the
setting without requesting a Soloist restart. The API response reports whether
the restricted restart was requested, while later GET responses report the
current settings without a restart request.
The deployment validates the downloaded Soloist `--version` build timestamp
before replacing an installed binary, and the systemd service checks that the
binary exists and is within Spotify's 90-day build lifetime before startup.
The diagnostic command does not inherit the Spotify API key; missing, invalid,
and expired builds produce an administrator-facing journal message. The
pre-start check runs `lumistripe-deploy-helper soloist-check` from the active,
commit-matched Rust release. Its child process receives a cleared environment,
bounded output, and a five-second deadline. The installer and updater build the
web server, engine, and deployment helper into the same release directory.
Automatic source selection retries a failed Spotify route after five seconds,
then keeps the configured priority of Spotify, Bluetooth input, and microphone.
Spotify activity changes trigger route reconciliation so a resumed stream uses
the current PipeWire default output.

Wi-Fi operations use the installed root-owned Rust NetworkManager helper. Its
`lumistripe-network` wrapper invokes `network` in a root-owned copy of the
commit-matched `lumistripe-deploy-helper`; the Rust web process uses only its
fixed stdin/stdout JSON interface. The Python `deploy/lumistripe_network.py`
remains as a differential reference and is no longer installed. The Rust
helper keeps NetworkManager access behind the fixed wrapper and sudoers rule,
validates SSIDs and security modes, writes connection profiles atomically with
mode `0600`, and bounds child output and runtime. The Rust update routes read
and atomically refresh the existing status file, run bounded Git checks
directly, and start installation through the fixed sudo trigger. The periodic
systemd updater and install lifecycle transitions use
the `update-state` subcommand in the commit-matched deployment helper. The
Python `deploy/update_state.py` remains as a differential reference. These
privileged helpers do not give the web process general root access. The Rust
web process can start and serve its APIs without Python. Soloist download and
extraction use the Rust deployment helper. New installations select the Rust
backend; explicitly configured Python deployments remain the rollback path.
The Docker development image also builds and runs Rust binaries.
Spotify device names are written to a service-user-owned EnvironmentFile by
the Rust deploy helper; sudoers permits only a nonblocking restart of the
Spotify service.
Rust release binaries are built on installation and update. Rust is the default
web backend for new installations. Existing Python configuration remains
selected until an administrator explicitly migrates it.

`frontend/openapi.json` and `src/generated/api.ts` are generated from the Rust
Axum routes and Utoipa schemas. The Rust document covers all 64 REST operations
and preserves the existing path set, property names, required fields, and
response references. Rust integration tests compare the checked-in document
to `ApiDoc`, probe every documented path with an unsupported method, exercise
documented GET/DELETE methods, and submit non-object JSON to request-body
methods to verify 422 validation without executing their mutations. They skip
no-body POST operations that can trigger scans, updates, or logout. The Python
exporter remains available as a rollback and differential reference. The
Python/Rust differential test compares success and unavailable HTTP statuses
for live system/provider GETs and validates payloads against shared Pydantic
response contracts. It compares unsupported update check/install responses,
identity updates and their printable-name conflict response, Bluetooth address
and alias validation errors, Wi-Fi interface/SSID/password contract errors,
dashboard snapshots and brightness-change WebSocket broadcasts after matching
audio-source configuration, state synchronization after reconnect, and fresh
non-zero demo-audio levels and bands against the shared deterministic fixture.
Audio telemetry schemas and stable route/configuration fields are also
compared. OpenAPI schema comparison recursively checks nested types, required
fields, array items, and enum values. Request bodies and parameters also compare
validation bounds and patterns after normalizing nullable-schema encoding and
redundant closed-bound annotations. Rust documents 409 conflicts on Bluetooth
and output mutations that FastAPI returns at runtime but omits from its OpenAPI
document; shared fixtures compare those runtime error responses. A local Soloist
WebSocket fixture compares connected playback metadata and status, then verifies
successful play/pause, skip, seek, volume, shuffle, and repeat commands through
both APIs, including Soloist `command_result` acknowledgements and matching
validation errors for invalid control values. The HTTP differential matrix
also submits malformed bodies across playback, startup, transitions, sequence
settings, topology, calibration, audio, network, and Bluetooth routes. Rust
preserves Pydantic's non-strict boolean coercions on startup, topology, and
audio sync-calibration requests; focused tests cover accepted and rejected JSON
boolean forms. Rust unit tests also verify provider rejection, an ambiguous
acknowledgement timeout, and that playback state remains event-driven. Preview
checks compare v1 framing and rendered pixel payloads after selecting a settled
solid color. Exhaustive business-error comparisons and parity for captured
microphone, Bluetooth, and Spotify audio streams remain cutover requirements.

The Rust workspace requires Rust 1.88. Cargo resolver 3 selects dependency
versions compatible with that declared minimum. The hosted x86_64 and ARM64 CI
jobs install and verify Rust 1.88, then test the workspace and native-feature
web package on both architectures. The trusted Raspberry Pi smoke job now
installs and verifies Rust 1.88, runs the Rust web suite with native BlueZ and
PipeWire features, and performs read-only live BlueZ and PipeWire snapshots
when those daemons are active. These snapshots verify service access and
discovery; physical pairing, audio routing changes, Wi-Fi, Soloist, and updater
recovery still need board-level validation.

Local x86_64 validation on 2026-11-03 passed the Rust 1.88 workspace tests,
the web tests with `bluez,pipewire` enabled, and the optimized feature-enabled
web build. The latest serial native-feature web run had 97 passing unit tests,
three passing integration tests, and two ignored live-daemon tests. The full
Python suite passed 7,396 tests with 169 skips in 96.20 seconds; the frontend
suite passed 176 tests and its production build. The Python/Rust API differential
test also passed against the release binaries. Local QEMU AArch64 tests passed
31 tests across six pure Rust engine crates. The web package also passed `cargo check` for
AArch64 with `bluez,pipewire` enabled, using the host pkg-config metadata; this
does not verify ARM64 linking. The local cross sysroot lacks ARM64 ALSA and
PipeWire development metadata, so a full AArch64 native-feature link/test could
not run here. The [hosted CI run on the current base commit](https://github.com/woobble/LumiStripe/actions/runs/41514145987)
passed the existing Rust Linux ARM64 job, but predates the feature-enabled
BlueZ/PipeWire steps in this worktree; those still need an execution result.
Its Python 3.12 job had one failure: the three-minute Rust adapter fixture could
generate beat timestamps before the boot-relative monotonic clock reached zero.
The fixture now offsets its synthetic timeline and all 36 tests in
`test_rust_engine_adapter.py` pass locally. The Raspberry Pi job stopped before
native compilation because the runner lacks `pkg-config` and `sudo`; the current
worktree adds a signed, job-local apt package overlay for that non-root case.
No Pi native tests have run against that provisioning change yet.
The feature-enabled Rust release web server and mock engine also started with
an empty `PATH` containing no `python3`. A direct smoke returned HTTP 200 for
health, system statistics, update status, Spotify status, and Bluetooth status.
The network route returned HTTP 503 because the local root-owned NetworkManager
helper was absent, although NetworkManager itself reported running. The Rust
update check accepted a request and recorded the bounded Git failure itself.
This confirms that Rust API startup and these status paths do not invoke the
Python application; Wi-Fi operations still require the documented privileged
helper.

On 2026-11-04, the current x86-64 release binaries were smoke-tested with
`lumistripe-web --simulate`, an isolated settings file, and mock outputs. The
server served its frontend and health endpoint; API requests selected Rainbow,
Dynamic mode, and demo audio, then applied an independent two-output topology
with 2/3 zones and a 15 W shared power budget. The preview WebSocket emitted a
binary frame, and a graceful restart restored the saved topology and power
settings with no output failures. This checked the HTTP/IPC simulation path and
frontend serving. Microphone capture and physical outputs remain separate
acceptance items.
The Rust `web_application` integration suite now repeats the two-output,
Dynamic/demo-audio, preview, and restart-persistence path with a real engine
child, with `python` and `python3` absent from the test process `PATH`. It also
asserts that persisted SPI settings remain mapped to mock outputs in simulation
mode.

The dashboard state also carries the engine's per-output Dynamic selector
decision, reason, top scores with contributing factors, timing, and recent
animation history. The Diagnostics page renders these values. The OpenAPI
field remains optional and the strict frontend parser defaults it to an empty
array so older Python rollback responses remain compatible.

The Rust native Bluetooth and PipeWire providers are opt-in build features.
The Raspberry Pi installer provisions Rust 1.88.0 and the
`libdbus-1-dev`/`libpipewire-0.3-dev` development headers for the non-root
service user. Build the web and engine binaries for the first Rust startup
with:

```bash
cd rust
cargo build --locked --release -p lumistripe-engine
cargo build --locked --release -p lumistripe-web --features bluez,pipewire
```

The hosted x86_64 and ARM64 Rust CI jobs are configured to compile and test the
web package with both native features enabled, including release builds. Those
checks cover compilation and injected-provider behavior; they do not replace
pairing, audio routing, or recovery checks on Raspberry Pi hardware. On
2026-11-03, the live PipeWire registry snapshot test passed on the local x86_64
host with a running user daemon. It found audio source and sink nodes, verified
bounded graph enumeration, and did not change routes. BlueZ integration could
not be verified on that x86_64 host because its system D-Bus service was
unavailable; Raspberry Pi validation is recorded below.

## Deployment behavior recorded during the migration

At the stage recorded here, new installations used commit-matched Rust
binaries while `LUMI_WEB_BACKEND=python` still selected the then-existing
rollback backend. Goal 12 retired that Python fallback. The current installer,
updater, and launcher reject the saved Python value with migration
instructions; see the [retirement inventory](python-retirement-inventory.md).

The updater at that stage read `LUMI_WEB_BACKEND` from the service environment
and rebuilt the web, engine, CLI, and helper with Rust 1.88.0 and the
`bluez,pipewire` features before restarting the web service. It built into a
staging target and atomically activated a commit-matched application release;
a failed build left the previous release active for rollback. The launcher
rejected a staged release when its source commit did not match the checkout.
The installer provisioned the Rust toolchain and native development headers,
then built the commit-matched web, engine, CLI, and deployment helper release.

## Migration checklist recorded before Python retirement

| Python area | Ownership at that stage | Planned Rust boundary and parity needs |
| --- | --- | --- |
| FastAPI route assembly and auth | `app.py`, `api/routes/*`, `api/dependencies.py`, and feature contracts under `contracts/` remain the rollback and differential reference. | Rust has feature-oriented Axum routes, typed contracts, pairing auth, origin checks, and static serving for all 64 REST operations. Rust OpenAPI documents the Bluetooth/output 409 conflicts returned at runtime but omitted from FastAPI's OpenAPI; differential fixtures compare those errors. Continue checking cookie policy and frontend strict schemas during cutover. |
| Runtime façade and worker | `runtime/facade.py` exposes `LumiStripeRuntime`; `runtime/worker.py` is the single writer for mutable playback, audio, topology, controller, and output state. `commands.py` defines queued operations; `state.py` defines snapshots and injected factories. | Move application policy into the Tokio command actor and immutable state snapshots. Preserve ordering, queue rejection, timeout behavior, fatal-worker recovery, and exactly one writer for each state element. Engine scene acknowledgements remain the boundary for rendered state. |
| Playback and rendering | `runtime/playback_service.py`, core `playback/`, `animation/`, `effects/`, `render/`, and `controller.py` cover mode policy, settings, animation selection, composition, correction, and Python/C fallback. Rust engine crates already own catalog rendering, effects, transitions, power, Cycling, and Dynamic scheduling. | Migrate saved animation settings, music-idle policy, transition configuration, complete per-output state, and any remaining unsupported scene behavior. Keep Python/C fallback until differential fixtures and hardware output agree. |
| Audio and calibration | Python `runtime/audio.py`, audio and calibration services, and core `audio/` remain the fallback. Rust has app-level audio settings, source routing and calibration, optional CPAL capture, DSP, beat/structure tracking, and typed engine status. | Preserve callback minimalism, bounded PCM handoff, profiles, sync offsets, calibration lifecycle, disconnect recovery, and source routing. Never run capture callbacks or DSP on Tokio executor threads. |
| Topology and persistence | Python topology/settings remain available in the fallback. Rust has validated settings persistence, topology, startup preferences, identity, sequences, audio profiles, and calibration persistence. | Keep versioned settings migrations, atomic writes, validation before replacing live outputs, and rollback on persistence or device failure. |
| Bluetooth and PipeWire | Python remains the complete fallback. Rust uses `bluer` for Linux BlueZ operations when built with `bluez`, and `pipewire-rs` for audio node enumeration and default route selection when built with `pipewire`. | Keep provider injection and partial service availability. BlueZ operations use bounded asynchronous D-Bus calls; PipeWire graph work runs on a dedicated thread with a bounded wait. `pactl` remains for Pulse compatibility controls and existing stream/module operations. Raspberry Pi checks must cover pairing, device roles, route selection, volume/mute, disconnect, and partial daemon availability. |
| Spotify Soloist | Python remains the complete fallback and the local Soloist service remains external. Rust has the local Soloist WebSocket client, bounded command queue, reconnect loop, immutable playback snapshot, position clock, and control acknowledgements. | Preserve one socket writer, bounded commands, reconnect policy, command deadlines, event validation, and track/status semantics. Do not couple Spotify transport to audio capture or output scheduling. |
| Networking and host services | Python remains the complete fallback. Rust has Wi-Fi routes, system statistics, bounded Git update checks, update status/install routes, and uses the root-owned Rust NetworkManager helper plus restricted sudo commands for updater and Spotify service restarts. Soloist archive extraction, periodic update-state checks, Spotify device-name updates, and Soloist pre-start validation use the version-matched Rust deployment helper. | Keep fixed helper paths, serialized bounded operations, typed responses, credential-free process arguments, and the privilege boundary. Validate networking and update recovery on the Pi; do not add broad privileged shell access. |
| WebSockets and preview | `runtime/preview.py`, `runtime/cadence.py`, and `api/routes/websocket.py` publish immutable state/audio snapshots and the versioned binary frame. | Keep latest-wins delivery, per-client failure isolation, auth refresh, cadence, binary fixture, and strict payload validation. Do not serialize full snapshots more often than the protocol needs. |
| Rust CLI | `rust/apps/lumistripe-cli` starts the Rust engine and supports Static, Cycling, and Dynamic playback over IPC, one or two mock/SPI/GPIO outputs, animation settings, brightness and power budgets. It also exposes health, capabilities, audio devices, microphone calibration/profiles, engine-backed JSONL audio diagnostics/recording/analysis, performance reports, selector diagnostics, and GPIO encoder controls. | Validate remaining Rust CLI hardware behavior on Pi. |
| Browser simulator mode | `lumistripe-web --simulate` runs the shared web application and Rust engine with mock outputs, loopback binding, and isolated settings. It serves the existing React dashboard and exercises the same web controls and engine path. Python-free integration coverage checks launch, mock playback, frontend serving, and clean shutdown. The obsolete Tkinter app's UI, feature list, or diagnostics do not need to be reproduced; no standalone simulator is planned. | Keep this portable mock-output mode available as a web-application smoke/demo path. Its acceptance follows the web app and engine requirements, not Tkinter feature parity. Real microphone capture/calibration is a separate hardware validation item. |
| Core package | `lumistripe-core` provides Python compatibility exports, animation/effect definitions, audio models and capture, playback policy, pixel buffers, render kernels, output controllers, power limits, and optional native C/GPIO/SPI extensions. | Continue replacing only bounded, well-tested primitives with workspace crates. Preserve optional native profiles, generated catalog ownership, portable defaults, typed Python boundary values during coexistence, and no FastAPI dependency in core crates. |
| Contracts and tests | Rust-generated `frontend/openapi.json`, generated `src/generated/api.ts`, strict Zod parsers, Python route/runtime tests, and preview fixtures define the compatibility surface. | The differential test recursively compares request constraints, nested response types, required fields, array items, and enums across all 64 operations, with separate mock-output processes for runtime parity. Keep FastAPI available for rollback; captured microphone, Bluetooth, and Spotify audio parity remains open. |

## Goal 9 status and remaining stages

1. **External services — implemented:** Rust has the Soloist client,
   feature-gated `bluer` and `pipewire-rs` providers, Wi-Fi helper adapter, and
   audio source integration. On a Raspberry Pi 5, the live BlueZ snapshot,
   live PipeWire registry snapshot, and app-level Bluetooth status request with
   the native providers all passed. The API test used mock LED output and
   confirmed that PipeWire exposed output devices. Provider injection keeps
   normal tests independent of host services.
2. **System operations — implemented:** Rust serves system statistics and
   update status/check/install routes. The web update check now runs bounded
   Git commands in Rust; the restricted privileged install trigger and
   periodic state checks now use the Rust deployment helper. Process RSS and
   thread count are included in the explicit system-stat contract extension.
3. **Parity and cutover evidence — in progress:** Rust integration checks
   confirm every FastAPI-documented API path is mounted without invoking
   service operations, and Rust generates the checked-in OpenAPI document for
   all 64 REST operations. The Python/Rust differential test covers live
   provider/system response contracts, update rejection paths, selected
   service mutations, dashboard state updates, audio telemetry schemas, and
   preview packet framing; a Soloist fixture now covers connected playback
   metadata, all playback controls, and matching invalid-value errors. The test
   also matches fresh demo audio levels and bands to the shared fixture.
   The process differential submits a malformed top-level JSON body to all 39
   REST operations that declare request bodies and confirms the exact 422
   validation response matches across both backends. The invalid-contract
   matrix also compares complete validation bodies, including error kinds,
   field paths, inputs, and contexts. Wi-Fi password validation responses are
   checked not to echo the credential. Successful behavior remains
   scenario-based for host-dependent providers, with injected-provider tests
   covering those paths.
   It also compares the animation catalog and reset path, pairing authentication
   across both processes, dashboard snapshots, audio-settings reads, and missing
   audio calibration-session errors. The pairing comparison checks invalid and
   valid codes, active cookie attributes, protected-route access, logout
   revocation, and the five-failure rate limit. An unauthenticated WebSocket
   closes with the same 4401 pairing code in both backends, and cross-origin
   WebSockets close with policy code 1008 in both. The comparison also exposed
   and fixed two Rust error-status differences: beginning a missing calibration
   session's music phase and finishing a missing session now match FastAPI's
   HTTP 409 responses.
   Python and Rust network API tests cover helper-backed scans, connections,
   and credential failures. The Axum test injects a temporary restricted
   helper and checks successful status, scan, and connect responses, failed
   password mapping, and omission of the password from the response. A shared
   Wi-Fi failure fixture also checks the same rejected password, HTTP 409, error
   text, and secret redaction through both APIs. A shared fixture drives the
   same ten Bluetooth adapter/device and audio
   output mutations through Python and Rust API tests. Both suites check the
   dispatched provider operation; Rust also waits for Bluetooth operations to
   reach a terminal state. Accepted Bluetooth mutations capture their
   `running` response before background work starts, so an immediate provider
   failure cannot race the initial API response. A shared failure fixture
   checks the same rejected pair request, running response, failed status, and
   provider error text in both backends. Another shared fixture covers
   rejected power, alias, discovery, connect, disconnect, and forget
   operations, checking matching running responses, terminal failures, and
   provider error text in both backends. Another shared fixture holds pairing
   in progress and checks that a concurrent power change returns the same HTTP
   409 conflict and error text in both backends without dispatching a second
   provider operation. A further
   shared fixture checks that a rejected PipeWire default-sink change returns
   the same HTTP 409 and provider error in both backends, with the previous
   default sink preserved. A second PipeWire failure fixture checks matching
   HTTP 409 errors for volume and mute while confirming that the selected sink,
   volume, and mute state remain unchanged. A shared updater fixture checks the
   unsupported, already-running, dirty-checkout, and no-update install guards
   across both APIs. A shared Wi-Fi helper-error fixture covers invalid,
   unsupported, unavailable, connection-failed, and unknown helper codes across
   both APIs, including password redaction, non-zero helper exit, and timeout.
   Python API tests cover a missing or unspawnable update checker and an
   unspawnable installer, including safe 503 responses. Rust service tests
   cover failed and timed-out Git checks plus failed and timed-out install
   trigger launches; its API error mapping returns the same safe 503 for an
   install launch failure. Unsupported update checks are also compared through
   both HTTP APIs. Rust performs Git checks asynchronously inside the service,
   so a Git launch or timeout failure is stored in `check_error` after the
   request is accepted; the Python rollback path can return 503 when its
   separate checker process cannot start. These checker-start responses differ
   by design. A shared API failure fixture now covers an unspawnable
   NetworkManager helper. Physical updater-launch and network-helper failures
   still need Raspberry Pi validation.
   PipeWire failure coverage forces Spotify loopback creation to fail during
   an output change and verifies restoration of the original default sink and
   monitor route.
   Automatic-source tests use mock engine IPC to verify Spotify-route failure
   falls back to Bluetooth, the Spotify retry window prevents an immediate
   repeated attempt, and unavailable Spotify/Bluetooth selects the microphone.
   Matching Python and Rust tests exercise a multi-step automatic handoff from
   Spotify to Bluetooth to microphone and back to Spotify. The Rust mock-engine
   test checks each device selector, route-specific DSP profile, PipeWire source
   restoration, and that an unchanged source does not restart capture.
   The capture batching/downmix path also tests interleaved stereo PCM across
   the fixed batch boundary, including sample positions and capture timestamps,
   then feeds those batches into the Rust DSP and verifies every sample arrives.
   The DSP is also checked against fixed Python/native-C PCM fixtures.
   Reconnecting the state WebSocket returns the latest snapshot from both
   backends. Python and Rust refresh tests now observe an out-of-band device
   rename/disconnect and preserve the last Bluetooth and PipeWire snapshot when
   BlueZ becomes temporarily unavailable. Physical partial-daemon recovery
   still needs Pi validation. Parity against recordings captured from
   microphone/Bluetooth/Spotify routes also remains.
   A test holds a
   Bluetooth disconnect pending while the real engine process continues
   rendering to mock outputs; another starts a disconnect through the app and
   confirms shutdown cancels the provider operation. Service-level
   cancellation and operation-gate release are also covered. Soloist playback
   has been verified across an engine restart and provider shutdown in the
   local web integration test. The trusted Pi workflow attempts read-only BlueZ
   and PipeWire snapshots; physical pairing and route changes, Wi-Fi
   connection changes, live Soloist playback, and update installation
   recovery remain. The hosted ARM64 workflow has not run in this session.
   Rust is the new-install default. The Python backend remains available for
   explicit rollback; hardware checks remain deferred in the retirement
   inventory.

## Validation and operational risks

### Raspberry Pi 5 provider smoke test (2026-11-03)

An isolated snapshot of this worktree was built on the Pi 5 with Rust 1.88.0
and the BlueZ/PipeWire native features. Formatting, the live BlueZ snapshot,
the live PipeWire registry snapshot, and the live-provider API test passed at
that earlier system state. The API test starts the Rust web app with the system
providers and mock LED output; PipeWire then exposed output devices.
The app-level test also passed on the Pi with a restricted `PATH` that exposed
Rust tools and `pactl` but no Python executable.
The three web integration tests also passed with that Python-free `PATH`,
covering Rust OpenAPI parity; authenticated stats, update, network, Spotify,
and Bluetooth routes; HTTP/WebSocket engine supervision; and Soloist fixture
playback across engine restart and application shutdown.
The first Pi snapshot had three audio sinks and no audio sources. A later
read-only probe reports one sink and one sink-monitor source, with no ALSA input
or Bluetooth source. Provider status and enumeration pass, but this does not
validate microphone capture, physical pairing or source routing, live Spotify
playback, Wi-Fi connection changes, or updater recovery.
At the time of these earlier probes, the restricted helper set was installed
for the unprivileged `www-data` identity. The staged source tree was under
`/opt/lumistripe`; the web service systemd unit was not installed. A separate
`woobb` user-session process was
listening on `127.0.0.1:18766` and returned a healthy `/api/health` response;
its mapped executable was the staging release binary, marked deleted after a
later build replaced that path. It was left running and was not managed-service
validation. The production NetworkManager wrapper and updater trigger were
root-owned, and the NetworkManager, updater, and Spotify-name sudoers files
pass `visudo` validation. `sudo -l -U www-data` lists only those three fixed
helper commands. An ignored Rust live API test ran as `www-data`, called
`GET /api/network/status` through the production sudo wrapper, and read the
initialized `GET /api/system/update` status. A second live route test ran
`POST /api/system/update/check` as `www-data`; it refreshed the shared status
file successfully and reported no available update. No update installation was
issued. A separate Rust live test scanned
`wlan0` through the restricted helper without printing or storing SSIDs; no
Wi-Fi connection was changed. At that point the update timer was disabled, so
live installation and recovery were not tested.
A second isolated launcher check called `GET /api/network/status` as
`www-data` with Python absent from the Rust process `PATH`; the restricted
helper returned `manager_available=true`. The response, including any SSID,
was not printed. That probe used the Python helper installed at the time; the
current installer installs the commit-matched Rust helper and removes the
legacy helper from the privileged directory.
The pre-install read-only probe confirmed that the Pi lacked
`lumistripe-spotify.service`, `/usr/local/bin/soloist`, and the Spotify
environment file; its PipeWire graph also has no `lumistripe_spotify` sink or
monitor. Live Spotify handoff remains untested. The Rust web process was
healthy at its loopback health endpoint, but no systemd unit supervised it at
that time.
The live API test needs `pulseaudio-utils` for its `pactl` compatibility
provider; the Pi CI preflight now checks and installs that package.
The Rust 1.88.0 web crate suite with both native features passed on the Pi:
97 passed and 9 ignored, with all 3 web integration tests passing. All nine
ignored live-service tests passed separately: native-provider API status,
installed NetworkManager REST boundary, NetworkManager Wi-Fi scan, updater
status check, 60-second BlueZ discovery, BlueZ D-Bus snapshot, NetworkManager
helper protocol, PipeWire registry snapshot, and a live system-statistics
sample. The statistics sample read the CPU thermal sensor, CPU counters,
memory, disk, uptime, and the test process's RSS and thread count. The Wi-Fi
scan ran as `www-data` through the restricted wrapper. The Bluetooth scan ran as `woobb`
with the adapter powered and idle, then verified the scan session released
without changing adapter power. The updater test only refreshed status and did
not request installation. The web integration suite also verified the checked-in
OpenAPI document against the Rust API description.
The Pi scan probes took 60.03 seconds for BlueZ's configured discovery window
and 3.95 seconds for a NetworkManager Wi-Fi rescan. These are single smoke
samples; no paired-device connect/pair, audio-route switch, or live Spotify
latency sample is available because those devices/services are not provisioned.
One initial service-user scan attempt received a transient
`manager_available=false` helper response; the installed REST status test and a
subsequent full scan passed, so helper status latency remains worth monitoring.
The trusted Pi workflow now runs the live system-statistics sample and those
two live scans only on explicit `workflow_dispatch`; routine trusted pushes
continue to use the read-only provider/status checks.
The full locked Rust workspace test suite also passed on the Pi, including the
three-minute audio pipeline and Python/native-C DSP differential test. Locked
release builds passed for the workspace and for the web app with native BlueZ
and PipeWire features; `file` confirmed both resulting executables are ARM
aarch64 binaries.
A fresh locked Rust 1.88.0 release build of `lumistripe-web` with `bluez` and
`pipewire` also passed after the Bluetooth accepted-operation snapshot fix;
`file` confirmed the executable is ARM aarch64. The existing user-session
process remained healthy and was not restarted.
The fresh binary also ran through `deploy/lumistripe-web-launcher.sh` as
`www-data` from an isolated staging tree, with mock LED output, separate
settings and temporary directories, and an empty `PATH` containing no Python
executable. `/api/health` returned `ok`, and after the sampler interval
`/api/system/stats` returned every metric as a populated value. The temporary
instance shut down cleanly; the existing user-session server was left
untouched.

After the installer refreshed `/opt/lumistripe-install`, the Pi rebuilt the
current `lumistripe-web` test target with Rust 1.88.0 and both native features.
Fresh read-only tests passed for the BlueZ adapter snapshot, PipeWire registry,
NetworkManager helper status, and system statistics; the 60-second BlueZ scan
also completed and released discovery. The live Rust API test passed with mock
LED output and the system BlueZ/PipeWire providers. The current PipeWire session
exposes only the `lumistripe_spotify` virtual sink and no audio source, so the
API correctly returns an empty selectable-output list. The smoke test checks
the response shape and selectors without requiring an unattached physical
speaker; real output-device enumeration and route changes remain unverified.
To verify non-empty enumeration separately, a temporary PipeWire `module-null-sink`
was loaded and the live provider API test was rerun with
`LUMISTRIPE_REQUIRE_PIPEWIRE_OUTPUT=1`; it passed. The module was unloaded
after the test, and no route-change operation was issued. This covers a
selectable virtual output only, not physical output or routing behavior.
The latest full non-ignored Rust web suite passed on this Pi: 97 unit tests and
3 integration tests, with 9 live-service tests excluded from that run. Rust
formatting also passed. Six live BlueZ, PipeWire, network-status, system-stats,
and native-provider API checks were run separately against the installed
services.

A fresh Rust 1.88.0 release build of the engine and a release build of the web
app with `bluez` and `pipewire` features passed on the Pi; both executables are
AArch64 ELF binaries.
The release web binary then ran in a temporary systemd user unit on
`127.0.0.1:18767` with the installed service identity, mock output, an isolated
settings path, and an empty `PATH`. Its health, system-stats, and update-status
GET routes all returned `200`. A second isolated run also returned `200` for
`GET /api/network/status` through the installed restricted helper; its response
body was discarded. The temporary units were stopped afterward, and the
production Python service remained active with its health route returning
`200`. This validates the release executable and these read-only routes without
Python on the process `PATH`; it does not validate physical output or switch
the installed service to Rust.

### Pi 5 installer deployment (2026-11-03)

The full installer ran from a root-owned checkout at `/opt/lumistripe-install`
for service user `woobb`. This Pi runs Ubuntu Resolute, while the documented
target is Raspberry Pi OS Trixie. After syncing the latest tracked workspace,
the updated installer completed again at 20:21 CET: required APT packages were
already present, Rust 1.88.0 was verified, Python 3.12 dependencies and native
audio/GPIO/SPI extensions built, and the frozen Bun frontend install and
production build passed. Bluetooth, Nginx, the web service, and the update-check
timer are active. No Spotify key was configured; the Spotify unit is installed
but disabled, and Soloist playback remains unavailable.

`lumistripe-web.service` is active on `127.0.0.1:8000` using the configured
Python rollback backend. HTTPS `/api/health` returned `200` with `{"status":"ok"}`
after the latest restart. The independent Rust smoke process also returned 200
on `127.0.0.1:18766`; the systemd unit has not been switched to Rust. No update
installation was requested. The pairing code is stored in
`/etc/lumistripe/lumistripe-web.env` with mode `600`; its value was not logged.

Nginx was already active before the initial installation, and its existing
worker kept the old configuration after `systemctl enable --now`; a manual
reload was needed then. The updated installer now reloads Nginx after enabling
or starting it, and the latest run passed Nginx configuration validation and
the HTTPS health check. The HTTPS system-statistics request returned `401`
without a paired session, so authenticated statistics through the installed
service were not checked. SPI was enabled; the Pi has not been rebooted, and
physical LED output, Bluetooth pairing/audio routing, Spotify handoff, Wi-Fi
connection changes, and update installation/recovery remain unverified.

On x86_64 with Rust 1.88.0, the feature-enabled workspace tests, live
PipeWire registry test, workspace release build, and native-provider web
release build passed. Ruff and mypy also passed.

The repository Python suite passed 7,396 tests with 169 skips. Frontend
validation passed: Rust OpenAPI plus TypeScript generation was reproducible,
176 Vitest tests passed, `bun run build` succeeded, and `bun run lint` exited
successfully with React warnings.

Rust integration tests launch the actual engine executable with mock outputs;
they cover pairing cookies, origin rejection, response validation, command
acknowledgements, all three WebSockets, the preview v1 packet, static SPA
fallback, child crash/restart with scene restoration, logout revocation, and
graceful shutdown. The Soloist fixture test also checks playback through an
engine restart and provider cleanup on application shutdown. Build the engine
binary before the web integration test with
`cargo build --package lumistripe-engine --bin lumistripe-engine`.
An Axum route-level test with injected providers verifies Bluetooth power,
alias, scan, pairing, connection, disconnection, forgetting, output selection,
output volume, and mute commands.

### Local mock performance sample

Measured on an x86_64 Linux host on 2026-11-02 with an 8-pixel mock output. The
Rust web app and engine used release builds; Python used the local Python 3.12
Uvicorn runtime with its in-memory simulation output. Servers ran separately.
The sample used 120 state GETs, 40 acknowledged brightness commands, 20
command-triggered state updates, 18 audio and preview WebSocket messages, then
eight concurrent audio clients for two seconds. Times are milliseconds unless
shown otherwise.

| Measurement | Rust | FastAPI |
| --- | ---: | ---: |
| `GET /api/state` p50 / p95 / p99 | 0.032 / 0.037 / 0.055 | 0.126 / 0.205 / 0.370 |
| Brightness command acknowledgement p50 / p95 / p99 | 5.019 / 5.146 / 5.199 | 1.765 / 2.221 / 12.744 |
| State change delivery p50 / p95 / p99 | 5.055 / 5.161 / 5.210 | 250.174 / 250.918 / 250.984 |
| Audio WebSocket p50 interval / p95 interval | 65.744 / 70.796 | 67.264 / 67.397 |
| Preview WebSocket p50 interval / p95 interval | 35.288 / 35.509 | 33.222 / 66.298 |
| Messages per audio client in the two-second sample | 32 each, 8 clients | 31 each, 8 clients |
| CPU during concurrent clients, percent of one core | 1.45% | 2.48% |
| RSS before / after concurrent clients | 16.49 / 17.74 MB, web plus engine | 79.03 / 79.47 MB |
| Linux thread count during the sample | 29, web plus engine | 30 |
| Observed command queue depth | 0 of 64 | Not exposed |

The Rust app owns six long-lived Tokio tasks at idle: the engine supervisor,
control actor, and four health/audio/preview publishers. Each connected
WebSocket adds a handler and one disconnect-reader task. This is a source-count
of application tasks; Tokio runtime internals are not exported as a metric.
The WebSocket timing above measures arrival intervals and command-to-state
delivery; the protocols do not carry a server-side send timestamp for one-way
latency. The command acknowledgement includes the Rust engine IPC round trip.

The engine crash/restart integration test recovered and reapplied its scene in
1,012 ms in this run. These short samples describe a development host and mock
outputs only; they do not establish Raspberry Pi, SPI, GPIO, Bluetooth, or live
audio performance.

The Soloist position ticker now uses a change-aware watch update, so a paused or
idle position does not wake dashboard subscribers every 250 ms. A Rust test
checks both the unchanged-position and advancing-position cases.

Frontend REST contract validation uses the Rust-generated OpenAPI and strict
Zod tests. Compare `openapi.json` and `src/generated/api.ts` after
`bun run generate:api`. Rust WebSocket integration tests assert exact root key
sets against the current frontend schemas. The shared preview fixture checks
binary layout, version, sequence encoding, and RGBA payloads.

Do not claim hardware readiness from mock or hosted tests. GPIO/SPI pin maps,
electrical waveforms, peripheral timing, process cleanup under board failure,
Bluetooth pairing and routing changes, PipeWire capture, and live microphone
latency still require trusted Raspberry Pi checks. Run Rust and Python servers
against independent mock outputs for differential tests; never bind both to a
physical strip.

### Current x86 runtime error differential (2026-11-05)

The Python/Rust process differential now compares duplicate and missing
animation/sequence resources, the saved-sequence cap, unknown and ineligible
Dynamic sequence animations, and semantic topology validation for zones, power
limits, supply capacity, duplicate stripe IDs, SPI devices, and GPIO pins. It
caught Rust returning a settings validation error for an invalid Dynamic
sequence and differences in topology validation details. Rust now validates
sequence animation eligibility before the settings operation and returns
Python-compatible 422 responses for the tested topology constraints. The
process differential passes all 3 tests. `cargo test --locked -p lumistripe-web`
passes 106 unit tests and 5 web integration tests; 5 live-service tests are
ignored because their host services are unavailable. The control differential
also compares unknown stripe IDs; stripe-scoped brightness, blackout,
animation, and music-idle errors across mirrored and independent layouts;
control changes during color calibration; stale calibration completion; and
missing or unknown music-idle animations. It caught additional status and
detail mismatches, which Rust now matches. All 3 process differential tests
and the 106-unit/5-integration web suite pass. Broader runtime and business-
error comparisons remain incomplete.

### Current Pi 5 ARM64 build and simulator check (2026-11-04)

A fresh snapshot of the current worktree was staged outside `/opt/lumistripe`
and built with the locked Rust 1.88.0 toolchain. Release builds passed for the
engine, web app with `bluez,pipewire`, deployment helper, and CLI; `file`
confirmed all four outputs are ARM aarch64 ELF binaries. The full Rust web
crate suite passed on the Pi: 103 unit tests and 4 integration tests passed;
9 live-service tests were ignored because their hardware or installed-service
prerequisites were unavailable. The integration target covers checked-in
OpenAPI, HTTP/authentication and WebSocket compatibility, engine supervision,
the simulator workflow, and Spotify fixture playback across restart and
shutdown.
The simulator test runs with Python and Python 3 absent from the test process
`PATH`, mock outputs, demo audio, two-output topology, and remembered settings
across restart.

The release web binary also ran directly in `--simulate` mode on
`127.0.0.1:18768` with the test-only `PATH` that cannot resolve Python. Its
`/api/health` response was `{"status":"ok"}` and `/api/state` returned `200`.
The temporary process was stopped after the requests.

The current Rust snapshot also passed the ignored live PipeWire registry
snapshot and BlueZ adapter snapshot tests, plus the live audio-status API test
with the system providers and mock LED output. The audio-status test verified
provider availability and response selectors without performing a route
change. The PipeWire session exposed `lumistripe_spotify.monitor` and HDMI
cards, but no physical microphone source; microphone capture and calibration
remain unverified.

The Rust CLI's Python-free process tests passed on the Pi (2 passed), covering
mock playback and Dynamic demo-audio diagnostics. The deployment-helper
Python-free process tests also passed (4 passed), covering network status,
Soloist preflight, Spotify device-name configuration, and update-state setup.

The temporary build and test did not modify or restart the installed service.
`lumistripe-web.service` remained active on its Python rollback backend and
`/api/health` returned `{"status":"ok"}`. This confirms an ARM64 build and
mock simulator workflow on this Pi 5; physical LED output, microphone capture
and calibration, Bluetooth audio routing, live Spotify playback, update activation
and rollback, and long-duration operation remain unverified. The Pi 4 is not
available in this validation environment.
