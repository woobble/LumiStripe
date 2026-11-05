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

This measures the audio processing pipeline without a capture device. It does
not measure microphone, PipeWire, Bluetooth, speaker, or LED timing.
