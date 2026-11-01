# Rust engine local IPC, version 2

Version 2 keeps low-rate control and health messages as UTF-8 JSON lines and
uses a compact binary packet for complete multi-output RGBA frame sets. The
Python adapter uses one private Unix-domain socket for control requests and
one persistent stream for frame data. A frame acknowledgement means Rust has
accepted or replaced the queued frame; it does not mean that the LEDs have
finished updating. The output worker owns device writes and reports completion
through health telemetry.

## Control messages

Each control request and response is one JSON object followed by `\n`. The
request includes `protocol_version: 2` and an opaque `request_id` copied into
the response. Control messages are limited to 1 MiB. Unsupported protocol
versions receive an error response; malformed or oversized JSON closes that
connection without changing output state. Request IDs must be nonempty, at
most 128 bytes, and contain no control characters. Reads have an overall
deadline per control request or frame packet; writes have a bounded socket
timeout. Persistent frame streams poll between packets so an idle stream does
not prevent clean engine shutdown.

Health request:

```json
{"protocol_version":2,"request_id":"req-1","command":{"type":"health"}}
```

Configure all outputs in one transaction:

```json
{"protocol_version":2,"request_id":"req-2","command":{"type":"configure","data":{"config":{"outputs":[{"id":"main","pixel_count":80,"reversed":false,"backend":{"type":"spi","device":"/dev/spidev0.0","speed_hz":1000000}},{"id":"secondary","pixel_count":40,"reversed":true,"backend":{"type":"gpio","chip":"/dev/gpiochip0","data_pin":10,"clock_pin":11}}]}}}}
```

The `backend.type` is `mock`, `mock_spi`, `spi`, or `gpio`. `mock` retains
frames in memory; `mock_spi` runs the SM16716 encoder and discards the encoded
transfer for parity and benchmark runs without opening a device. Each output
has a unique ID and 1–16,384 pixels; no more than two outputs are accepted. SPI
device paths and GPIO chip/pin pairs must be unique. GPIO pins must be distinct
and between 0 and 53. Invalid configuration returns `result.type = "error"`
with `invalid_config`.

If a valid configuration cannot open or claim a device, configure returns
`output_failure`. Rust blackouts and releases the current output set before
opening the new devices; if opening fails, health records the error and no
output set remains configured.

Health includes configured pixel counts, completed frames, last completed
sequence, queue drops, failed writes, last device error, measured output rate,
last frame queue-to-completion latency with rolling p50/p95/p99, queue wait,
time spent transmitting all outputs sequentially, measured completion skew,
and the timestamp of the last successful device update. The Python bridge also
measures the frame IPC send/ack round trip. Errors use `invalid_frame`, `not_configured`,
`output_failure`, or `shutting_down` as appropriate.

Shutdown waits for the Rust output worker to send a black frame and release
configured device handles, then acknowledges and exits. If a hardware write
fails, the error remains visible in health until a later successful output.
Every newly opened output set also sends a black frame before configuration is
reported successful, so a process restart clears a strip's latched prior frame
before Python begins publishing its current topology.

## Binary frame-set packet

All integers are unsigned little-endian. Each packet has a 20-byte header
followed by one concatenated RGBA byte buffer per configured output:

| Offset | Size | Field |
| --- | ---: | --- |
| 0 | 4 | Magic bytes `LSF2` |
| 4 | 2 | Frame protocol version (`2`) |
| 6 | 2 | Output count |
| 8 | 8 | Python frame sequence |
| 16 | 4 | Total payload byte count |
| 20 | variable | Output frames in configured order; each pixel is `[R, G, B, A]` |

The payload size must exactly equal four times the sum of configured pixel
counts. The total payload is capped at 128 KiB. Reversal is configured per
output and applied in Rust. Color correction, power limiting, and brightness
have already been applied by Python. Rust multiplies each channel by alpha
using integer floor division by 255 before driving the output.

Every packet receives a fixed 24-byte acknowledgement:

| Offset | Size | Field |
| --- | ---: | --- |
| 0 | 4 | Magic bytes `LSA2` |
| 4 | 2 | Frame protocol version (`2`) |
| 6 | 2 | Status: `0` accepted, `1` not configured, `2` shutting down, `3` invalid frame |
| 8 | 8 | Acknowledged sequence |
| 16 | 8 | Cumulative Rust queue frames dropped |

The Rust queue uses two preallocated frame sets: at most one is waiting while
the output worker owns the other. A newer waiting set replaces the older one.
The Python exchange has the same latest-wins rule, so slow output cannot build
an unbounded backlog or block the Python render loop.

## Socket ownership and cleanup

Python starts and owns one engine process per runtime in a private temporary
directory and passes its PID as the engine owner. Rust binds the Unix socket
with mode `0600`, refuses to replace a non-socket path or an active socket,
and removes the socket on shutdown. The engine blackouts and exits if its
owner process disappears, so an abrupt application restart does not leave an
orphaned child holding the hardware. The frame stream stays open while output
is active; control connections are accepted concurrently. This is local
process IPC, not a network service.

Rust creates, uses, blackouts, and closes all mock/SPI/GPIO devices on its
single output-owner thread. SPI uses mode 0, eight-bit, MSB-first transfers and
attempts to disable chip select; an `EINVAL` fallback matches Raspberry Pi 5
controllers that reject `SPI_NO_CS`. GPIO selects its MMIO layout from the
device tree: older supported BCM SoCs use `/dev/gpiomem`, and RP1 systems such
as Raspberry Pi 5 use `/dev/gpiomem0` with the IO_BANK, RIO, and PADS register
banks. The RP1 path restores pin control, output-enable, output-latch, and pad
settings during cleanup. Unknown platforms skip direct MMIO and use Linux GPIO
character-device line requests. The RP1 mapping and register offsets follow
Raspberry Pi's [Pi 5 device tree](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/boot/dts/broadcom/bcm2712-rpi.dtsi)
and [RP1 pinctrl driver](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/pinctrl/pinctrl-rp1.c).

## Shared fixtures and tests

The control health fixtures are in
[`rust/test-fixtures/protocol-v2/`](../../rust/test-fixtures/protocol-v2/).
Rust and Python validate the same fixture. Rust tests cover configuration,
binary headers and acknowledgements, alpha/channel encoding, reversal, mock
outputs, latest-wins queue behavior, socket permissions/lifecycle, and
multi-output frame sets. The Python integration test starts one Rust process,
configures multiple outputs, sends binary frames, checks health, and verifies
shutdown.
