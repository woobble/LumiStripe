# LumiStripe Rust CLI

The installer exposes this Rust binary as `lumistripe-cli` from the active,
commit-matched deployment release. It starts the matching Rust engine as a
child process and controls it over versioned local IPC. Playback, scheduling,
rendering, audio capture, and hardware output remain owned by the engine.

The `run` command defaults to SPI output at
`/dev/spidev0.0`, 80 pixels, and Static mode. Select `--backend mock` for
hardware-free runs. It supports mock, mock SPI, SPI, and GPIO output; one or
two outputs; animation settings; brightness; frame-rate limits; a shared
power budget; sequential or shuffled cycling; and the Dynamic selector,
music gate, idle brightness, and microphone tuning options. Audio device
discovery and capture use engine IPC. Dynamic playback defaults to microphone
capture and also accepts the engine's demo source. `--audio-config` accepts a
JSON object of shared Rust `AudioConfig` fields for tuning capture and DSP.
Use `--engine-binary` or `LUMISTRIPE_ENGINE_BINARY` when the engine executable
is not next to the CLI.

From the repository root, enter `rust/` before running the examples below;
they use the Rust workspace manifest.

Legacy `--spi-device-2` and secondary GPIO pin flags create a second output
with the primary pixel count when `--pixels-2` is omitted. The Rust-only
`--pixels-2` option can set a different length. Mic profiles can be written
offline with `--write-mic-profile`; add `--audio-source mic` and
`--auto-calibrate-audio` only when the profile should include live calibration.

```bash
cargo run -p lumistripe-cli -- list-animations
cargo run -p lumistripe-cli -- run --backend mock --pixels 80 --animation rainbow_cycle
cargo run -p lumistripe-cli -- run --backend mock --pixels 80 --pixels-2 48 --mode cycling
cargo run -p lumistripe-cli -- run --backend mock --mode cycling --cycle-order shuffle --cycle-timing fixed --cycle-interval 30
cargo run -p lumistripe-cli -- run --backend mock --mode dynamic
cargo run -p lumistripe-cli -- run --backend mock --mode dynamic --idle-enter-frames 60 --music-activation-delay 0.75 --dynamic-idle-brightness 0.08
cargo run -p lumistripe-cli -- run --backend mock --mode dynamic --debug-selector --stats
cargo run -p lumistripe-cli -- run --backend mock --mode dynamic --audio-source demo
cargo run -p lumistripe-cli -- run --backend mock --mode dynamic --audio-device "USB mic" \
  --audio-config '{"target_level":0.42,"noise_floor":0.02}'
cargo run -p lumistripe-cli -- run --backend mock --mode dynamic --mic-profile auto \
  --auto-calibrate-seconds 5 --stats
cargo run -p lumistripe-cli -- run --backend mock --mode dynamic --audio-source mic \
  --write-mic-profile ~/.config/lumistripe/mic.json
cargo run -p lumistripe-cli -- run --backend spi --mode static \
  --encoder1-a 5 --encoder1-b 6 --encoder1-button 13 \
  --encoder2-a 20 --encoder2-b 21 --encoder2-button 26
cargo run -p lumistripe-cli -- calibrate-audio --duration-seconds 5 --audio-device "USB mic"
cargo run -p lumistripe-cli -- audio-debug --duration-seconds 30 --verbose --record /tmp/lumi-audio.jsonl --label rehearsal
cargo run -p lumistripe-cli -- analyze-audio-debug /tmp/lumi-audio.jsonl
cargo run -p lumistripe-cli -- validate-outputs outputs.json
cargo run -p lumistripe-cli -- health
cargo run -p lumistripe-cli -- audio-devices
cargo run -p lumistripe-cli -- capabilities
```

The process test starts the CLI and a real Rust engine with a `PATH` that
contains no Python interpreter:

```bash
cargo test -p lumistripe-cli --features test-engine
```

The installed `lumistripe-cli` command uses Rust without Python. It supports
one-shot and automatic calibration, named or device-matched mic
profiles, profile writing, and opt-in live engine performance reports through
`run --stats`. Rust audio-debug recordings
contain the engine's capture and DSP telemetry as JSONL; analysis accepts those
files and earlier compatible JSONL recordings.
Dynamic playback uses the engine's generated effect catalog and its default
effect-scheduler tuning. `--debug-selector` streams
the selected animation, activity gate, selector reason, ranked scores and
contributing factors, timing, recent history, beat clock, and effect state.
`audio-debug --verbose` includes that selector data in its structured audio
rows and JSONL recordings.

For compatibility with older scripts, `lumistripe run` accepts aliases such
as `--output-backend`, `--chip`, `--cycle-interval`, `--spi-speed-2`, and
`--auto-calibrate-audio`. Top-level audio action aliases are also available
without a subcommand:

```bash
cargo run -p lumistripe-cli -- --list-audio-devices
cargo run -p lumistripe-cli -- --calibrate-audio 5 --audio-device "USB mic"
cargo run -p lumistripe-cli -- --audio-debug --audio-debug-duration 30 \
  --audio-debug-record /tmp/lumi-audio.jsonl --audio-debug-label rehearsal
cargo run -p lumistripe-cli -- --analyze-audio-debug /tmp/lumi-audio.jsonl
```

When multiple action flags are supplied, dispatch follows the historical
priority: analyze recordings, list devices, calibrate, write a mic
profile, audio diagnostics, then playback. Use `--quiet` to suppress routine
playback status.

Optional rotary encoders use GPIO edge events with pull-ups. Encoder 1 rotates
through animations and cycles Static/Cycling/Dynamic on press; encoder 2 changes
brightness. The GPIO chip defaults to `--gpio-chip` and can be set separately
with `--encoder-chip`.
