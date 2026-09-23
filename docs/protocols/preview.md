# Preview frame protocol

The preview stream is a binary WebSocket at `/ws/preview`.

All integer fields are little-endian. The packet begins with:

| Field | Size | Value |
| --- | ---: | --- |
| magic | 4 bytes | ASCII `LSFP` |
| version | 1 byte | `1` |
| flags | 1 byte | `0` currently |
| sequence | 4 bytes | unsigned frame sequence |
| output count | 2 bytes | number of output payloads |

Each output then contains a four-byte unsigned pixel count followed by
`pixel_count * 4` RGBA bytes. Outputs are ordered according to the backend
topology.

The backend encoder lives in `lumistripe_web.api.protocols.preview`; the
frontend decoder must reject unknown magic, versions, truncated payloads, and
non-RGBA output sizes.
