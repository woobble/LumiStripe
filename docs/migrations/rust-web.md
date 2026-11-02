# Native Rust web migration

## Goal 7 boundary

The Rust web application is an independent Axum server in
[`rust/apps/lumistripe-web`](../../rust/apps/lumistripe-web). It starts and
supervises a separate `lumistripe-engine` process, serves the built React app,
and exposes the first authenticated dashboard API slice. The Python FastAPI
application remains the default deployment and complete feature fallback.
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
| Pairing | `GET /api/auth/status`, `POST /api/auth/pair`, `POST /api/auth/logout` | Four-digit code, opaque 30-day session, HttpOnly/SameSite Strict cookie, optional Secure cookie, five failures per 60-second window, logout revocation, and same-origin mutation checks. |
| State stream | `/ws/state` | Authenticated latest snapshot, sent on revision change at a 30 Hz maximum. |
| Audio stream | `/ws/audio` | Authenticated engine telemetry at 15 Hz using the existing strict Zod schema. |
| Preview stream | `/ws/preview` | Authenticated binary preview protocol v1, emitted when the engine frame sequence changes. |
| Frontend | Axum `ServeDir` and SPA fallback | Uses the existing built React output from the configured static directory or the package default. |

The preview encoder is checked against the shared v1 golden packet. Dashboard
and audio WebSocket tests compare the emitted JSON key sets to the strict
frontend schemas so added or missing fields are visible. REST controls accept
the same legacy ignored fields as their current Pydantic request models.

The Rust server is intentionally a first slice, not a route-complete FastAPI
replacement. The existing frontend still calls unported APIs for settings,
system statistics, topology editing, audio configuration, calibration,
sequencing, transitions, Bluetooth, Wi-Fi, startup preferences, identity, and
system updates. Those calls return the Rust server's normal API 404 response
until their stages below land. Keep deployments on FastAPI when those features
are required.

`frontend/openapi.json` remains the generated FastAPI contract for the complete
application. Goal 7 adds no public Python route or Pydantic model, so
`bun run generate:api` must leave `openapi.json` and
`src/generated/api.ts` unchanged. The Rust integration test pins every
migrated HTTP method and its request/response schema references to the matching
FastAPI OpenAPI subset; live response tests also enforce the current strict
frontend payload schemas. A complete Rust OpenAPI document remains a
route-complete cutover requirement.

## Remaining Python responsibilities and Rust replacements

| Python area | Current ownership | Planned Rust boundary and parity needs |
| --- | --- | --- |
| FastAPI route assembly and auth | `app.py`, `api/routes/*`, `api/dependencies.py`, and feature contracts under `contracts/` | Add routes in feature modules with typed request/response contracts. Preserve paths, methods, body defaults, status codes, error details, CORS/origin checks, cookie policy, and frontend strict schemas. Do not change generated TypeScript by hand. |
| Runtime façade and worker | `runtime/facade.py` exposes `LumiStripeRuntime`; `runtime/worker.py` is the single writer for mutable playback, audio, topology, controller, and output state. `commands.py` defines queued operations; `state.py` defines snapshots and injected factories. | Move application policy into the Tokio command actor and immutable state snapshots. Preserve ordering, queue rejection, timeout behavior, fatal-worker recovery, and exactly one writer for each state element. Engine scene acknowledgements remain the boundary for rendered state. |
| Playback and rendering | `runtime/playback_service.py`, core `playback/`, `animation/`, `effects/`, `render/`, and `controller.py` cover mode policy, settings, animation selection, composition, correction, and Python/C fallback. Rust engine crates already own catalog rendering, effects, transitions, power, Cycling, and Dynamic scheduling. | Migrate saved animation settings, music-idle policy, transition configuration, complete per-output state, and any remaining unsupported scene behavior. Keep Python/C fallback until differential fixtures and hardware output agree. |
| Audio and calibration | `runtime/audio.py`, `audio_service.py`, `calibration_service.py`, `sync_calibration.py`, and core `audio/` own source selection, capture/DSP contracts, tuning, telemetry, device selection, latency measurement, and calibration persistence. Rust has optional CPAL capture, DSP, beat/structure tracking, and typed engine status. | Add app-level Rust audio settings and device/source contracts; preserve callback minimalism, bounded PCM handoff, profiles, sync offsets, calibration start/update/finish, disconnect recovery, and source routing. Never run capture callbacks or DSP on Tokio executor threads. |
| Topology and persistence | `runtime/topology.py` and `settings.py` validate up to two outputs, zones, GPIO/SPI uniqueness, power constraints, versioned settings, animation settings, startup state, identity, sequences, audio profiles, and calibration. Writes use validated snapshots and atomic replacement. | Add a typed settings service with versioned migration and atomic file writes, then expose topology and per-output settings routes. Validate configurations before replacing live outputs and retain rollback on persistence or device failure. |
| Bluetooth and PipeWire | `bluetooth.py`, `bluetooth_models.py`, `bluetooth_operations.py`, `bluetooth_polling.py`, `bluetooth_process.py`, and `bluetooth_providers.py` separate status models, BlueZ/PipeWire providers, process parsing, operation coordination, and polling. | Keep provider injection and the BlueZ/PipeWire/process boundaries. Use bounded Tokio process execution, cancellation, polling cadence, and operation state; preserve pairing, roles, volume/mute, output routing, and partial provider availability. |
| Spotify Soloist | `spotify.py` owns the local Soloist WebSocket, bounded command queue, reconnect loop, immutable playback snapshot, position clock, and control acknowledgements. | Add a dedicated async client/service with one socket writer, bounded commands, reconnect policy, command deadlines, event validation, and identical track/status semantics. Do not couple Spotify transport to audio capture or output scheduling. |
| Networking and host services | `networking.py` calls the installed root-owned NetworkManager helper; `system_stats.py` samples `/proc`, thermal zones, disk, and uptime. `system.py` also exposes update status and invokes narrow installer helpers. | Add explicit service interfaces. Keep privileged operations in installed helpers; pass structured input, enforce deadlines, hide secrets, and preserve unavailable/error status. Tokio must not perform broad privileged shell access. |
| WebSockets and preview | `runtime/preview.py`, `runtime/cadence.py`, and `api/routes/websocket.py` publish immutable state/audio snapshots and the versioned binary frame. | Keep latest-wins delivery, per-client failure isolation, auth refresh, cadence, binary fixture, and strict payload validation. Do not serialize full snapshots more often than the protocol needs. |
| CLI and simulator | `apps/lumistripe-cli` provides terminal playback and output encoding; `apps/lumistripe-sim` provides the Tkinter simulator. `lumistripe-app-support` shares UI-independent configuration policy. | Port only after core engine and settings contracts stabilize. Preserve portable mock output, headless CLI use, configuration precedence, and simulator interaction. Keep app-support policy independent from Axum and hardware details. |
| Core package | `lumistripe-core` provides Python compatibility exports, animation/effect definitions, audio models and capture, playback policy, pixel buffers, render kernels, output controllers, power limits, and optional native C/GPIO/SPI extensions. | Continue replacing only bounded, well-tested primitives with workspace crates. Preserve optional native profiles, generated catalog ownership, portable defaults, typed Python boundary values during coexistence, and no FastAPI dependency in core crates. |
| Contracts and tests | `frontend/openapi.json`, generated `src/generated/api.ts`, strict Zod parsers, Python route/runtime tests, and preview fixtures define the current compatibility surface. | Keep Python OpenAPI generation clean until Python route contracts change. Add Rust route contract tests and full Rust OpenAPI generation before route-complete cutover. Add differential tests against FastAPI using separate mock-output processes. |

## Migration stages after Goal 7

1. **Settings and topology:** typed versioned persistence; startup restore;
   stripe topology, zones, color correction, power limits, animation settings,
   transitions, and saved sequences. Replace the remaining per-output
   dashboard requests while preserving atomic writes and output rollback.
2. **Audio application policy:** device discovery and selection, tuning
   profiles, source selection, sync offsets, microphone calibration, and
   audio REST settings. Validate Rust telemetry with the frontend parser and
   live CPAL devices. Keep callback and DSP work on engine threads.
3. **External services:** Bluetooth/BlueZ and PipeWire providers, process
   status and operation state, Spotify Soloist client, and NetworkManager
   helper adapter. Preserve injection so tests do not require host services.
4. **System and application integrations:** system stats and updater status,
   identity, startup preferences, CLI, simulator, and remaining shared policy.
   Keep privileged deployment helpers outside the web process.
5. **Route-complete cutover:** generate the Rust OpenAPI document, compare it
   with FastAPI, regenerate TypeScript from the chosen canonical source, run
   differential endpoint/WebSocket tests, and retain the Python backend until
   hardware and deployment checks pass on x86_64 and Raspberry Pi ARM64.

## Validation and operational risks

Rust integration tests launch the actual engine executable with mock outputs;
they cover pairing cookies, origin rejection, response validation, command
acknowledgements, all three WebSockets, the preview v1 packet, static SPA
fallback, child crash/restart with scene restoration, logout revocation, and
graceful shutdown. The engine binary must be built before the web integration
test (`cargo build --package lumistripe-engine --bin lumistripe-engine`).

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

Frontend contract validation still uses the FastAPI-generated OpenAPI and
strict Zod tests. Compare those generated files after `bun run generate:api`;
any diff must correspond to an intentional Python REST contract change.
Rust WebSocket integration tests assert exact root key sets against the current
frontend schemas. The shared preview fixture checks binary layout, version,
sequence encoding, and RGBA payloads.

Do not claim hardware readiness from mock or hosted tests. GPIO/SPI pin maps,
electrical waveforms, peripheral timing, process cleanup under board failure,
Bluetooth routing, PipeWire capture, and live microphone latency still require
trusted Raspberry Pi checks. Run Rust and Python servers against independent
mock outputs for differential tests; never bind both to a physical strip.
