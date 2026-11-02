# Rust engine local IPC, version 2

Control protocol v3 is current and adds optional Rust audio capture, DSP,
device discovery, and status commands. This document records the v2 rendering
and output contract, which remains supported for existing clients. See the
[v3 protocol](rust-engine-ipc-v3.md) for the additive audio commands.

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
`scheduler_missed_deadlines` reports elapsed frame deadlines skipped while the
Rust scheduler was delayed. It is omitted while zero, preserving compatibility
with clients that ignore additions. When animation scenes are active, health
also reports `selected_animations`, `scene_revisions`, and `cycling_active` in
configured output order. They describe the selection Rust is currently
rendering, including selections made by Cycling policy. Scheduled render health
reports `audio_feature_to_led_latency_ms` and p50/p95/p99 values when audio
features carry `feature_timestamp_ns`. The timestamp is the audio capture time
in the shared monotonic clock domain; latency ends after the corresponding LED
output transaction completes. The percentile window is bounded to the most
recent 256 output frames. A future or absent capture timestamp is not measured.
Scheduled render health also reports one `beat_clocks` entry per output with its
`sampled_at_ns`, BPM,
phase, confidence, beat index, and predicted beat timestamps. The sample timestamp
uses the same monotonic nanosecond domain as render inputs. Each clock also
reports `last_event_sequence`, the highest timestamped beat event it consumed.
Dynamic outputs that include an activity configuration also report one
`dynamic_gate_states` value per output: `idle`, `candidate`, or `music`.
Outputs without a Rust-owned Dynamic activity gate report `disabled`. The field
is omitted when no render schedule is active.
When any output has a saved Dynamic sequence, `sequence_playback` reports
sequence progress per configured output, including the optional sequence ID,
whether it is active or waiting for a confident clock, the current step and
animation, remaining bars, and the next transition timestamp. Entries follow
configured output order; outputs without a sequence have an inactive state.
The field is omitted when no sequence is configured. Transition timestamps use
the same monotonic nanosecond domain as render inputs.
When any Dynamic output has an `effect_scheduler`, `effect_schedulers` reports
active effects, cooldowns, trigger results, drop-accent state, and beat-tick
lateness per output. Outputs without effect scheduling have an inactive entry.

The scheduled runtime uses separate scene and audio control messages. A
`scene_update` is a complete pixel-free snapshot with a monotonically
increasing `revision`, timestamp, brightness, power settings, output scenes,
and scheduler configuration:

```json
{"protocol_version":2,"request_id":"scene-1","command":{"type":"scene_update","data":{"update":{"revision":1,"timestamp_ns":1256000000,"brightness":1.0,"outputs":[{"animation":"rainbow"}],"scheduler":{"max_fps":60}}}}}
```

Rust processes scene updates in request order and acknowledges the revision
with `scene_updated`. Revisions are independent of legacy render sequence IDs.
The response's `replaced` flag reports when a newer schedule replaced one that
had not rendered yet; the revision was still processed in order. The Python
bridge uses a bounded 64-entry pending FIFO; a topology reconfiguration clears
snapshots for the previous output set. If the queue fills, the runtime
reports the failure and returns to the Python renderer instead of discarding a
control update. The acknowledgement means Rust accepted the schedule, not that
an LED frame has finished transmission. Health reports the latest accepted
`scene_control_revision`.

An `audio_update` carries a monotonically increasing `sequence`, monotonic
timestamp, and the latest optional audio snapshot:

```json
{"protocol_version":2,"request_id":"audio-1","command":{"type":"audio_update","data":{"update":{"sequence":1,"timestamp_ns":1272000000,"audio":null}}}}
```

The Python bridge holds at most one pending audio snapshot; a newer snapshot
replaces it. One-shot beat and structure events remain in each snapshot until
Rust health confirms that their per-output sequences were consumed. Rust
ignores duplicate or out-of-order audio sequences and rejects timestamps older
than the last accepted audio timestamp. The `audio_updated` response reports
whether the snapshot was accepted, while health reports the latest
`audio_update_sequence`.

### Direct Rust rendering

The `render` command accepts timestamped animation inputs instead of pixels.
The configured output count and order must match `frame.outputs`. Rust keeps
each animation instance, effect state, and scratch frame in the output worker.
It renders into reusable buffers and queues the result for output. A newer
pending render replaces a stale pending render.

`audio.feature_timestamp_ns` optionally carries the capture time for the
current feature snapshot. Rust uses it to measure capture-to-output latency.
`audio.fallback_bpm` is an optional tempo hint, and `audio.beat_events` carries
timestamped beat events detected by Python. Event timestamps use the same
monotonic nanosecond domain as `frame.timestamp_ns`. Rust deduplicates events by
sequence and ignores events whose timestamps do not advance the clock. The
Python bridge retains up to 256 unacknowledged beat events and includes them in
subsequent scene updates until every configured Rust beat clock reports that it
consumed their sequence. Replaced latest-wins scene updates therefore do not
consume one-shot beat events. If the bounded replay window overflows, the oldest
events are dropped and counted in audio health.
`audio.structure_events` carries one-shot music-structure edges separately
from replaceable feature snapshots. Each event contains its output index,
per-output sequence, monotonic timestamp, phase, confidence, buildup progress,
and `drop_event` or `section_change` flags. The batch is capped at 256 events;
the Python bridge replays pending events until health reports the corresponding
`structure_event_sequences` entry for each output. Rust ignores duplicate or
older event sequences, so replaced scene updates do not lose an edge or apply
it twice. Events are consumed in sequence order once their timestamps are due;
the flags in one eligible batch are coalesced into one scheduler update per
output. If the replay window fills, the bridge falls back to Python playback
instead of growing the queue or dropping unacknowledged edges. A structure event
must contain at least one edge flag.
Each beat event may include a normalized `strength`; `audio.beat_event_stream`
identifies producers that provide timestamped events even when the latest batch
is empty. `audio.effect_features` carries the effect scheduler's normalized
beat strength and musical impact. The selector feature object continues to
carry onset, spectrum, and structure-related feature inputs.
Dynamic selector inputs may include `audio.feature_sequence`, a monotonically
increasing producer sequence. Rust evaluates selector scores once for each new
sequence and ignores duplicate or older values, so the scheduler's render FPS
does not change how quickly seeded selector randomness advances. If omitted,
Rust uses the enclosing render request sequence. Latest-wins may skip intermediate
feature sequences when the producer outpaces the engine.
`selector.activity` optionally configures Rust's smoothed feature thresholds,
activation delay, hysteresis, beat-density window, and idle-release frame count.
When present, `cycling.dynamic_idle` selects `off`, `solid`, or `animation`
behavior while the gate is idle or a candidate. Idle animation mode includes
its animation name and saved settings; solid mode includes its RGB color and
brightness. Rust keeps the active Dynamic selection while it renders the idle
scene, and health reports that active selection in `selected_animations`.
`audio_enabled` defaults to true on each output and may be set false to render
that output without the current audio frame.

The selector may include `switch_quantization` (`off`, `beat`, `bar`, or
`phrase`), `bars_per_phrase` (4, 8, or 16), `clock_confidence_threshold`,
`subdivisions_per_beat` (1, 2, or 4), and signed `sync_offset_ns` (from
-500,000,000 to 500,000,000). These fields default to unquantized switching
when omitted. Beat, bar, and phrase switches wait for a confident boundary from
the per-output Rust beat clock. A pending candidate follows newer selector
intent while retaining its scheduled boundary; a lost or uncertain clock clears
it. Late or transition-blocked candidates move to the next matching boundary.

An optional `scheduler` enables autonomous Rust rendering after the scene has
been submitted:

```json
{"protocol_version":2,"request_id":"req-4","command":{"type":"render","data":{"frame":{"sequence":8,"timestamp_ns":1256000000,"delta_ns":16000000,"brightness":0.8,"audio":null,"outputs":[{"animation":"rainbow"}]},"scheduler":{"max_fps":60}}}}
```

Cycling scenes carry their mode and choices on each output. Rust owns the
selection clock and switches only after a successful output update:

```json
{"protocol_version":2,"request_id":"req-5","command":{"type":"render","data":{"frame":{"sequence":9,"timestamp_ns":1272000000,"delta_ns":16000000,"brightness":1.0,"audio":null,"outputs":[{"animation":"rainbow","cycling":{"order":"shuffle","timing":"per_animation","interval_ns":30000000000,"seed":7,"choices":[{"animation":"rainbow","settings":{}},{"animation":"rainbow_cycle","settings":{"speed":2}}],"transition_duration_ns":300000000,"transition_mode":"crossfade"}}]},"scheduler":{"max_fps":60}}}}
```

`cycling` is optional and configured independently for each output. `order` is
`sequential` or `shuffle`; `timing` is `per_animation` or `fixed`. Fixed timing
uses `interval_ns`; per-animation timing uses the catalog's
`frames_per_cycle` and actual Rust-rendered frames. `choices` contains the
automatic animation catalog entries and each animation's saved settings.
Shuffle uses the same MT19937 draw sequence as Python `random.Random` for
non-negative seeds that fit in 64 bits. When a selection changes, Rust restarts
that animation, increments its scene revision, and starts the configured
transition from the previous rendered frame. A zero transition duration
disables the transition. A scene update with the same active animation and
cycling config preserves the cycle clock and random state.

Dynamic mode uses the same per-output choices and adds a `selector` object to
`cycling`. When present, Rust ranks the catalog's `dynamic_metadata` using the
latest `audio.selector_features`, then applies minimum/maximum duration,
switch cooldown, score margin, drop hold, and section-change timing before
changing the scene. `selector_features` carries the Python-produced energy,
spectrum, beat-confidence, BPM, silence, drop, and section-change values; it
does not move capture, FFT, or DSP into Rust. Seeded score jitter uses the same
Python-compatible MT19937 draws as other Rust selection. Selection is evaluated
once per `feature_sequence` update rather than on each scheduled render. The
initial active Dynamic selection is immediate. `switch_quantization` controls
when later candidates commit; `sync_offset_ns` shifts those musical boundaries.
Without `selector`, `cycling` retains the sequential/shuffle behavior above.
`cycling_active` reports whether Rust owns one of these automatic selection
policies.

An optional `sequence` on Dynamic `cycling` schedules saved animation choices
against the Rust beat clock:

```json
{"id":"night-drive","repeat":true,"steps":[{"animation":"pulse","duration_bars":2},{"animation":"comet","duration_bars":1}]}
```

The sequence requires `selector`; its steps must name entries from `choices`.
It accepts 1–32 steps, each lasting 1, 2, 4, or 8 estimated 4/4 bars. Rust
waits for a confident clock and the next bar boundary before starting, applies
`sync_offset_ns` to boundaries, and pauses progress while clock confidence is
low. A non-repeating sequence returns control to the Dynamic selector when its
last step completes. Python mirrors Rust's selection and sequence state for API
compatibility and fallback. A timestamped section edge may rescue a missed bar
transition only after the configured bar duration is exhausted, and only while
the edge plus `sync_offset_ns` is within the normal late-transition grace. The
offset applies to the section timestamp; an early section edge never shortens a
step. Python continues classifying music structure and sends that state with
each output. Rust owns effect cooldowns, beat subdivision
scheduling, category/capacity checks, candidate selection, active-effect
lifetimes, and drop accents for Dynamic scenes.

An optional `cycling.effect_scheduler` configures this Rust-owned policy. It
contains beat and onset thresholds, rhythmic and accent cooldowns, the active
effect limit, overlay strength, beat subdivisions, clock confidence threshold,
seed, sync offset, dynamic response, and available effect choices with saved
settings. It requires a Dynamic `selector`. The output's optional
`music_structure` object carries the phase, confidence, buildup progress, and
legacy one-update `drop_event` edge. Python sends new one-shot drop and section
edges in `audio.structure_events`; Rust also accepts the legacy output field
for compatibility. Explicit `output.effects` remain available for clients that
schedule effects themselves.

`scheduler.max_fps` must be between 1 and 240. The actual frame period is the
slower of this cap and the fastest animation-specific frame period in the
submitted scene. The scheduler uses monotonic absolute deadlines, advances from
the prior deadline to avoid sleep drift, and skips elapsed deadlines instead
of queueing obsolete work. Its pending render shares the bounded latest-wins
output queue, while a separate thread performs rendering and physical
transmission. Updates with the scheduler enabled replace the active scene and
retain the existing schedule phase. Rust translates elapsed time from the
request's timestamp onto its own monotonic clock; subsequent render timestamps
and deltas remain in the caller's clock domain. Omitting `scheduler` preserves
one-shot rendering for existing clients. A binary frame submission cancels an
active scene schedule.

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
Unsupported animation names/settings, invalid selection choices, and invalid frame-level values reject the
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
Rust owns Cycling and Dynamic base-animation selection, including saved
sequences and activity policies supported by the protocol; unsupported scene
policies continue through the Python fallback.
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
