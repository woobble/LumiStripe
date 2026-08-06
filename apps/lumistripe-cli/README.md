# lumistripe-cli

Headless GPIO runtime for Lumistripe on Raspberry Pi.

For live mic tuning without GPIO, run audio debug mode:

```bash
lumistripe-cli --audio-debug --audio-device 2
```

Measure a microphone and print recommended tuning flags:

```bash
lumistripe-cli --calibrate-audio 3 --audio-device 2
```

Apply calibration before Dynamic/audio-debug runtime starts:

```bash
lumistripe-cli --mode dynamic --auto-calibrate-audio 3
lumistripe-cli --audio-debug --auto-calibrate-audio 3
```

Playback and audio source are separate. Static and Cycling default to no audio;
Dynamic defaults to the microphone:

```bash
lumistripe-cli --mode static --animation aurora
lumistripe-cli --mode cycling --cycle-order shuffle --cycle-timing fixed --cycle-interval 30
lumistripe-cli --mode dynamic --audio-source mic
```

Dynamic holds a steady soft-blue idle color until music is confirmed. Tune the
gate delay or idle brightness when needed:

```bash
lumistripe-cli --mode dynamic --music-activation-delay 0.75 --dynamic-idle-brightness 0.08
```

While music is active, Dynamic runs one stable base animation and adds up to
two short-lived rhythmic/accent effects. Base changes use a short crossfade;
strobe-style standalone animations remain available in Static and Cycling but
are not selected by Dynamic.

## Console diagnostics

Interactive terminals show a live dashboard with separate base-animation and
effect-layer state, audio meters, music-gate health, transitions, and blend
budget. Redirected output uses timestamped `STATE`, `BASE`, `GATE`, `FX_START`,
and `FX_END` records without ANSI control codes.

Enable selector scores plus effect thresholds, cooldowns, and trigger or
suppression reasons with:

```bash
lumistripe-cli --mode dynamic --debug-selector
```

Audio-only diagnostics use the same render-stack model. `--audio-debug-verbose`
adds the full selector and effect scheduler state, while JSONL recordings retain
their existing fields and add `effect_layers` and `effect_scheduler` objects.
Set `NO_COLOR=1` to disable dashboard colors while retaining meters.
