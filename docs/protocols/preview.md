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

Version 1 reserves the flags byte and requires it to be zero. Implementations
must reject packets with more than two outputs, more than 4096 pixels per
output, trailing bytes, or an output length that is not a complete RGBA
pixel sequence. The sequence wraps at `2^32`.

The following version-1 packet is the cross-language golden example (sequence
23, one one-pixel output and one two-pixel output):

```text
4c534650010017000000020001000000ff0001ff020000000203048009080700
```

The backend encoder lives in `lumistripe_web.api.protocols.preview`; the
frontend decoder must reject unknown magic, versions, truncated payloads, and
non-RGBA output sizes.
