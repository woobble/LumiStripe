# Performance Baseline and Bottleneck Analysis

## Summary

This baseline measures commit `db6d139d6c7a2ef279d410fa9f6a3732c2e9cd9c`
(`main`) on a desktop simulation. It establishes reproducible timing and
resource measurements for the runtime worker, animation catalog, audio DSP,
and SPI frame encoder. It does not establish Raspberry Pi performance.

The strongest desktop candidate for follow-up is dashboard snapshot
publication: its median per-case p95 was 3.60 ms, compared with 0.28 ms for
output preparation and power limiting. Treat this as a hypothesis for Pi
verification. No optimization is implemented as part of this baseline.

## Environment and method

- Host: Linux 7.2.11, x86_64, AMD Ryzen 9 9900X, 24 logical CPUs, Python
  3.12.15.
- Runtime cases used virtual pixel buffers, two simulated outputs, and no
  physical audio or LED devices. Static playback used audio off; Dynamic
  playback used the synthetic demo source.
- The runtime matrix covered 150, 300, and 600 total LEDs; mirrored,
  continuous, and independent layouts; one and four zones per output; static
  and Dynamic playback; and power limiting off and on. Four-zone outputs used
  the alternate reversal pattern. Each case warmed up for
  0.5 seconds and measured for 1.5 seconds.
- The runtime harness measured scheduled frame steps, output preparation,
  audio telemetry, preview capture, and dashboard publication. Stage probes
  use `perf_counter_ns` and add small timing and sample-list overhead.
- CPU is process CPU time divided by wall time, expressed as a percentage of
  one fully occupied logical CPU. RSS values are process resident memory; the
  matrix runs cases sequentially in one process, so RSS growth includes
  retained interpreter and library memory.
- Percentiles use nearest-rank samples. Per-case p99 values are sensitive to
  occasional host scheduling stalls, especially with roughly 70 measured
  frames per case.

### Runtime results

The table reports the median of each per-case percentile across all 72 cases;
the final columns show the largest per-case p95 and p99.

| Stage | Median p50 (ms) | Median p95 (ms) | Median p99 (ms) | Max p95 (ms) | Max p99 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full `_step()` | 3.818 | 5.005 | 25.480 | 6.934 | 35.654 |
| `_publish()` | 2.659 | 3.596 | 24.236 | 4.773 | 35.048 |
| Output and power preparation | 0.182 | 0.283 | 0.358 | 0.353 | 0.638 |
| Audio telemetry | 0.075 | 0.112 | 0.159 | 0.162 | 0.395 |
| Preview capture | 0.001 | 0.002 | 0.002 | 0.003 | 0.022 |

The preview capture method is fast in this path because output preparation
already provides the immutable preview bytes. Output preparation timing
includes creating those bytes.

Static playback produced 46.0–50.7 FPS (median 49.3); Dynamic playback produced
34.0–41.9 FPS (median 37.0). The worker reported effective frame intervals of
16–20 ms. The current loop schedules the next frame after the step completes,
then waits the requested interval, so render time contributes to the observed
frame period. Across the matrix, 142 of 4,652 measured frames missed the
existing deadline diagnostic (3.1%); the highest case rate was 3 of 70 frames
(4.3%).

Process CPU use had a 19.0% median and 25.8% maximum on the one-core scale.
RSS ranged from 67.75 MB at the start of a case to 73.64 MB at the end of the
matrix; the largest within-case increase was 5.02 MB. This is not an extended
playback leak test.

An allocation-traced run of 600 LEDs, independent layout, four zones per
output, Dynamic playback, and a 15 W cap recorded 1.54 MB current traced memory,
3.10 MB peak traced memory, and 10,530 retained traced blocks. These are live
memory and retained-block measurements, not cumulative allocation traffic.
Tracing materially changed performance: for the same case, full-step p95
rose from 5.68 ms to 27.49 ms and process CPU from 19.7% to 55.0%. Do not use
the traced run's timing, FPS, CPU, or RSS as baseline performance figures.

### Subsystem results

- **Animation catalog:** 324 measurements across 150, 300, and 600 LEDs, static
  and audio-reactive ticks. The slowest sampled case was `beat_tunnel` at 600
  LEDs in audio mode: p50 4.000 ms, p95 4.161 ms, p99 4.679 ms against an
  18 ms catalog budget.
- **Audio DSP:** Feeding 47.55 seconds of synthetic audio took 0.076–0.106
  seconds on this host, or 448–626× real-time headroom. At 16 hops per call,
  feed latency was p50 0.291 ms, p95 0.325 ms, and p99 0.428 ms.
- **SPI encoding:** At 600 LEDs, native encoding was p50 3.61 µs, p95 3.80 µs,
  and p99 5.02 µs. The Python fallback was p50 15.26 µs, p95 16.19 µs, and
  p99 24.14 µs. The estimated wire time at 1 MHz was 15.656 ms. No physical
  SPI transfer was available on this host.

## Ranked findings and next measurements

1. **Measure `_publish()` on the Pi first.** It was the largest consistently
   measured runtime stage in simulation. Separate snapshot construction from
   preview byte preparation if further attribution is needed; keep immediate
   control-change publication intact when evaluating any cadence change.
2. **Compare effective FPS and deadline tails on the Pi.** The desktop results
   show the cost of the current completion-relative scheduling semantics and
   occasional p99 stalls. Repeat with the same animation, output setup, and
   CPU configuration before changing frame timing.
3. **Do not prioritize simulated output-copy or power-limit work yet.** Their
   measured p95 was below 0.4 ms through 600 LEDs. Verify with physical SPI and
   power settings on the target before drawing a hardware conclusion.
4. **Treat DSP headroom as host-only evidence.** This synthetic feed test does
   not include Bluetooth capture, PipeWire routing, callback scheduling, or
   audio-to-LED latency. The new input-capture probe measures callback duration
   on a real input device.

## Reproduction

Run the simulated runtime matrix and save its raw JSON outside the checkout:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python scripts/benchmark_runtime_pipeline.py \
  --warmup 0.5 --duration 1.5 --power-budget-watts 15 \
  --output /tmp/lumistripe-runtime-baseline.json
```

Run a separate allocation-traced representative case. Use only its
`allocations` object for allocation analysis:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python scripts/benchmark_runtime_pipeline.py \
  --led-counts 600 --layouts independent --zone-counts 4 \
  --modes dynamic --power-limits on --power-budget-watts 15 \
  --warmup 1 --duration 3 --trace-allocations \
  --output /tmp/lumistripe-runtime-allocations.json
```

Run the existing and extended subsystem benchmarks:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python scripts/benchmark_animation_catalog.py \
  --lengths 150,300,600 --warmup 20 --frames 100
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python scripts/benchmark_audio_dsp.py --iterations 4096
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python packages/lumistripe-core/benchmarks/benchmark_spi.py \
  --iterations 10000
```

On a Pi with an active audio input, measure capture callback timing and health:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python scripts/benchmark_audio_capture.py \
  --device "USB Mic" --warmup 2 --duration 20
```

For physical SPI encoding plus transfer timing, run with the target device path.
The benchmark sends repeated black frames and clears the output on exit:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python packages/lumistripe-core/benchmarks/benchmark_spi.py \
  --device /dev/spidev0.0 --speed 1000000 --iterations 10000 \
  --transfer-iterations 20
```

For the real scheduled runtime on a Pi, explicitly enable hardware output and
set the configured SPI devices, power cap, and audio source to match the test
setup. This writes benchmark frames to both outputs:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --spi-speed-hz 1000000 --audio-source bluetooth \
  --power-limits on --power-budget-watts 15 \
  --warmup 2 --duration 10 \
  --output /tmp/lumistripe-pi-runtime-baseline.json
```

Use `scripts/benchmark_audio_sync_output.py --sink <PipeWire-sink>` separately
to measure the existing calibration tone callback timing and route health.

## Limitations

- This host is x86_64 and has no accessible `/dev/spidev*` device or working
  ALSA input. The microphone capture benchmark failed to open the host's
  `jack` input with ALSA error `-5`; Bluetooth audio and the sync-output
  benchmark were not run.
- Physical SPI transfer time, Raspberry Pi CPU/RSS, Bluetooth capture/DSP,
  physical multi-zone LEDs, long-duration memory stability, and audio-to-LED
  latency remain unmeasured.
- The runtime benchmark exercises server-side snapshot and preview publication
  work but has no WebSocket clients attached. Browser rendering, network
  delivery, and React subscription behavior are outside this baseline.
- Desktop measurements are not comparable to Pi acceptance measurements.
  Repeat on the same target device with matching CPU configuration, SPI speed,
  LED count, animation, zones, power limit, and preview settings.
