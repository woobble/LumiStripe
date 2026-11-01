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

After a direct `render` request completes, health may include
`render_time_ms`, `estimated_power_watts`, `applied_power_scale`,
`output_power_watts`, `limiting_output_index`, and `preview_outputs`. Render
time measures Rust scene rendering, zone expansion, color correction, and power finalization;
queue, output transmission, and sequential skew remain separate measurements.
Power estimates describe the corrected frame before alpha scaling. The output
array and optional limiting index use the same order as the configured outputs.
`preview_outputs` contains post-correction, post-power-limit RGBA samples in
physical output order, with at most 128 evenly spaced pixels per output.
These fields are omitted until a direct render completes, preserving the
existing health fixture and compatibility with clients that ignore additions.

### Direct Rust rendering

The `render` command accepts timestamped animation inputs instead of pixels.
The configured output count and order must match `frame.outputs`. Rust keeps
each animation instance, effect state, and scratch frame in the output worker.
It renders into reusable buffers and queues the result for output. A newer
pending render replaces a stale pending render.

```json
{"protocol_version":2,"request_id":"req-3","command":{"type":"render","data":{"frame":{"sequence":7,"timestamp_ns":1240000000,"delta_ns":16000000,"brightness":0.8,"audio":{"rms":0.4,"bands":[0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8],"beat":true,"beat_strength":0.9},"power":{"enabled":true,"budget_watts":80.0},"outputs":[{"animation":"rainbow","settings":{"palette":"neon"},"effects":[{"name":"hard_beat","settings":{},"intensity":0.75}],"base_intensity":0.8,"max_overlay_strength":0.65,"zone_count":2,"zone_pattern":"alternate","transition":{"started_at_ns":1200000000,"duration_ns":250000000,"mode":"crossfade"},"color_correction":[240,255,224],"voltage_v":5.0,"full_white_current_a":0.06,"power_limit_watts":45.0}]}}}}
```

Each output has one base animation and may specify up to two effects. Effect
settings use the effect catalog schema; effect intensity and base intensity
are bounded to `[0, 1]`, and overlay strength is bounded to `(0, 1]`. Missing
settings and effect arrays default to empty,
base intensity defaults to `1.0`, and overlay strength defaults to `0.65`.
An optional `solid_color` contains three RGB bytes and renders a brightness-
scaled solid base instead of the named animation. Solid scenes cannot include
effect layers.
`zone_count` defaults to `1` and must not exceed the configured physical pixel
count. `zone_pattern` defaults to `repeat` and may be `alternate`. Rust renders
to a logical canvas of `ceil(pixel_count / zone_count)` pixels, then expands it
into each physical zone using evenly spaced nearest samples with ties rounded
to even. `alternate` reverses the sample order in every odd-numbered zone.
For a shared whole-topology scene, `scene_pixel_count` gives the logical canvas
length and `scene_offset` selects the first pixel assigned to this output.
Omitting both fields renders one independent scene per output. Continuous
layouts use these fields to render one logical animation across multiple
outputs before applying each output's zone mapping. `scene_resample` instead
maps the full canvas to this output's logical length using evenly spaced
nearest samples with ties rounded to even; it requires a whole scene canvas
and a zero offset. Mirrored layouts use it when output lengths differ.
An optional `transition` has a monotonic `started_at_ns`, positive
`duration_ns`, and a mode of `crossfade`, `fade_black`, or `wipe`. When the
scene revision changes, Rust captures the prior logical scene frame and
composites the transition before selecting the output slice and expanding
zones. Transition frames use the same reusable buffers as normal renders.
`blackout` defaults to `false`. While true, the animation and effects continue
advancing in Rust, but the physical output frame is black; clearing the flag
restores the current rendered scene without resetting its state.
`power` defaults to disabled. When enabled, it requires a non-negative global
`budget_watts`; each output can also provide a `power_limit_watts` ceiling,
`voltage_v`, and `full_white_current_a`. Per-output RGB `color_correction`
defaults to `[255,255,255]`. Rust applies integer color correction after scene
composition and zone expansion, estimates power from corrected RGB and alpha,
chooses the minimum global/per-output scale, and applies that scale to alpha
before output. Per-output power limits are ignored while global power limiting
is disabled, matching the Python output path.
Unsupported animation names/settings and invalid frame-level values reject the
render request before it is queued. The output worker validates effect names
and settings before it renders; a bad effect rejects the frame, increments
failed-frame health, and does not transmit it. `sequence`, `timestamp_ns`, and
`delta_ns` are unsigned integers; brightness is between zero and one. Audio is
optional and carries RMS, eight bands, beat state, and beat strength.

This command renders the base animation and effect stack, applies render
brightness, scene transitions, per-output blackout and zone mapping, then
applies color correction and power limiting before writing to the configured
output set. Continuous layouts use the shared scene slice fields, and
differently sized mirrored layouts use scene resampling. The web runtime routes
supported Static, Cycling, Dynamic, and Solid scenes through this command.
The binary frame-set protocol remains available for older clients and
unsupported custom scene requests.

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
