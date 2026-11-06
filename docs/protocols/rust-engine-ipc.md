# Rust engine IPC

The web application uses the in-process `EngineRuntime` and does not use this
protocol. The standalone `lumistripe-engine` executable exposes the protocol
for the Rust CLI and other local clients over a Unix-domain socket. JSON
control messages are newline-delimited. The same socket accepts a persistent
binary frame stream identified by its `LSF2` magic. This is local process IPC,
not a network service; the engine socket is created with mode `0600`.

The Rust types and runtime behavior are defined in
[`lumistripe-engine-protocol`](../../crates/lumistripe-engine-protocol/src/lib.rs)
and
[`lumistripe-engine-runtime`](../../crates/lumistripe-engine-runtime/src/lib.rs).
Shared health fixtures are under
[`tests/fixtures/`](../../tests/fixtures/); the versioned fixtures are consumed
by serialization and runtime tests.

## Control message envelope

Each control connection carries one request and one response. A request ID is
copied to its response. Requests must have a nonempty ID of at most 128 bytes
with no control characters. Control lines are limited to 1 MiB. Malformed
JSON, invalid IDs, oversized input, and incomplete requests close the
connection without changing engine state.

Versions 2, 3, and 4 use the typed `Request` and `Response` models. The server
currently accepts versions 3 and 4; version 2 is retained only as a historical
wire fixture. Commands use a tagged `type` and optional `data` object:

```json
{"protocol_version":4,"request_id":"health-1","command":{"type":"health"}}
{"protocol_version":4,"request_id":"configure-1","command":{"type":"configure","data":{"config":{"outputs":[{"id":"main","pixel_count":80,"reversed":false,"backend":{"type":"mock"}}]}}}}
```

Responses have the shape `{"protocol_version":4,"request_id":"health-1","result":{"type":"health",...}}`.
Successful result types include `health`, `configured`, `render_capabilities`,
`render_queued`, `scene_updated`, `audio_updated`, `audio_configured`,
`audio_stopped`, `audio_devices`, `audio_status`, and `shutdown`.

## Supported control versions

| Version | Status | Commands and compatibility |
| ---: | --- | --- |
| 1 | Legacy compatibility | Separate `LegacyRequest`/`LegacyResponse` models for health, one-output configuration, RGB frame rendering, and shutdown. Configuration accepts 1–16,384 pixels. Errors are `unsupported_version`, `invalid_config`, `invalid_frame`, or `not_configured`. |
| 2 | Retired | Historical typed `Request`/`Response` format. The current server rejects this version. |
| 3 | Supported previous | Health, render capabilities, configuration, direct rendering, scene updates, audio feature updates, Rust audio start/configure/stop, device enumeration, and status. |
| 4 | Current | Version 3 commands plus `audio_start_direct` for a runtime with a decoded-PCM provider. New clients should use version 4. |

Version 1 uses its own envelope and response result types. For example:

```json
{"protocol_version":1,"request_id":"legacy-1","command":{"type":"configure","data":{"config":{"pixel_count":80}}}}
{"protocol_version":1,"request_id":"legacy-1","result":{"type":"configured","pixel_count":80}}
```

For versions 3 and 4, audio lifecycle commands include `audio_start` with
`selector` and `config`, `audio_configure` with `config`, `audio_stop`,
`audio_devices`, and `audio_status`. Version 4 also supports
`audio_start_direct` with an optional `fallback_selector` and `config`. The
direct command is intended for an in-process runtime whose native Spotify
provider supplies decoded PCM; standalone IPC clients do not attach a PCM
producer. The built-in `demo` selector is supported by `audio_start`. Version 1
remains a distinct legacy wire model; it is not a reduced `Request`.

Control errors use `result.type = "error"` with a typed code and message.
Codes are `unsupported_version`, `invalid_config`, `invalid_frame`,
`invalid_render`, `audio_failure`, `not_configured`, `output_failure`, and
`shutting_down`. Version 1 has its own smaller error-code set above. The Rust
runtime validates configuration and render inputs before queueing work. A
render or scene response acknowledges queue acceptance; `replaced` reports
when a pending item replaced an older item. It does not mean output transmission
has completed; use health results for completed-frame telemetry.

## Binary frame transport

Frame transport is version 2 (`LSF2`) and is independent of JSON control
versions 1 through 4. Integers are little-endian. The header is 20 bytes:

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 4 | Magic `LSF2` |
| 4 | 2 | Frame version, `2` |
| 6 | 2 | Configured output count |
| 8 | 8 | Frame sequence |
| 16 | 4 | Payload byte count |
| 20 | variable | Concatenated RGBA output frames in configured order |

The payload must contain exactly four bytes per configured pixel. At most two
outputs of 16,384 pixels each are supported, for a maximum 128 KiB payload.
Packet reads have bounded deadlines. A fixed 24-byte acknowledgement uses
magic `LSA2`, frame version, status, sequence, and cumulative dropped-frame
count. Status values are `0` accepted, `1` not configured, `2` shutting down,
and `3` invalid frame. Accepted means the bounded latest-wins queue accepted
the frame; a later frame can replace a queued frame before output completes.

## Web authentication and error details

HTTP pairing, session-cookie, REST error, and WebSocket close-code behavior is
documented in [WebSocket and HTTP streams](websocket.md). The REST schema is
generated from the Rust Axum application; see the
[OpenAPI contract](../../apps/lumistripe-web/frontend/openapi.json).
