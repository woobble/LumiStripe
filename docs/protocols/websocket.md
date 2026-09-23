# WebSocket streams

The dashboard currently exposes three authenticated streams:

- `/ws/state`: dashboard state snapshots, sent when the revision changes
- `/ws/audio`: audio telemetry at approximately 15 Hz
- `/ws/preview`: binary preview frames at approximately 30 Hz

Clients authenticate using the same pairing session cookie as REST requests.
Unauthenticated connections receive WebSocket close code `4401`. A runtime
failure closes an accepted stream with code `1011`.

REST models are generated from FastAPI OpenAPI. WebSocket payloads remain
explicitly validated at the frontend boundary and must be updated together
with their backend Pydantic models.
