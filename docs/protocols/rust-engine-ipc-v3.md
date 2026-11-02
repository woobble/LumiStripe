# Rust engine local IPC, version 3

Protocol v3 adds Rust audio capture lifecycle, device discovery, and telemetry
to the v2 rendering and output protocol. Control requests remain UTF-8 JSON
lines on the private Unix socket. The binary frame socket stays at LSF2/version
2 and its packet format is unchanged. The server continues to accept v1 and v2
requests for their existing commands; audio commands require v3.

Each request includes `protocol_version: 3` and a nonempty `request_id` copied
into its response. Control messages are limited to 1 MiB. Existing socket
timeouts, request validation, and private-socket lifecycle are unchanged; see
the [v2 protocol](rust-engine-ipc-v2.md) for render, scheduler, and binary frame
details.

## Audio commands

### Start capture and processing

`audio_start` selects an optional input and applies the complete flat audio
configuration. An empty config object uses the Rust defaults:

```json
{"protocol_version":3,"request_id":"audio-start-1","command":{"type":"audio_start","data":{"selector":null,"config":{}}}}
```

The response is `audio_configured` with `active: true` after a stream opens. If
the device cannot be opened, the server returns `error` with code
`audio_failure`; the audio worker retains the desired settings and retries
capture recovery in the background.

`selector` may be `null` to choose the first available default input in host
priority order, a selector returned by `audio_devices`, or a device name match.
Linux host priority is PipeWire, PulseAudio, then ALSA. The stream uses the
selected device's default sample rate and channel count and converts each input
frame to mono. The device selector is stable only as provided by the host; use
`audio_devices` again after hardware or server changes.

### Reconfigure and stop

`audio_configure` replaces DSP and microphone-conditioning settings on the
active capture stream. It preserves the selected device and sample rate by
recreating the processor against that stream's current rate:

```json
{"protocol_version":3,"request_id":"audio-configure-1","command":{"type":"audio_configure","data":{"config":{"microphone_enabled":true,"high_pass_hz":30.0}}}}
```

The response is `audio_configured` with the resulting `active` state. Calling
it while capture is stopped stores the settings and returns `active: false`.
`audio_stop` releases the input stream and clears pending beat and structure
events:

```json
{"protocol_version":3,"request_id":"audio-stop-1","command":{"type":"audio_stop"}}
```

It responds with `{"type":"audio_stopped"}`. Engine shutdown stops and joins
the audio worker before it shuts down the output worker.

### Enumerate devices

`audio_devices` returns input devices from the supported CPAL Linux hosts:

```json
{"protocol_version":3,"request_id":"audio-devices-1","command":{"type":"audio_devices"}}
```

Each device has `selector`, `host`, `name`, `is_default`, `sample_rate`, and
`channels`. Device enumeration and stream creation happen on the control/audio
worker paths, outside the capture callback.

### Read audio status

`audio_status` returns the latest typed snapshot:

```json
{"protocol_version":3,"request_id":"audio-status-1","command":{"type":"audio_status"}}
```

The result contains:

| Field | Contents |
| --- | --- |
| `active`, `device`, `last_error` | Current capture state, selected input, and most recent recovery or processing error. |
| `frame` | Current RMS, eight bands, beat state and strength, sequence, capture timestamp, and freshness. |
| `features` | BPM/confidence, energy and level, volume, bass/mid/treble, brightness, onset, spectral centroid/flux, dynamic range, rolling/program loudness, musical impact, silence, drop, and section-change features. |
| `structure` | Current `idle`, `breakdown`, `groove`, `buildup`, or `drop` phase, confidence, buildup progress, and one-frame drop edge. |
| `processor` | Feed/sample/FFT counts, normalization gain, RMS, loudness, impact, peak/clipping, microphone gate, and beat-event overflow counts. |
| `capture` | Callback/error/queue-overflow counts, dropped samples, latest callback/error times, capture latency p50/p95/p99, callback duration p50/p95/p99, and callback interval jitter p95. |
| `last_frame_ns` | Monotonic timestamp of the latest complete analysis frame. |
| `audio_update_sequence`, `dropped_audio_updates`, `dropped_structure_events` | Scheduler handoff sequence and bounded-event overflow counters. |

Audio and capture times use the Linux monotonic clock domain shared with the
Rust scheduler and output pipeline. Capture latency and callback-duration
percentiles use the most recent at most 64 callbacks. Interval jitter is the
absolute difference between consecutive callback intervals and the expected
interval from each callback's frame count, summarized at p95. A zero percentile
means no nonzero sample has been recorded yet.

## Configuration fields

`config` is a flat object. Omitted properties use the same defaults as Python's
`AudioConfig`:

| Category | Fields |
| --- | --- |
| Microphone conditioning | `microphone_enabled`, `input_noise_floor`, `high_pass_hz` |
| Smoothing and gate | `noise_floor`, `rms_attack`, `rms_release`, `band_attack`, `band_release`, `beat_release`, `smoothing_enabled` |
| Normalization | `target_level`, `min_gain`, `max_gain`, `adapt_attack`, `adapt_release`, `music_threshold`, `music_max_gain`, `silence_floor`, `normalization_enabled`, `dc_block_enabled` |
| Music analysis | `drop_bass_threshold`, `drop_bass_delta_threshold`, `drop_onset_threshold`, `section_change_threshold`, `section_onset_threshold` |

Numeric values must be finite; microphone noise floor must be 0–0.5 and high
pass frequency 0–100 Hz. Invalid values return `invalid_config`.

## Data flow and bounds

The CPAL callback downmixes into fixed 2048-sample batches and pushes them into
an eight-entry preallocated SPSC queue. The callback does not allocate, block on
a lock, perform filesystem access, log, or send IPC. A dedicated Rust audio
worker drains the queue, runs the 2048-point/512-hop DSP and structure tracker,
and submits features plus reliable beat/structure edges directly to the Rust
playback scheduler. Raw PCM never crosses the Python IPC boundary. Queue
overflows and dropped samples are counted; the DSP advances sample positions
across gaps and records discontinuities.

The audio worker checks for a stopped callback, tears down a stalled stream,
and retries after a bounded delay. Python polls `audio_status` to populate the
existing API, dashboard, and calibration models. When `audio_backend` is
`python`, the existing PortAudio/native-C worker remains the only active capture
path.

## Version compatibility

Version 2 remains valid for rendering, scene updates, audio snapshots produced
by Python, health, configuration, and shutdown. Sending an audio lifecycle
command with v1 or v2 returns `unsupported_version`. The separate LSF2 binary
frame stream remains version 2 under both control protocol versions.
