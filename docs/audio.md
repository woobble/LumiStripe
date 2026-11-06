# Audio

The Rust engine owns audio capture, processing, feature extraction, and
telemetry. The web application and CLI select an input, apply its tuning
profile, and send lifecycle commands to the engine.

## Sources and devices

The dashboard supports automatic selection, microphone, demo, Bluetooth,
Spotify Connect, and off. Microphone devices are listed by the engine's audio
backend. Bluetooth and Spotify routes depend on the configured PipeWire and
provider services. Demo audio is generated in-process and does not require a
physical input.

When the native librespot provider is selected, the engine analyzes decoded
Spotify PCM directly while librespot continues sending playback to the
`lumistripe_spotify` PipeWire sink. The bounded PCM queue feeds the same DSP,
feature extraction, and renderer path used by capture devices. If decoded PCM
fails or stops arriving during active playback, the engine switches analysis
to the Spotify PipeWire monitor when it is available. Soloist continues using
monitor capture. Only one input feeds DSP at a time; fallback probe batches are
discarded while the monitor remains active.

The decoded tap receives PCM after librespot applies its software normalization
and Connect volume, and before it writes to the PipeWire sink. Spotify software
volume and normalization therefore affect LED response. PipeWire volume, mute,
speaker gain, and output-device changes do not affect the analyzed PCM level.
This keeps analysis independent of downstream device controls while following
the level Spotify sends to its output. It does not imply that the analyzed PCM
is identical to the waveform at the speakers.

Ingress downmixes mono or stereo PCM to mono. It rejects zero sample rates,
malformed interleaving, unsupported channel layouts, and regressing submission
timestamps; non-finite samples become counted silence. The eight-slot queue
uses nonblocking producer pushes; a full queue or busy producer lock drops the
batch and records the discontinuity. The audio sink never waits for DSP. The
worker accounts for sample-position gaps, rejects overlaps and stale
generations, and keeps feature timestamps monotonic.

Pausing Spotify clears the latest render snapshot and pending beat/structure
events without opening monitor fallback. The worker preserves DSP and structure
state for resume. Track changes and seeks advance the stream generation; the
worker drops prior events and starts fresh DSP and structure state. If active
direct PCM does not deliver its first packet within three seconds, or stops
delivering for 1.5 seconds after playback begins, monitor fallback is attempted.
While fallback is active, LumiStripe probes direct PCM every five seconds. It
requires eight contiguous batches within a three-second probe window before
switching back. Probe samples are discarded, failed probes leave the monitor
path in place, and the next probe waits another five seconds. Returning to
direct PCM resets DSP and structure state. Sync offsets are not changed by any
of these transitions.

Direct feature timestamps use monotonic PCM queue-submission time and sample
position to project the batch end. They do not represent decoder media time or
speaker presentation time. PipeWire, speaker, and LED presentation delays stay
outside that timestamp. Software tests can verify ordering and monotonicity;
they cannot establish physical audio-to-LED synchronization.

The Audio page's `input_state` distinguishes direct PCM waiting, healthy,
overloaded, and stalled states; intentional Spotify pause; active or unavailable
monitor fallback; DSP stalls; and capture health. Queue drops are recent for
five seconds in this classification. Spotify provider status separately
identifies missing authentication, a disconnected receiver, and output-sink
errors.

Use **Setup → Audio** to select and inspect the source and input device, manage
Bluetooth or Spotify routing, and tune profiles. Audio telemetry is available
on the Audio page. The settings response includes the active source, device,
monitoring state, route, and current offset.

## Tuning

Tuning profiles are saved per input device or source. They configure input
noise floor, processed noise floor, target level, smoothing response, and
feature thresholds. Values are applied as an `AudioConfig` when the engine
starts or reconfigures capture. See the [CLI guide](../apps/lumistripe-cli/README.md)
for `--audio-config`, microphone profiles, calibration, and audio-debug commands.

## Audio-to-LED sync

Open **Setup → Audio → Sync**. Confirm the active input and output route, play
the comparison cue, then adjust the route offset until the sound and LED flash
line up. Positive values delay LEDs; negative values advance them. The control
uses 5 ms steps over −500 to +500 ms. Stop the cue and save the offset for the
active route before checking ordinary playback.

The offset is persisted in dashboard settings and selected with the active
source/device route. It is an operator adjustment, not a measurement of
speaker-to-LED latency. The simulator can exercise UI and settings behavior;
physical timing validation requires external measurement equipment.

For a physical timing study, record audible output with a microphone and LED
transitions with a photodiode on two channels sharing one recorder clock. Keep
speaker level, sensor placement, brightness, and capture settings fixed. Record
at least 100 beats at a zero offset, pair each light transition with the
nearest audible onset within half a beat, then report signed median delay,
median absolute deviation, p95/p99 absolute error, and missed beats. Apply the
route offset with the guided cue and repeat the same measurement. Keep results
separate for each input/output route and do not treat a software simulation as
a physical timing result. This repository contains no established Pi latency
baseline or pass threshold.

## Software checks

Audio DSP has a Criterion benchmark that runs on synthetic 512-sample input:

```bash
cargo bench --locked -p lumistripe-engine-audio --bench audio_pipeline
```

For JSON p50/p95/p99 latency, CPU, allocation, and throughput measurements of
the DSP plus the synthetic monitor and direct-PCM queue paths, run:

```bash
cargo run --release --locked -p lumistripe-engine-audio --bin benchmark_audio -- --iterations 4096
```

The benchmark compares stereo-f32 monitor downmix plus queue and DSP against
stereo-f64 decoded-PCM conversion plus queue and DSP, using generated samples.
On the same Linux x86_64 development host and release command, the Goal 19
baseline and hardened run reported:

| Samples per call | Path | Goal 19 p50 / p95 / p99 (ms) | Hardened p50 / p95 / p99 (ms) |
| ---: | --- | ---: | ---: |
| 512 | Monitor | 0.00873 / 0.00963 / 0.01073 | 0.00901 / 0.00964 / 0.01041 |
| 512 | Direct PCM | 0.00967 / 0.01040 / 0.01198 | 0.00978 / 0.01048 / 0.01128 |
| 2,048 | Monitor | 0.03419 / 0.03713 / 0.03881 | 0.03529 / 0.03825 / 0.04041 |
| 2,048 | Direct PCM | 0.03716 / 0.03986 / 0.04235 | 0.03810 / 0.04134 / 0.04363 |
| 8,192 | Monitor | 0.16192 / 0.17545 / 0.18171 | 0.15991 / 0.17156 / 0.17456 |
| 8,192 | Direct PCM | 0.17458 / 0.18711 / 0.19654 | 0.17150 / 0.18315 / 0.18611 |

All paths reported zero steady-state allocations. Process peak RSS was 77.56
MB at baseline and 76.92 MB in the hardened run; RSS is process-wide, not a
per-path measurement. The 512- and 2,048-sample p50/p95 differences stayed
within about four percent, while the 8,192-sample percentiles improved in this
run. This does not show a material regression in the queue-plus-DSP microbench,
but the benchmark does not include the librespot sink wrapper, Spotify
decoding, PipeWire scheduling, hardware, or LED output.

Run the selectable bounded stress test with:

```bash
cargo test --locked -p lumistripe-spotify \
  synthetic_start_seek_switch_overload_and_shutdown_stay_bounded \
  -- --ignored --nocapture
```

The test exercises synthetic start/stop, seek and track generation changes,
software volume changes, demo/direct source switches, queue saturation, and
runtime shutdown. One Linux x86_64 run processed 2,097,152 synthetic samples in
0.06 seconds, filled the queue to 8/8 batches, dropped 2,013,696 samples during
the deliberate producer burst, observed 430,155 ns DSP p95, used about 103%
of one core, and ended 9.5 MiB above its process RSS baseline. Engine worker
threads joined during shutdown; the test process had one additional idle Tokio
blocking thread when sampled. These are one-host stress observations, not
hardware guarantees. The existing three-minute simulated DSP pipeline test
continues to cover sustained processing and reconfiguration.

Synthetic runs do not emulate Spotify authentication, local discovery,
PipeWire/device output, physical speakers, or LED timing. Use external
measurement for physical audio-to-LED results.
