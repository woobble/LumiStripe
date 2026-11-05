# HTTP authentication and WebSocket streams

## Pairing and REST errors

When pairing is enabled, `/api/health`, `/api/auth/status`, and
`/api/auth/pair` are public. Other `/api/` routes require a valid
`lumistripe_session` cookie. Pairing accepts exactly four ASCII digits. A
successful response sets an HttpOnly, SameSite=Strict cookie with a 30-day
expiry and adds Secure when the request uses HTTPS. Logging out revokes that
session. Sessions and failed attempts are held in memory, so a web-service
restart clears them.

The process allows five failed pairing attempts in a 60-second window across
all clients. Further attempts receive HTTP 429 with `Retry-After`; an incorrect
code receives 401. Other common responses are 401 for a missing or expired
session, 403 for a disallowed origin on a state-changing request, and 422 for
request validation. `ApiError` responses use `{"detail": ...}`; validation
details are arrays with `type`, `loc`, `msg`, and related compatibility fields.
The Rust-generated [OpenAPI document](../../apps/lumistripe-web/frontend/openapi.json)
defines the REST request and response schemas.

## Streams

The dashboard exposes three streams:

- `/ws/state`: JSON snapshots when published dashboard state changes
- `/ws/audio`: JSON audio telemetry at approximately 15 Hz
- `/ws/preview`: binary preview frames at approximately 30 Hz

When pairing is enabled, clients use the same cookie as REST. Invalid origins
close with WebSocket code `1008`. Missing, expired, or revoked sessions close
with `4401`; the server rechecks authentication while a stream is open. A
runtime failure closes an accepted stream with `1011`, and a client that
cannot keep up may be closed with `1013`.

The binary preview packet is specified in the
[preview protocol](preview.md). Frontend network and stream payloads are
validated at the boundary and must change together with their Rust contracts.
