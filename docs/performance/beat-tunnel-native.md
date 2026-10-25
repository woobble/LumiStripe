# Native BeatTunnel benchmark

## Scope

The optional C kernel handles the per-pixel ring calculations and RGBA writes
for `BeatTunnel`. Python retains audio interpretation, animation state, palette
selection, intensity, and scheduling. Builds without the render extension use
the existing Python effect implementation.

The benchmark compares the native kernel with the Python effect while keeping
the existing native pixel correction, alpha scaling, and layered compositor
enabled in both full-renderer cases. It checks final pixels for equality. The
layered workload uses simulated animations and an in-memory `Stripe`; it does
not include audio capture or physical SPI transmission.

## Reproduction

From the repository root on Linux:

```bash
LUMISTRIPE_BUILD_EXTENSIONS=render uv run --with setuptools \
  python packages/lumistripe-core/setup.py build_ext --inplace
UV_CACHE_DIR=/tmp/lumistripe-uv uv run python \
  packages/lumistripe-core/benchmarks/benchmark_beat_tunnel.py \
  --iterations 300 --warmup 40 --rounds 3
```

The benchmark covers static and audio-driven rendering, 150/300/600 LEDs, and
full `LayeredRenderer` cases with zero, one, or two effects. It reports p50,
p95, p99, callback-equivalent FPS, process CPU percentage, 60 Hz deadline
misses, and traced peak Python allocation per call. Python and native final
frames are compared exactly.

## Results

Measured on Linux x86_64, Python 3.12.15, using simulated frames and no
Raspberry Pi hardware. Values below are microseconds per render callback;
allocation values are traced peak bytes per call.

| Workload | Python p50 / p95 / p99 | Native p50 / p95 / p99 | Peak bytes Python → native | p95 reduction |
| --- | ---: | ---: | ---: | ---: |
| Audio effect, 300 LEDs | 2,188 / 2,215 / 2,244 | 9.4 / 9.6 / 11.7 | 2,073 → 1,656 | 99.6% |
| Audio effect, 600 LEDs | 4,400 / 4,587 / 4,890 | 11.6 / 12.3 / 18.0 | 2,073 → 1,656 | 99.7% |
| Audio layered, 150 LEDs, 2 effects | 1,101 / 1,141 / 1,249 | 25.1 / 26.2 / 27.9 | 2,857 → 2,684 | 97.7% |
| Audio layered, 300 LEDs, 2 effects | 2,236 / 2,422 / 2,707 | 29.5 / 31.0 / 36.3 | 2,889 → 2,684 | 98.7% |
| Audio layered, 600 LEDs, 2 effects | 4,423 / 4,690 / 5,158 | 37.0 / 45.0 / 60.3 | 2,889 → 2,684 | 99.0% |

The measured 600 LED two-effect p95 reduction exceeds the 10% integration
threshold. The 150 LED case also improves, with no observed regression. The
isolated audio effect clears the 20% p95 threshold at both 300 and 600 LEDs.
Static workloads showed similar isolated improvements.

The tight callback loop reported approximately 99% process CPU for both
implementations while running. That figure indicates the benchmark process
kept one CPU core busy; it does not estimate idle playback CPU on the target
device. The measured callback durations and allocations provide the comparison
here. No 60 Hz callback deadline misses occurred in these simulated runs.

## Limits

These measurements are not Raspberry Pi results and exclude audio callback
load, full runtime publication, GPIO, and SPI transmission. Repeat the same
benchmark on the target device and compare complete playback before claiming
device-level CPU, latency, or dropped-frame improvements. The C kernel uses
portable scalar C; architecture-specific SIMD was not evaluated.
