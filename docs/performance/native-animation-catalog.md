# Native animation renderer comparison

## Dispatch policy

`AnimationPlayer` and `EffectLayerRenderer` use the native animation renderer
when the animation opts in, its native kernel is available, and its configured
pixel threshold is met. The default threshold is 150 pixels. `RedBlackoutStrobe`
uses 600 pixels because its vectorized Python fill is faster at the smaller
measured sizes. Below each threshold, dispatch uses the Python implementation.
The Python implementation also remains the fallback when a kernel is unavailable
or a renderer rejects the current configuration.

The thresholds are based on paired catalog measurements on the host below.
Across 108 animation/mode pairs per size, native rendering had worse p95 in 19
pairs at 1 pixel, 2 at 60 pixels, 2 at 150 pixels, 2 at 300 pixels, and none
at 600 pixels. All regressions from 60 through 300 pixels were
`RedBlackoutStrobe`; its native fill was faster at 600 pixels in both modes.
Across the full catalog, median p95 speedup was 56.5× at 150 pixels, 82.8× at
300, and 104.6× at 600. The other 53 renderers had no p95 regressions at those
sizes. Tiny absolute timings and a fast desktop make the ratios host-specific.
Rebenchmark these thresholds on deployment hardware before changing them for
another CPU.

NeonStorm and ElectricStorm currently use the native kernel for the default
rainbow palette and fall back to Python for neon, fire, and custom palettes.
Those palette paths are kept on Python because the current C palette
transformation did not match the reference pixels exactly.

## Reproduction

Build the optional render extension from the repository root:

```bash
LUMISTRIPE_BUILD_EXTENSIONS=render UV_CACHE_DIR=/tmp/lumistripe-uv-cache \
  uv run --directory packages/lumistripe-core --with setuptools \
  python setup.py build_ext --inplace
```

Then run the paired benchmark:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python \
  scripts/benchmark_animation_catalog.py \
  --renderer both --lengths 1,60,150,300,600 --warmup 50 --frames 200
```

The output has one row per party catalog renderer, LED count, playback mode,
and renderer implementation. It reports p50/p95/p99 frame time, effective FPS,
native-kernel hits, and the catalog frame budget. Native mode deliberately
forces the renderer method, bypassing the runtime size gate so the gate can be
evaluated. The Python and native instances receive the same static/audio
workload, but maintain independent animation state. For higher-resolution
measurements, use `--warmup 50 --frames 200`. Store CSV output when comparing
machines.

## Recorded run

- Date: 2026-10-26
- Host: Linux 7.2.11, x86_64, AMD Ryzen 9 9900X, Python 3.12.15
- Render extension: C11 build, `LUMISTRIPE_BUILD_EXTENSIONS=render`
- Catalog size: 54 animations and effects
- Samples: 50 warmup and 200 measured frames per row; static and audio modes
- LED counts: 1, 60, 150, 300, and 600
- Hardware: virtual pixel buffers; no SPI device or Raspberry Pi used
- Native hits: 200/200 samples for all catalog entries, modes, and sizes in the
  forced-native run (default settings)

| LEDs | Python/native comparisons | Native slower | Median p95 speedup | Minimum p95 speedup |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 108 | 19 | 1.5× | 0.40× |
| 60 | 108 | 2 | 28.8× | 0.76× |
| 150 | 108 | 2 | 56.5× | 0.82× |
| 300 | 108 | 2 | 82.8× | 0.90× |
| 600 | 108 | 0 | 104.6× | 1.08× |

This measures renderer work, not audio capture, output preparation, SPI
transmission, or end-to-end playback latency. It does not establish equivalent
performance on ARM64.
