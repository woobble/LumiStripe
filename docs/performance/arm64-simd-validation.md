# ARM64 SIMD validation

## Scope and machine

This pass added an ARM64 Advanced SIMD implementation for the native RGBA
color-correction kernel and checked the native extension profile for Raspberry
Pi 4 and Pi 5 compiler targets. The validation host was x86-64 Linux. It had
`aarch64-linux-gnu-gcc` 16.2.0, GNU binutils 2.47, QEMU user emulator 11.1.2,
and the `/usr/aarch64-linux-gnu` sysroot. The native Pi benchmark results
below were collected separately on `rpi02`; its exact Pi model was not
included with the output.

The cross sysroot has no AArch64 Python installation, so an isolated AArch64
CPython 3.12.16 runtime and NumPy 2.5.4 were assembled under `/tmp`. The full
extension profile was cross-compiled against those target Python and NumPy
headers for both Cortex-A72 and Cortex-A76. All four AArch64 extension modules
then imported successfully in that target runtime under QEMU for both CPU
models. The temporary build outputs use setuptools' host-tagged filenames;
they were renamed only in the temporary runtime to exercise the target ABI and
are not installable wheels.

## Renderer kernel

`native_pixel.c` provides a scalar implementation and an AArch64 implementation
for `correct_rgba_into`. On AArch64, the dispatched path processes 16 RGBA
pixels at a time with `vld4q_u8` and `vst4q_u8`. RGB products are widened to
16-bit lanes and divided exactly by 255 using integer shifts and additions;
alpha is copied unchanged. Remaining pixels use the scalar implementation.
The wrapper still validates array layout, channel gains, and overlap before it
calls the helper, including for exact in-place operation.

The division identity stays within 16-bit unsigned range for all supported
inputs (`0..255` channel values and gains), and no fast-math flags are used.
Non-AArch64 builds and AArch64 builds with
`LUMISTRIPE_DISABLE_RENDER_NEON=1` use the scalar function.

The benchmark harness verifies all 256 gains against all 256 source values,
including unchanged alpha, then compares scalar and dispatched correction,
alpha-scaling, and screen-composition calls at 600, 4,096, and 16,384 pixels.
The Python regression tests cover vector boundaries for all three kernels.

### Reactive effect candidates

`native_reactive.c` has scalar references and AArch64 NEON candidates for
Aurora, PlasmaRave, and BeatRipple. BeatRipple vectorizes per-pixel distance
and intensity calculations; Aurora and PlasmaRave vectorize final alpha
scaling while keeping trigonometry and palette conversion scalar. The
candidates are exposed only through private benchmark entry points for now;
production rendering stays scalar until a Raspberry Pi run confirms a
repeatable speedup.

Build the render extension on the Pi from the repository root:

```bash
LUMISTRIPE_BUILD_EXTENSIONS=render UV_CACHE_DIR=/tmp/lumistripe-uv-cache \
  uv run --directory packages/lumistripe-core --with setuptools \
  python setup.py build_ext --inplace --force
```

Run all three candidates at the established strip sizes:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python \
  packages/lumistripe-core/benchmarks/benchmark_reactive_simd.py 10000
```

The script verifies exact scalar/candidate parity before timing each effect
in static and audio modes. It runs three rounds in alternating order and
reports the median timing, ratio, and output checksum. Share the output to
decide which candidates should become production dispatch paths.

## Compiler and instruction checks

The `none`, `audio`, `gpio`, `spi`, `render`, and `all` build profiles remain
opt-in and portable by default. The Pi installer and updater now set
`LUMISTRIPE_NATIVE_OPTIMIZATION=1` when rebuilding on the device. The setup
configuration then enables `-O3`, LTO, and the compiler's native CPU selection
for that Pi. This keeps general distribution wheels portable.

The AArch64 compiler accepted `-mcpu=cortex-a72` (Pi 4) and
`-mcpu=cortex-a76` (Pi 5). Full `all` profiles for both targets produced
ELF64 AArch64 shared objects for render, audio, GPIO memory, and SM16716/SPI.
Those cross-extension smoke builds used the package's portable `-O2` level
with an explicit target CPU. The dedicated kernel benchmark and vectorization
inspection used `-O3`; on-device installer builds select `-O3` through the
native optimization setting.
The render objects contain `ld4`, `umull`, `usra`, `uzp2`, and `st4` Advanced
SIMD instructions. The benchmark executed that path under QEMU with
`-cpu cortex-a72` and `-cpu cortex-a76`; its checksums matched the scalar
reference in both runs. An AArch64 build with `LUMISTRIPE_DISABLE_RENDER_NEON=1`
also passed the byte-domain parity check.

GCC's `-fopt-info-vec-all` report for `native_pixel.c` shows the scalar loop
auto-vectorizing to 16-byte vectors at `-O3`; the explicit intrinsic loop is
reported as non-vectorizable because its structured load/store intrinsics are
memory operations. The disassembly confirms the emitted instructions directly.
The AArch64 audio report shows 16-byte vectorized loops in `_audio.c`, including
windowing and magnitude work in `_process_fft`. Its floating-point reductions
and the vendored KISS FFT butterfly code remain scalar in this build; no
fast-math mode was enabled to force reassociation. GPIO register access and
SM16716 bit serialization also remain scalar because their operations are
ordered hardware I/O and bit packing, not independent numeric lanes.

## QEMU benchmark results

Each figure below is one 1,000-iteration run. CPU utilization is process CPU
time divided by wall time. The first scalar baseline disables GCC automatic
vectorization so the table isolates the explicit NEON path. The second scalar
baseline uses normal `-O3`, where GCC's report says the scalar loop is
auto-vectorized. Both benchmark binaries use identical input and correction
values and produced identical checksums.

These are QEMU measurements on an x86-64 host. QEMU's instruction translation
does not reproduce Cortex-A72/A76 throughput, frequency scaling, cache behavior,
or thermal limits. The ratios are useful as a functional benchmark comparison
only and do not predict Raspberry Pi speedups.

| CPU model | Pixels | Scalar baseline | Scalar ms | NEON dispatch ms | Ratio | CPU use scalar / dispatch |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| Cortex-A72 | 600 | auto-vectorization off | 2.433 | 1.518 | 1.60x | 100.7% / 96.8% |
| Cortex-A72 | 4,096 | auto-vectorization off | 16.412 | 10.058 | 1.63x | 98.2% / 97.8% |
| Cortex-A72 | 16,384 | auto-vectorization off | 64.854 | 40.285 | 1.61x | 98.5% / 98.2% |
| Cortex-A76 | 600 | auto-vectorization off | 2.279 | 1.430 | 1.59x | 101.9% / 97.7% |
| Cortex-A76 | 4,096 | auto-vectorization off | 15.348 | 9.439 | 1.63x | 99.4% / 99.2% |
| Cortex-A76 | 16,384 | auto-vectorization off | 61.575 | 37.799 | 1.63x | 99.2% / 99.3% |
| Cortex-A72 | 600 | normal `-O3` scalar | 7.905 | 1.517 | 5.21x | 99.0% / 98.5% |
| Cortex-A72 | 4,096 | normal `-O3` scalar | 52.429 | 10.135 | 5.17x | 98.3% / 98.1% |
| Cortex-A72 | 16,384 | normal `-O3` scalar | 210.906 | 40.527 | 5.20x | 98.1% / 99.4% |
| Cortex-A76 | 600 | normal `-O3` scalar | 8.287 | 1.412 | 5.87x | 100.7% / 100.0% |
| Cortex-A76 | 4,096 | normal `-O3` scalar | 56.334 | 9.645 | 5.84x | 98.8% / 98.0% |
| Cortex-A76 | 16,384 | normal `-O3` scalar | 227.103 | 42.118 | 5.39x | 97.8% / 92.9% |

The final screen-composition harness also ran under QEMU for 500 iterations.
The emulator ratios were about 0.82x on both CPU models, despite the strong
native Pi gain below. This confirms QEMU timings are not a useful proxy for
screen-kernel performance on the target hardware.

| CPU model | 600 pixels | 4,096 pixels | 16,384 pixels |
| --- | ---: | ---: | ---: |
| Cortex-A72 | 0.82x | 0.82x | 0.82x |
| Cortex-A76 | 0.83x | 0.83x | 0.82x |

The large difference between the two scalar baselines is another reason not to
use emulator timings to forecast device performance. Native measurements from
`rpi02` follow.

## Raspberry Pi benchmark results

The standalone C harness was run four times per build with 10,000 iterations.
The table reports medians across those runs. Both binaries passed exhaustive
byte-domain parity, and every scalar/dispatch pair produced identical
checksums. CPU use stayed near 100%, as expected for this single-threaded
kernel. The Pi model, clock governor, temperature, and cooling conditions were
not recorded. One 600-pixel no-autovectorization run and one 4,096-pixel
optimized run were clear timing outliers, so the table uses medians.

| Scalar build | Pixels | Scalar median ms | Dispatch median ms | Median speedup |
| --- | ---: | ---: | ---: | ---: |
| `-O3 -fno-tree-vectorize` | 600 | 30.209 | 8.055 | 3.77x |
| `-O3 -fno-tree-vectorize` | 4,096 | 205.459 | 52.341 | 3.93x |
| `-O3 -fno-tree-vectorize` | 16,384 | 828.287 | 213.372 | 3.88x |
| normal `-O3` (autovectorization enabled) | 600 | 13.779 | 8.004 | 1.72x |
| normal `-O3` (autovectorization enabled) | 4,096 | 93.547 | 54.826 | 1.71x |
| normal `-O3` (autovectorization enabled) | 16,384 | 374.556 | 214.826 | 1.74x |

The explicit NEON path is about 3.8–3.9x faster than the non-vectorized scalar
build and about 1.7x faster than the normally optimized scalar build in these
measurements. These are kernel-only timings; they do not measure the full
rendering pipeline or GPIO/SPI output.

## `rpi02` follow-up benchmark

The updated correction/screen harness was run twice with 10,000 iterations per
case on `rpi02`. The table reports medians across both runs. The Pi model and
clock/thermal conditions were not included. Both runs passed parity; screen
composition reported a zero-byte delta and matching checksums.

| Kernel | Pixels | Scalar ms | Dispatch ms | Speedup |
| --- | ---: | ---: | ---: | ---: |
| RGBA correction | 600 | 13.773 | 8.049 | 1.71x |
| RGBA correction | 4,096 | 91.555 | 52.891 | 1.73x |
| RGBA correction | 16,384 | 375.985 | 215.593 | 1.74x |
| Screen composite | 600 | 489.291 | 120.586 | 4.06x |
| Screen composite | 4,096 | 3,614.177 | 835.549 | 4.33x |
| Screen composite | 16,384 | 14,413.501 | 3,320.468 | 4.34x |

The screen SIMD path clears the 10% target at all tested sizes and is retained.
An alpha-scale SIMD candidate was measured once: its dispatch ratios were
1.16x, 0.61x, and 1.00x at the three frame sizes. It regressed at 4,096 and was
neutral at 16,384, so its NEON path was removed. Alpha scaling uses the scalar
implementation and preserves exact NumPy floor semantics.

## Validation

- Cross-built the full native extension profile for both Cortex-A72 and
  Cortex-A76 against target CPython 3.12 and NumPy headers; all four extension
  modules were ELF64 AArch64 and imported in target CPython 3.12.16 with NumPy
  2.5.4 under QEMU on both CPU models.
- Ran AArch64 extension parity checks under QEMU across SIMD boundaries,
  screen-composition strengths and overlays, saturation fallback, and supported
  in-place aliases. Alpha scaling remains scalar and byte-exact.
- Inspected render and audio compiler vectorization reports and disassembled
  the generated AArch64 object code.
- Ran exhaustive C scalar/dispatch parity and benchmark tests under QEMU on
  both CPU models; ran the scalar-fallback build under QEMU as well.
- Ran an x86-64 AddressSanitizer and UndefinedBehaviorSanitizer build of the
  pixel helper benchmark; parity passed with no sanitizer findings.
- GCC `-fanalyzer` completed for all host native extensions with no diagnostics
  in first-party C sources. Warnings were limited to NumPy C API headers under
  `-Wpedantic`.
- Built all workspace source distributions and wheels. A clean staging checkout
  verified that the `none` wheel contains no shared objects, `audio` contains
  only `_audio`, and `all` contains the expected audio, GPIO, SPI, and render
  modules. The core source distribution includes the new pixel helper and the
  KISS FFT sources.

## Final regression pass

The web app now uses a data router so `UnsavedChangesProvider` can block router
transitions from links, `useNavigate`, and browser history. The selected stripe
is shared between the dashboard and advanced playback page. Startup restoration
loads independently from dashboard identity settings, so a failed name lookup
does not disable the startup control. Pairing codes can come from
`LUMI_PAIRING_CODE`; systemd no longer puts the value in the web process
arguments. The installer validates that the checkout and deployment sources
are not writable by the invoking or service account, imports privileged files
before transferring checkout ownership, and rebuilds native extensions with
the device-local optimization setting.

The full Python suite passed **6,915 tests with 168 skips** on Python 3.12.15.
Ruff and mypy passed; mypy checked 175 source files. The frontend suite passed
**174 tests in 28 files**, its production build succeeded, and lint exited
successfully with Oxlint warnings. OpenAPI generation produced no changes to
`openapi.json` or the generated TypeScript contract. The stale settings tests
now compare persisted data with the current `SETTINGS_VERSION`; all 66 settings
tests passed.

RGBA correction and screen-composition performance were measured natively on
`rpi02`; alpha SIMD was removed after failing the acceptance bar. The Pi model
and runtime conditions were not recorded. GPIO/SPI hardware output and
Bluetooth/PipeWire audio smoke tests were not part of the reported run. QEMU
checks validate target-architecture loading and behavior, but do not substitute
for those device checks. The broader expression-level renderer safety audit
and its remaining risks are tracked in
[`native-render-hardening.md`](native-render-hardening.md).
