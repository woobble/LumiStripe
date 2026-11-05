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

## Web engine IPC and direct integration comparison

A one-off mock-output comparison was run on 2026-11-05 against the web and
engine binaries from the pre-change `HEAD` and the in-process web build. Both
used locked dependencies, Rust 1.98.1, the development profile, one 80-pixel
mock output, demo audio, and a 60 FPS cycling scene. The IPC case used the
standalone engine child; the direct case used one web process with its embedded
runtime. This ran on an x86_64 Arch Linux development machine with a Ryzen 9
9900X and 24 logical CPUs. The measurements are local observations, not device
or release-build guarantees.

| Measurement | IPC baseline | In-process |
| --- | ---: | ---: |
| `GET /api/health`, p50 / p95 | 0.318 / 0.524 ms | 0.268 / 0.460 ms |
| `GET /api/state`, p50 / p95 | 0.479 / 0.649 ms | 0.403 / 0.619 ms |
| Animation scene acknowledgement, p50 / p95 | 5.048 / 5.367 ms | 0.725 / 0.892 ms |
| State WebSocket revision after scene command, p50 / p95 | 5.056 / 5.544 ms | 0.862 / 1.033 ms |
| Audio WebSocket message interval, p50 / p95 | 65.970 / 71.360 ms | 66.948 / 68.033 ms |
| Preview WebSocket message interval, p50 / p95 | 35.097 / 36.056 ms | 33.234 / 34.582 ms |
| CPU during 20 health/state GET pairs per second | 6.18% of one core | 4.39% of one core |
| Average process-tree RSS during that load | 42.62 MiB | 36.20 MiB |
| Render output rate | 59.996 FPS | 59.997 FPS |
| Scheduler deadline misses | 0 | 0 |

The scene acknowledgement sample used 60 alternating animation updates. The
state WebSocket sample continuously issued updates for five seconds and waited
for the published revision to reach each REST acknowledgement; it delivered
958 updates in the IPC case and 4,882 in the direct case. Audio and preview
intervals used 40 received messages each. CPU and RSS were sampled for five
seconds with demo audio and rendering active; the old measurement includes
both the web process and its engine child, while the direct measurement has one
process. Both cases reported one dropped frame during the full run.

This run shows lower control acknowledgement latency for the direct path.
Audio and preview delivery stayed near their existing 15 Hz and 30 Hz cadence,
and both configurations rendered at 60 FPS without scheduler deadline misses.
The CPU and memory figures are a single short debug-build sample; they do not
establish a general resource reduction. No rendering performance improvement
is claimed.
