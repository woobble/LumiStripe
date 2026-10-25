# Performance Baseline and Bottleneck Analysis

## Summary

This baseline measures commit `db6d139d6c7a2ef279d410fa9f6a3732c2e9cd9c`
(`main`) on a desktop simulation. It establishes reproducible timing and
resource measurements for the runtime worker, animation catalog, audio DSP,
and SPI frame encoder. It does not establish Raspberry Pi performance.

In the unoptimized desktop baseline, dashboard snapshot publication was the
largest consistently measured runtime stage: median per-case p95 was 3.60 ms,
compared with 0.28 ms for output preparation and power limiting. The
x86_64-only follow-up and measured changes are recorded below; the baseline
does not claim Raspberry Pi performance.

## Native optimization assessment

The x86_64 follow-up adds an optional scalar C render extension behind the
NumPy-facing `lumistripe.render.kernels` API. The kernels preserve correction
rounding and floored alpha scaling. A focused 5,000-iteration benchmark showed
lower kernel latency at each tested LED count, while the complete worker
output/power stage remained about 0.27 ms p95. The earlier full-runtime gain
came primarily from reducing state snapshot construction frequency. The
separate measured layered-renderer gain is reported below.

| Area | Measurement evidence | Follow-up result |
| --- | --- | --- |
| Dashboard publication | Before optimization, `_publish()` p95 median was 3.53 ms per call and ran every render step. It now runs at the dashboard's 4 Hz cadence, with command-triggered snapshots remaining immediate. | Kept state snapshot construction in Python and decoupled it from render cadence. |
| Pixel correction and output preparation | `_flush_power_limited_outputs()` p95 median was 0.266 ms before and 0.274 ms after; worker-owned source, corrected, scaled, and preview buffers are reused. | Added an optional native path. Scalar kernels beat NumPy in isolation, but the full output stage did not show a material x86_64 improvement. |
| Power estimation and limiting | The estimator still uses float64 NumPy operations, and Python still selects the shared/per-output cap. C applies the exact integer RGB correction and alpha scale after the cap is selected. | Kept budget policy and power estimation in Python; output semantics are covered by exact comparison tests. |
| Zone mapping | `ZoneMappingController` reuses its expanded buffer; `ScaledMultiController.output_pixels_into()` maps into caller-owned buffers while the existing allocating API remains unchanged. | Kept NumPy mapping; no native zone kernel was justified. |
| Animation and effects | The slowest catalog case on this host, `beat_tunnel` at 600 LEDs, measured p95 4.161 ms against an 18 ms budget. | Kept animation orchestration in Python; no catalog effect justified a native rewrite. |
| SPI encoding | At 600 LEDs, native encoding p50 is 3.61 µs versus 15.26 µs for the Python fallback, while estimated wire time at 1 MHz is 15.656 ms. Physical transfer was unavailable. | Kept the existing native encoder; no additional SPI optimization was measured on this host. |
| Audio DSP | Native DSP measured 448–626× real-time headroom on this x86_64 host. Microphone and Bluetooth callback/DSP load were not measured. | Kept the existing audio extension; no DSP change was supported by the available measurements. |

The extension uses C11 and the NumPy C API behind a Python fallback. The
`render` build profile is optional and included by `all`; `none` remains pure
Python. It was compiled and benchmarked on x86_64 only. ARM64 and Raspberry Pi
performance remain unverified, and NEON was not added. Raspberry Pi execution
is outside this implementation and acceptance scope.

### Layered compositor experiment

`LayeredRenderer` now sends the base frame and up to two already-budgeted
overlays through a fused C screen compositor when the optional render module is
available. The Python path remains the reference fallback. Effect scheduling,
rendering, transition behavior, and the overlay strength budget remain in
Python. The benchmark uses fixed synthetic layers, reusable outputs, 300
warmup calls, and 6,000 measured samples per case.

| LEDs | Compositor p95, NumPy → C (µs) | Full `LayeredRenderer` p95, NumPy → C (µs) |
| ---: | ---: | ---: |
| 150 | 17.132 → 3.877 (77.4% lower) | 13.586 → 11.862 (12.7% lower) |
| 300 | 20.989 → 5.531 (73.6% lower) | 15.619 → 9.377 (40.0% lower) |
| 600 | 25.838 → 8.486 (67.2% lower) | 18.424 → 10.911 (40.8% lower) |

These are two-overlay p95 comparisons from the paired benchmark run. The
600-LED compositor p50/p95/p99 was 25.298/25.838/39.214 µs for NumPy and
8.376/8.486/12.002 µs for C. At 600 LEDs, the separately traced transient
peak was 47,840 bytes per compositor call for NumPy and 705 bytes for C; the
whole renderer measured 34,000 and 1,545 bytes, respectively. Tracing measures
Python-visible temporary peaks and adds overhead; it is not cumulative
allocation traffic or RSS. Pixel outputs were compared exactly across the
fallback and native paths.

The tight benchmark loops used approximately 99–100% of one logical CPU for
both implementations. That confirms the measured callbacks are CPU-bound; it
is not a forecast of appliance-wide CPU utilization during scheduled playback.

The C implementation passed the planned gates on this host: at least 20%
compositor p95 improvement at 300 and 600 LEDs, at least 10% whole-renderer
p95 improvement for 600 LEDs with two overlays, and no 150-LED p95 regression.
This simulated result does not include actual animations' effect-generation
cost, audio capture, SPI transmission, or ARM64 behavior.

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
  use `perf_counter_ns` and add small timing overhead. Runs of 10 seconds or
  longer retain at most the latest 512 stage samples per stage; total frame and
  deadline counts remain cumulative.
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

## x86_64 optimization comparison

The before run used revision `4830a8e` with the benchmark instrumentation and
the original per-frame publication behavior. The after run used the same host,
Python version, 72-case matrix, warmup, and sample duration. Both runs used
simulated virtual buffers with no physical audio or LED hardware.

| Metric | Before | After | Notes |
| --- | ---: | ---: | --- |
| Full `_step()` median of per-case p50 / p95 / p99 | 3.791 / 5.033 / 24.325 ms | 1.032 / 3.508 / 4.244 ms | p95 down 30%; p99 down 83%. |
| `_publish()` median per-call p95 | 3.525 ms | 3.062 ms | It now ran six times per 1.5-second case (4 Hz), rather than on every frame. |
| Output and power stage median per-case p95 | 0.266 ms | 0.265 ms | No material latency change; the C result is reported separately below. |
| Process CPU, one-core scale, median / maximum | 18.65% / 24.97% | 6.15% / 13.52% | Median down 67%. |
| Effective FPS, static / dynamic median | 50.00 / 36.00 | 58.67 / 44.00 | Same virtual output workloads. |
| Missed deadlines | 139 / 4,680 frames | 0 / 5,502 frames | No miss in this short matrix is not a guarantee under longer or physical load. |
| Median RSS start / end across cases | 73.57 / 73.58 MB | 73.52 / 73.53 MB | RSS stayed nearly flat after the process had warmed up. |

Frame-derived publication now follows the existing WebSocket limits: dashboard
state at 4 Hz, audio telemetry at up to 15 Hz, and preview snapshots at up to
30 Hz. The worker remains single-writer, command snapshots are immediate, and
the completion-relative animation scheduler was left unchanged.

The native microbenchmark used a non-identity RGB correction and an alpha scale
of 0.63, with reusable buffers and 5,000 iterations per size. At 150, 300,
and 600 LEDs, respectively, C p50 was 0.321, 0.521, and 0.892 μs; the NumPy
fallback p50 was 5.591, 6.853, and 9.177 μs. This microbenchmark measures the
two kernels and Python-to-C dispatch, not power estimation, topology mapping,
or SPI transfer. In the full pipeline those other stages dominate, so the
measured `_flush_power_limited_outputs()` latency remained effectively flat.

The 60-second 600-LED independent-layout dynamic soak measured 49.43 FPS,
12.73% one-core CPU, and 8 missed deadlines out of 2,966 frames (0.27%). Its
full-step p50/p95/p99 over the retained timing window was 2.46/4.84/6.31 ms.
RSS was 67.90 MB before runtime initialization and 73.03 MB at the first
post-warmup sample; it reached 73.67 MB while bounded timing buffers filled,
then stayed at 73.67 MB for the final 20 seconds. Long runs retain only the
latest 512 timing samples per stage while counting all frames, so the sampling
harness has bounded memory. This 60-second host run is not a long-duration

### Original baseline details

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
output, Dynamic playback, and a 15 W cap recorded 1.62 MB current traced
memory, 2.94 MB peak traced memory, and 11,501 retained traced blocks. These
are live memory and retained-block measurements, not cumulative allocation
traffic. Tracing materially changed performance: for the same case, full-step
p95 rose from 3.95 ms to 16.30 ms and process CPU from 7.90% to 34.05%. Do not
use the traced run's timing, FPS, CPU, or RSS as baseline performance figures.

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

## Findings and next work

1. **The main desktop gain came from publication cadence.** Keep control
   snapshots immediate and retain the separate 4 Hz state, 15 Hz telemetry,
   and 30 Hz preview limits. The matrix had no dashboard WebSocket client, so
   browser and network cost is not included.
2. **Keep C acceleration scoped to measured pixel work.** The fused layered
   compositor improved the complete synthetic `LayeredRenderer` case, while
   the web worker's separate output and power stage remained below 0.4 ms p95
   and did not materially improve from the earlier correction and alpha
   kernels. The optional build and NumPy fallback remain important for
   portability.
3. **Keep the completion-relative scheduler semantics for now.** The desktop
   matrix improved effective FPS and missed deadlines after publication work
   was removed, but the scheduler was intentionally unchanged to avoid altering
   animation timing without a separate behavior review.
4. **Audio and physical transport remain separate hardware cases.** Synthetic
   DSP results do not include live capture, Bluetooth routing, SPI transfer,
   or audio-to-LED latency; no such device measurements were run for this
   x86_64-only scope.

## Reproduction

Run the simulated runtime matrix and save its raw JSON outside the checkout:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python scripts/benchmark_runtime_pipeline.py \
  --warmup 0.5 --duration 1.5 --power-budget-watts 15 \
  --output /tmp/lumistripe-runtime-baseline.json
```

Run the 60-second high-load memory sample with bounded timing retention:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python scripts/benchmark_runtime_pipeline.py \
  --led-counts 600 --layouts independent --zone-counts 4 \
  --modes dynamic --power-limits on --warmup 1 --duration 60 \
  --output /tmp/lumistripe-runtime-soak.json
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

Run the native render kernel microbenchmark separately from the full runtime:

```bash
LUMISTRIPE_BUILD_EXTENSIONS=render UV_CACHE_DIR=/tmp/lumistripe-uv uv sync \
  --locked --all-extras --dev --reinstall-package lumistripe-core
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python packages/lumistripe-core/benchmarks/benchmark_render.py \
  --iterations 5000
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python packages/lumistripe-core/benchmarks/benchmark_layered_compositor.py \
  --iterations 2000 --warmup 300 --rounds 3
```

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
