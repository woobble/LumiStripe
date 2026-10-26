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
- Shared array validation now checks dtype, rank, fixed dimensions, alignment,
  C-contiguity, and writability before direct buffer access. Finite-value and
  range checks cover mutable particle levels and fixed/active geometry used by
  the hardened kernels. Scratch arrays with spare capacity validate only the
  active prefix so intentionally uninitialized inactive slots remain valid.
- Mutable state buffers are checked for overlap with output and with other
  state buffers where the kernel reads and writes them in the same call.
- NeonConfetti validates palette indices before indexing its seven-color table;
  its frame-center multiplication now reduces the frame before multiplying to
  avoid signed overflow.
- Added adversarial cases for misalignment, read-only output, invalid mutable
  state, negative palette indices, active-prefix validation, and state/output
  overlap.
- RainbowStrobe now passes its selected palette and custom color through the
  existing native rainbow kernel, including palette-correct RGB under fully
  transparent pixels. Default rainbow output remains unchanged.
- Added adversarial overlap, exact-alias, and non-finite geometry tests.

## Acceptance follow-up

- Native extension exports: 51 callable functions, unchanged.
- Full native release core suite after the validation additions: 6,162 passed,
  168 skipped. The skips are native palette combinations that deliberately
  dispatch through Python. The isolated native build rendering, parity, and
  spectrum suite passed 2,718 tests, with 168 unsupported native palette cases
  skipped. Final UBSan and
  ASan focused native boundary suites each passed 31 tests.
- ASan had to be
  preloaded into the project Python directly; preloading it into `uv` crashed
  in the runner's jemalloc before Python started. Leak detection was disabled,
  so this does not verify Python reference leaks.
- No-render isolated profile build (`audio,gpio,spi`, without `render`): 590
  passed, 5,740 skipped. Every skip reports
  that the render extension/kernel is not
  built; these skips are not counted as fallback coverage. A representative
  playback/render subset ran without the extension: 120 passed, 29 native-only
  skips. Existing layer/playback tests exercise zero, one, and two effects,
  Crossfade, FadeBlack, Wipe, and independent playback-engine transition state.
- Catalog parity covers all supported catalog animation/palette cases at
  1, 30, 60, 150, 300, 600, and 1,200 LEDs for 12 consecutive static and audio
  frames. The 168 unsupported native palette combinations use and test the
  existing Python fallback. Exact RGBA byte equality is asserted when a native
  kernel is available. Layered playback is separately covered by the core
  compositor and transition tests; no physical LED zone or mirror wiring was
  available for hardware-level validation.
- Final serial benchmark samples on x86-64 (Python 3.12.15, NumPy 2.5.4):

  | LEDs | Correction+alpha C p50 / p95 / p99 (µs) | NumPy p95 (µs) | Two-overlay compositor C p95 (µs) | NumPy p95 (µs) |
  | ---: | ---: | ---: | ---: | ---: |
  | 150 | 0.371 / 0.381 / 0.391 | 6.111 | 8.135 | 20.779 |
  | 300 | 0.561 / 0.571 / 0.681 | 8.926 | 9.117 | 15.018 |
  | 600 | 0.932 / 1.222 / 1.583 | 10.680 | 10.910 | 18.184 |

  Catalog and layered benchmarks completed at 150/300/600 LEDs in static and
  audio modes, with 0/1/2 effects. Native calls stayed within frame budgets;
  results were consistent with the earlier baseline and showed no broad
  sustained regression. These are synthetic in-memory measurements, not a
  statistical CPU-frequency/thermal study. Benchmark JSON/CSV is generated to
  stdout by the existing scripts and was not committed as a large artifact.
- `ruff check .`, core `mypy`, and `git diff --check` pass. `graphify update .`
  completed with the existing KissFFT parser and pyproject warnings.
- The final rerun of `test_render_kernels.py`, `test_catalog_native_parity.py`,
  and `test_spectrum_flash_native.py` passed 2,718 tests with 168 expected
  fallback skips; focused checks emitted only project coverage warnings about
  unrelated packages not imported by this core-only selection.
- Separate `setup.py build` output trees were produced for `audio,gpio,spi` and
  `render,gpio,spi`. The no-render build has no `_native` shared object; the
  native build exports all 51 methods. The representative fallback suite passed
  against the no-render tree, and 2,718 focused native tests passed against the
  native tree.
- No Raspberry Pi or ARM64 test ran. Real SPI transfer, Bluetooth capture,
  long-duration RSS stability, physical zone mapping, and appliance frame
  deadlines remain unmeasured.

## Remaining native safety audit

The machine-readable entry inventory is
[`native-render-safety-audit.json`](native-render-safety-audit.json). It lists
all 51 methods registered by the four native method tables. Source inspection
and targeted fixes in this pass addressed extreme palette hue conversion,
ElectricStorm geometry casts, and signed frame/index products in reactive
kernels. The full core suite confirms normal rendering parity after these
changes.

The final validation on x86-64 Python 3.12 was: full native core suite **6,164
passed, 168 skipped**; focused kernel/catalog/SpectrumFlash parity **2,720
passed, 168 skipped**; targeted UBSan **4 passed**; targeted ASan **4 passed**
with leak detection disabled; and isolated no-render playback/render tests
**228 passed, 31 native-only skipped**. The native extension exports 51
functions. Ruff, core mypy, and `git diff --check` pass.

GCC `-fanalyzer` built the extension and reported no warnings in project C
sources; its warnings were confined to NumPy's C API headers.

Preloading ASan into the `uv` launcher crashed inside its jemalloc before Python
started. Preloading ASan into the project Python directly succeeded. The ASan
result therefore covers only those selected Python tests and does not include
Python reference leak detection. No ARM64 or physical hardware validation was
performed.

This audit is still marked incomplete: per-expression proofs are outstanding
for all scalar conversions and signed/unsigned arithmetic across every entry,
allocation-failure injection was not performed for Python reference paths, and
the full invalid-input matrix was not executed separately for each method.
Sanitizers exercise selected boundary cases only. The inventory records these
remaining risks for each entry rather than implying sanitizer coverage proves
memory safety. No Raspberry Pi or physical-output tests ran.
