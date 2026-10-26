# Native renderer hardening report

## Scope and environment

This work establishes and repeats the existing renderer benchmark suite,
hardens selected native buffer boundaries, and splits the renderer into
focused C translation units. It does not change animation algorithms or add
features.

Measurements were collected on Linux x86-64, AMD Ryzen 9 9900X, Python 3.12.15,
NumPy 2.5.4. No Raspberry Pi or ARM64 hardware was available. These are
synthetic in-memory rendering measurements; they do not measure real audio
capture or SPI transmission.

Build the extension from the core package with:

```bash
LUMISTRIPE_BUILD_EXTENSIONS=render uv run --no-sync --with setuptools \
  python setup.py build_ext --inplace --force
```

Run the repeatable measurements from the repository root:

```bash
uv run python packages/lumistripe-core/benchmarks/benchmark_render.py \
  --lengths 150,300,600 --iterations 10000
uv run python scripts/benchmark_animation_catalog.py \
  --renderer both --lengths 150,300,600 --warmup 50 --frames 200
uv run python packages/lumistripe-core/benchmarks/benchmark_layered_compositor.py \
  --lengths 150,300,600 --iterations 1000 --warmup 200 --rounds 3
uv run python packages/lumistripe-core/benchmarks/benchmark_spectrum_flash.py \
  --lengths 150,300,600 --iterations 300 --warmup 50
uv run python packages/lumistripe-core/benchmarks/benchmark_beat_tunnel.py \
  --lengths 150,300,600 --iterations 300 --warmup 50 --rounds 3
```

The benchmark scripts report per-case p50/p95/p99 and effective FPS. Catalog
results include native dispatch hits. Compositor benchmarks also use
`tracemalloc` to estimate peak Python allocation volume per call; it is not a
measurement of total process RSS or native allocator activity.

## Baseline and repeat measurements

`benchmark_render.py` compares the NumPy fallback and native correction plus
alpha scaling. Times are microseconds per call:

| LEDs | NumPy p50 / p95 / p99 | Baseline C p50 / p95 / p99 | Repeat C p50 / p95 / p99 | Final split C p50 / p95 / p99 |
| ---: | ---: | ---: | ---: |
| 150 | 5.500 / 5.571 / 8.907 | 0.361 / 0.371 / 0.380 | 0.341 / 0.351 / 0.391 | 0.361 / 0.371 / 0.381 |
| 300 | 6.642 / 6.743 / 7.093 | 0.581 / 0.591 / 0.601 | 0.551 / 0.561 / 0.571 | 0.541 / 0.561 / 0.571 |
| 600 | 9.006 / 9.097 / 10.039 | 1.002 / 1.022 / 1.032 | 0.942 / 0.952 / 1.022 | 0.932 / 0.962 / 0.972 |

For two-overlay composition, p95 microseconds and traced peak bytes per call
were:

| LEDs | NumPy p95 / bytes | C p95 / bytes | Repeat NumPy p95 / bytes | Repeat C p95 / bytes |
| ---: | ---: | ---: | ---: | ---: |
| 150 | 16.851 / 13,584 | 3.577 / 593 | 16.621 / 13,584 | 3.627 / 593 |
| 300 | 20.047 / 24,984 | 5.160 / 593 | 19.857 / 24,984 | 5.230 / 593 |
| 600 | 25.307 / 47,784 | 8.186 / 593 | 24.726 / 47,784 | 8.055 / 593 |

For the 108 animation/mode cases at each catalog size, native dispatch recorded
200 hits per case. Median p95 speedup over Python was 56.0x at 150 LEDs, 83.4x
at 300, and 102.7x at 600. Two 150/300-LED cases were slower through native
dispatch (RedBlackoutStrobe); no 600-LED case was slower. These results are
synthetic call-path comparisons and do not include hardware transmission.

The SpectrumFlash benchmark at 600 LEDs with two effects recorded baseline
Python/C p95 of 1,898.106/42.339 microseconds. The BeatTunnel baseline recorded
Python/C effect p95 of 4,384.441/37.661 microseconds for its audio workload.
These kernels are already native-dispatched; this work is not claiming a speed
improvement for them.

The repeated microbenchmarks are within small timing variation of baseline.
No statistically meaningful rendering-speed change is expected from input
validation or source-file organization. The repeat results are not a controlled
CPU-frequency or thermal study.

## Changes

- Preserved all 51 methods under `lumistripe.render._native`.
- Split implementation into `_native.c` (module setup), `native_common.c` and
  `native_common.h` (shared validation and color utilities),
  `native_composite.c`, `native_patterns.c`, `native_particles.c`, and
  `native_reactive.c`.
- Native correction and alpha-scaling entry points now reject unsafe partial
  buffer overlap; exact in-place aliases remain supported.
- Fixed-shape native floating-point arrays now reject NaN and infinities before
  use. This covers the tunnel geometry array.
- RainbowStrobe now passes its selected palette and custom color through the
  existing native rainbow kernel, including palette-correct RGB under fully
  transparent pixels. Default rainbow output remains unchanged.
- Added adversarial overlap, exact-alias, and non-finite geometry tests.

## Validation and limits

- Initial focused native renderer suite: 3,357 passed before changes.
- Full core suite with native rendering, expanded length and palette matrix:
  6,153 passed, 168 skipped for palette variants that deliberately use the
  existing Python fallback.
- Pure-Python full core suite: 590 passed, 5,731 native-only tests skipped.
- Focused catalog parity and kernel suite after the translation-unit split:
  2,612 passed and 168 unsupported native palette cases skipped, both normally
  and under UBSan.
- A normal extension build succeeded after the split. Catalog parity covers
  1, 30, 60, 150, 300, 600, and 1,200 LEDs in static and audio modes. Palette
  cases cover each palette setting on the catalog animations; unsupported
  native palette paths are verified to take the Python fallback.
- `graphify update .` completed. It reported existing parser warnings for
  third-party KissFFT headers and `pyproject.toml`.
- Raspberry Pi ARM64 output, real SPI timing, Bluetooth audio, long-duration
  RSS stability, and physical frame-deadline behavior were not measured.

The native-interface audit still needs adversarial coverage across every
array-bearing kernel, including alignment, writeability, numerical ranges,
integer conversions, and cross-buffer aliasing. Transition states and every
possible layered scenario have not all been exercised against the C paths.
