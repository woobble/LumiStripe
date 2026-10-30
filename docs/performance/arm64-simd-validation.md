# ARM64 SIMD validation

## Scope and machine

This pass added an ARM64 Advanced SIMD implementation for the native RGBA
color-correction kernel and checked the native extension profile for Raspberry
Pi 4 and Pi 5 compiler targets. The validation host was x86-64 Linux. It had
`aarch64-linux-gnu-gcc` 16.2.0, GNU binutils 2.47, QEMU user emulator 11.1.2,
and the `/usr/aarch64-linux-gnu` sysroot. No physical Pi was attached.

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
including unchanged alpha, then compares direct scalar and dispatched calls at
600, 4,096, and 16,384 pixels. The Python regression test also covers vector
boundaries at 0, 1, 15, 16, 17, 31, 32, 33, and 257 pixels.

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

The large difference between the two scalar baselines is another reason not to
use emulator timings to forecast device performance. The optimization is
retained because the explicit implementation is byte-exact, uses baseline
ARM64 SIMD, and has no portability cost on other targets; its on-device
benefit still needs measurement on a Pi.

## Validation

- Cross-built the full native extension profile for both Cortex-A72 and
  Cortex-A76 against target CPython 3.12 and NumPy headers; all four extension
  modules were ELF64 AArch64 and imported in target CPython 3.12.16 with NumPy
  2.5.4 under QEMU on both CPU models.
- Ran the Python extension's byte-parity checks on both CPU models, covering
  every 8-bit gain/source value across RGB channels, SIMD boundary lengths,
  alpha preservation, and exact in-place aliasing.
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

No physical Pi was attached, so real-device performance, GPIO/SPI hardware
output, and Bluetooth/PipeWire audio smoke tests were unavailable. QEMU checks
validate target-architecture loading and behavior, but do not substitute for
those device checks. The broader expression-level renderer safety audit and
its remaining risks are tracked in
[`native-render-hardening.md`](native-render-hardening.md).
