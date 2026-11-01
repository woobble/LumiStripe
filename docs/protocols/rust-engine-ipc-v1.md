# Rust engine local IPC, version 1

This page documents the original single-output protocol, which remains
available as a compatibility path for existing clients. The current runtime
also supports [version 2](rust-engine-ipc-v2.md), which adds multi-output
ownership and compact binary frame sets. Version 1 requests use the same
single-output simulated backend as the original prototype; hardware and
multi-output clients must use version 2.

The engine uses a Unix-domain stream socket with one UTF-8 JSON message per
connection. Each request and response is one JSON object followed by `\n`.
The request and response are typed in the Rust protocol crate and validated by
the Python runtime adapter. The Rust server accepts version 1 health,
configuration, RGB render, and shutdown requests and returns version 1
responses.

The protocol version is the top-level `protocol_version` field. Version 1
supports health checks, one-output configuration, complete RGB frame writes,
and orderly shutdown. A breaking wire change requires a new protocol version;
the Rust server rejects unsupported versions with a version-1 error response.

## Request envelope

```json
{"protocol_version":1,"request_id":"req-1","command":{"type":"health"}}
```

`request_id` is an opaque string copied into the corresponding response. A
breaking wire change requires a new protocol version; version 1 remains
unchanged for existing single-output simulated clients. Supported commands
are:

| Command | Data | Meaning |
| --- | --- | --- |
| `health` | none | Readiness, configured pixel count, and frames rendered. |
| `configure` | `{"config":{"pixel_count":80}}` | Configure one simulated output. Pixel count must be 1–16,384. |
| `render` | `{"pixels":[[r,g,b], ...]}` | Replace the complete simulated frame; its length must match configuration. |
| `shutdown` | none | Acknowledge and stop the engine process. |

RGB channels are unsigned 8-bit integers. Alpha, color correction, brightness,
and power limiting are resolved by the existing Python runtime before the frame
crosses IPC.

## Response envelope

```json
{"protocol_version":1,"request_id":"req-1","result":{"type":"health","status":"ready","pixel_count":80,"frames_rendered":10}}
```

`health.pixel_count` is `null` until configuration succeeds. Successful
configuration returns `configured`; a rendered frame returns `rendered` with a
one-based `frame_index`. Errors use `result.type = "error"` with one of
`unsupported_version`, `invalid_config`, `invalid_frame`, or `not_configured`.
Malformed JSON or an oversized message closes that connection without changing
the output state.

Messages, including their newline, are limited to 1 MiB. The Python adapter
opens one connection for each request and applies a one-second request timeout.
Version 1 requests are handled sequentially with other legacy requests. The
current process still owns output state and routes these requests through the
same bounded output worker used by version 2.

## Local socket handling

The Python adapter starts and owns the process, creates a private temporary
directory for its socket, polls `health` during startup, sends `configure`, and
sends `shutdown` on close. Rust binds the socket with mode `0600`, removes a
stale socket it can identify, refuses to replace a non-socket path or an active
socket, and removes its socket on orderly exit. The adapter terminates the
process if graceful shutdown times out.

This socket is local process IPC, not a network service. The temporary socket
path and file permissions limit access to the current user. There is no remote
authentication layer.

## Shared fixtures

The health request and response fixtures live in
[`rust/test-fixtures/protocol-v1/`](../../rust/test-fixtures/protocol-v1/).
Rust serialization tests and the Python codec compatibility test consume the
same fixtures. The Python-to-Rust integration test exercises the live socket,
configuration, frame write, health response, and shutdown lifecycle.
