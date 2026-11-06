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
monitor capture. The Audio page reports `direct_pcm`, `monitor_fallback`, or
the active capture transport, along with input format, queue drops, and DSP
processing p50/p95.

Direct PCM timestamps describe decoder queue submission, not when sound reaches
the speakers. PipeWire, speaker, and LED delays remain outside that timestamp.

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
One release run on Linux x86_64 (`--iterations 4096`) reported:

| Samples per call | Monitor p50 / p95 | Direct PCM p50 / p95 |
| ---: | ---: | ---: |
| 512 | 0.00877 / 0.00997 ms | 0.00955 / 0.01070 ms |
| 2,048 | 0.03408 / 0.03720 ms | 0.03777 / 0.04068 ms |
| 8,192 | 0.16319 / 0.17421 ms | 0.17578 / 0.18741 ms |

All paths reported zero steady-state allocations. The reported 76.2 MB peak
RSS is process-wide, not a per-path memory measurement. This synthetic run
does not emulate Spotify decoding, PipeWire scheduling, physical capture or
playback, Bluetooth, speakers, or LED timing. Direct conversion adds a small
software cost here; the benchmark does not measure the PipeWire monitor delay
that the direct path avoids. Use the procedure above for physical
audio-to-LED measurements.
