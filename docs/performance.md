# Performance and benchmarks

The repository contains software microbenchmarks for engine audio processing
and animation rendering. They use synthetic inputs and are useful for
comparing changes on the same host and toolchain. No current Raspberry Pi
throughput or end-to-end audio-to-LED latency claim is established here.

Run the Criterion benchmarks from the repository root:

```bash
cargo bench --locked -p lumistripe-engine-audio --bench audio_pipeline
cargo bench --locked -p lumistripe-engine-animation-state --bench animation_render
```

The audio benchmark processes synthetic 512-sample hops, with and without the
music-structure tracker. The animation benchmark renders selected animations
at 150, 300, and 600 pixels. These do not include hardware output or capture.

For a stable comparison, use the same release profile, Rust toolchain, machine,
power mode, and benchmark command. Save Criterion's local output separately;
it is not a checked-in baseline and values should not be compared across
different hosts as hardware guarantees.

The [audio guide](audio.md) distinguishes DSP benchmarking from physical sync
calibration. Hardware validation remains deferred.
