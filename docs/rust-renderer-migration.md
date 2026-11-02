# Rust renderer migration

Goal 3 is in progress. The Python animation catalog remains canonical and the
Python/C renderer remains the application fallback.

## Current implementation

`scripts/export_rust_animation_catalog.py` exports all 54 animation definitions
and 19 effect definitions to the Rust catalog. The Rust workspace now has
separate crates for definitions, animation state, render primitives,
compositing, and effects.

`AnimationInstance` now renders all 54 catalog animations, including the 19
visual effects. The reusable two-layer effect stack also supports all 19 effect
definitions. Random effects use a seeded Python-compatible MT19937 sequence
for repeatable rendering. A Rust render output can now layer up to two effects
over its base animation, with caller-supplied base intensity, overlay budget,
settings, and per-effect intensity. The effect instances and scratch frames stay
owned by the Rust output worker. Reconfiguring a layer stack retains state for
effects that remain active, including when another layer is added or intensity
changes; only new or settings-changed effects get fresh state.

Protocol v2 has a pixel-free `render` control command. It carries a sequence,
monotonic timestamp, frame delta, brightness, optional audio features, and one
animation/settings object per configured output. Each output can also specify
its logical zone count and repeat or alternate zone pattern. The Rust output
worker owns the animation instances, writes directly into reusable logical and
physical frame buffers, applies timestamped scene transitions, zone mapping,
per-output color correction, power limiting, and blackout, then submits those
frames to the existing output set.
A renderer-capabilities query lets the client keep any future unsupported
animation on the Python/C fallback; the engine rejects unsupported render
requests before queuing them. A newer queued render replaces an older one. The
compact binary pixel-frame protocol and v1 client remain available.

`RustEngineProcess.render_frame()` exposes this direct request to Python. The
web runtime uses a bounded FIFO of monotonically revised `scene_update`
commands for reliable scene changes, with a separate latest-wins `audio_update`
mailbox for replaceable audio snapshots. Beat and structure edges stay in the
audio updates until health acknowledges their sequences. `render_frame()` and
the latest-wins `render` command remain available for compatibility callers.
The web runtime routes Static, Cycling, and Dynamic animation scenes to Rust
for independent outputs, Continuous layouts, and mirrored outputs of different
logical lengths.
The Rust scheduler owns Dynamic selection, activity gating, and effect
triggering, cooldowns, active effect timing, and drop accents. Python continues
classifying music structure and sends structure and audio features to Rust.
Dynamic off and animation idle scenes also use Rust. Solid playback and solid
Dynamic idle scenes use the same Rust finalization path for brightness,
correction, and power limiting. Python retains the fallback policy path.

Continuous requests carry the shared canvas length and each output's scene
offset. Rust renders that canvas and maps the assigned slice through the
output's zone geometry. Differently sized mirrored outputs carry a resampling
flag; Rust applies the same evenly spaced, ties-to-even sample indices as the
Python controller. Per-output zone mapping, scene transitions, color
correction, power limiting, and blackout are handled inside Rust for supported
scene layouts. The compact binary pixel-frame protocol remains available for
fallback and compatibility paths. Rust health telemetry reports separate
scene-render time and power estimates. The Python runtime adapter maps those
values into the dashboard and forwards bounded post-correction preview
samples to the existing preview stream; the frontend maps them onto the full
output layout.
The dashboard reports Python playback scheduling time separately from Rust
scene-render time and output transmission/queue timings. Playback scheduling
measures policy advancement without pixel rendering; Rust scene-render time
measures the worker-owned renderer and finalization path.

## Rendering parity audit

| Python source of behavior | Rust owner | Current parity evidence |
| --- | --- | --- |
| `animation/catalog.py`: names, settings, defaults, frame periods, and the 19 effect definitions | Generated animation-definition catalog and validated `AnimationInstance` settings | `export_rust_animation_catalog.py --check`; Rust catalog validation tests |
| `render/kernels.py` and `render/native_*.c`: Python/NumPy fallback and optimized C kernels | Rust animation and effect renderers; the Python/C implementation stays available for rollback | Four-frame Python/native-C golden cases for all 54 animations and all 19 effects; renderer benchmark compares Python, C, Rust release, and scalar Rust |
| `effects/layers.py`: active effect selection, trigger beats, intensity, and overlay budget | Rust owns Dynamic scheduler policy and effect pixel state; Python keeps the fallback path | Seeded effect golden fixtures, Rust scheduler checks, Python adapter checks, and layer-retention tests |
| `controller.py`: logical zones, uneven physical segments, repeat/alternate orientation, and topology layout | Rust zone maps, shared scene slicing, and mirrored scene resampling | Python-generated layout goldens compare independent, continuous, and mirrored multi-output frames with uneven lengths, zones, and audio inputs |
| `PlaybackEngine` and `AnimationPlayer`: transition start time, duration, and mode | Timestamped Rust crossfade, fade-through-black, and wipe compositor | Controlled transition golden frames and runtime scene-revision tests |
| `AudioSnapshot` render frame: RMS, eight bands, beat, and beat strength | Typed protocol-v2 `AudioInput`; Rust animation/effect instances consume timestamped values | Animation and effect goldens include beat and non-beat inputs; Rust reactive-input tests compare Python band/drive rules |
| Python `uint8` RGBA output and the v2 frame-set packet | Rust `FrameBuffer` uses contiguous `[R,G,B,A]`; direct render IPC carries no pixel payload | Shared protocol fixtures, frame-buffer tests, and a socket test that queues typed renders without pixel bytes; the compact binary frame path remains for compatibility and rollback |

The golden fixtures exercise representative settings and seeded random states;
the generated catalog check covers every setting definition and default. They
do not enumerate every possible combination of user setting values. Timing
benchmarks report render latency and allocations separately from the Python
playback scheduler and Rust hardware output measurements.

## Golden-frame coverage

`scripts/export_rust_animation_golden.py` generates controlled Python and
native-C frames for all 54 animations (108 variants). The Rust tests compare
four sequential frames per case using a 64-pixel output, fixed brightness,
settings, timestamps, seeded random state, and audio with beat and non-beat
inputs.

The comparison allows a maximum RGB channel error of 5 for HSL and palette
conversion renderers and 1 for the other tested renderers. Alpha allows an
error of 1. PixelExplosion's Python fallback now clears its reusable output
frame like its native C kernel, keeping both paths consistent.
The tolerance covers small floating-point conversion differences between
Python, C, and Rust; the tests report the renderer, variant, frame, byte, and
observed error when they fail.

`scripts/export_rust_effect_golden.py` generates four-frame Python and native-C
fixtures for all 19 effects (38 variants). Rust checks the effect renderers,
seeded randomness, and reusable state buffers.

`scripts/export_rust_transition_golden.py` generates Python frames for
crossfade, fade-through-black, and wipe transitions at three progress points.
The Rust compositing tests compare every channel against those controlled
fixtures.

`scripts/export_rust_power_golden.py` generates Python reference results for
color correction, per-output and shared power limits, blackout, and final RGBA
frames. Rust checks both the resulting frame bytes and power estimates.

`scripts/export_rust_layout_golden.py` renders controlled audio-reactive frames
through the Python controllers for independent, continuous, and mirrored
layouts. The fixture covers unequal output lengths, alternate zone orientation,
scene slicing, mirrored resampling, settings, brightness, and four timestamps;
the Rust runtime checks each output frame with explicit RGB and alpha
tolerances.

## Renderer benchmarks

For statistical microbenchmarks of representative Rust animation renderers,
run the Criterion harness. It reuses one animation instance and frame buffer per
case, so each sample excludes setup allocations:

```bash
cargo bench --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-engine-animation-state --bench animation_render
```

The harness samples four representative animations at 150, 300, and 600 pixels.
Use the runtime matrix for scheduler, IPC, CPU, and physical-output
measurements; these microbenchmarks isolate animation rendering.

`scripts/benchmark_rust_renderers.py` measures the Python fallback, available
native-C kernel, and one or more Rust release binaries for the same animation,
settings, LED count, warmup, frame count, and audio sequence. Rust reports
per-frame allocation calls and allocated bytes. Python can report retained and
peak live traced memory with `--trace-allocations` in a separate pass. The
tracer does not count total allocations or allocations inside native C. These
are direct renderer measurements, not full runtime or output-worker
measurements.

Use `scripts/benchmark_rust_catalog_comparison.py` to run that comparison for
all 54 animations and save per-animation results and profile summaries:

```bash
uv run python scripts/benchmark_rust_catalog_comparison.py \
  --pixels 300 --frames 5000 --warmup 500 --audio --python --native-c \
  --rust-binary llvm=/tmp/lumistripe-rust-llvm/release/examples/benchmark_render \
  --rust-binary scalar=/tmp/lumistripe-rust-scalar/release/examples/benchmark_render \
  --output /tmp/lumistripe-renderer-catalog.json
```

The comparison reports whether the installed C build enables its optional
NEON path. Run it once with the scalar C extension and once with the target
optimized C extension, retaining each JSON file. The Rust effect-layer path is
measured separately by `scripts/benchmark_rust_catalog.py`, which covers all 19
effects and can fail if a steady-state frame allocates.

The trusted Raspberry Pi 5 CI job runs both C profiles alongside target-native
LLVM and scalar Rust builds, then uploads the JSON results and toolchain/CPU
metadata as a workflow artifact. It measures renderer code only and does not
write to physical GPIO or SPI outputs. The workflow does not dispatch
pull-request code to the persistent Pi runner. Raspberry Pi 4 measurements
still require running these commands on a Pi 4 and recording its artifact
separately.

Use `scripts/benchmark_rust_catalog.py` to sweep all 54 animations and 19 effect
layers with each Rust build profile. It can fail the run if any steady-state
frame allocates and writes the per-case results to JSON:

```bash
uv run python scripts/benchmark_rust_catalog.py \
  --rust-binary llvm=/tmp/lumistripe-rust-llvm/release/examples/benchmark_render \
  --rust-binary scalar=/tmp/lumistripe-rust-scalar/release/examples/benchmark_render \
  --pixels 300 --frames 5000 --warmup 500 --audio \
  --fail-on-allocation --output /tmp/lumistripe-rust-catalog-sweep.json
```

Build an optimized and a scalar Rust binary in separate target directories, so
their compiler flags cannot reuse the same Cargo artifact:

```bash
CARGO_TARGET_DIR=/tmp/lumistripe-rust-llvm cargo build --release --locked --manifest-path rust/Cargo.toml -p lumistripe-engine-animation-state --example benchmark_render
CARGO_TARGET_DIR=/tmp/lumistripe-rust-scalar RUSTFLAGS="-C no-vectorize-loops -C no-vectorize-slp" cargo build --release --locked --manifest-path rust/Cargo.toml -p lumistripe-engine-animation-state --example benchmark_render
```

Then compare the Python path and both Rust builds (add `--native-c` when the
render extension is installed):

```bash
uv run python scripts/benchmark_rust_renderers.py \
  --animation rainbow --pixels 300 --frames 5000 --warmup 500 --audio \
  --python --native-c \
  --rust-binary llvm=/tmp/lumistripe-rust-llvm/release/examples/benchmark_render \
  --rust-binary scalar=/tmp/lumistripe-rust-scalar/release/examples/benchmark_render
```

Measure the Rust two-effect layer path separately with `--effects` (Python and
native-C comparison options render the base animation only):

```bash
uv run python scripts/benchmark_rust_renderers.py \
  --animation rainbow --pixels 300 --frames 5000 --warmup 500 --audio \
  --effects '[{"name":"hard_beat","settings":{},"intensity":0.75}]' \
  --base-intensity 0.8 --max-overlay-strength 0.65 \
  --rust-binary llvm=/tmp/lumistripe-rust-llvm/release/examples/benchmark_render \
  --rust-binary scalar=/tmp/lumistripe-rust-scalar/release/examples/benchmark_render
```

Use `--outputs 4` to measure independent multi-strip rendering. The reported
frame time covers all outputs in one frame tick, and Rust reports allocations
per tick and per output. This still measures renderer throughput only; it does
not include SPI/GPIO transmission time.

### Local x86-64 sample

One run on an AMD Ryzen 9 9900X, Linux x86-64, Python 3.12.15 used 300 pixels,
2,000 measured frames, 250 warmup frames, and audio input. Values are p95
nanoseconds per direct renderer call:

| Animation | Python | Native C | Rust release | Rust scalar |
| --- | ---: | ---: | ---: | ---: |
| Rainbow | 996,183 | 11,752 | 8,897 | 6,502 |
| Fire | 772,685 | 6,302 | 1,453 | 1,383 |
| Aurora | 1,106,049 | 17,212 | 13,976 | 13,615 |

The Rust renderer reported zero allocations and zero allocated bytes per frame
for these cases. A separate `rainbow` + `hard_beat` run reported p95 of 8,045
ns for the release build and 8,265 ns for the scalar build, also with zero
allocations per frame. The Rust release build did not show a consistent
advantage over the scalar build in these samples. These host results do not
establish Raspberry Pi performance or full runtime throughput; the benchmark
measures direct renderer calls and excludes playback policy and output
transmission.

A separate three-output `rainbow` run on the same host used 300 pixels per
output, 2,000 measured frames, 250 warmup frames, and audio input. The p95
frame-tick time was 2,953,074 ns for Python and 19,437 ns for Rust release
(LLVM); Rust reported zero allocations across all three outputs per frame.

An allocation sweep on that x86-64 host covered 54 animations and 19 individual
effect layers with audio, 32 measured frames, and 8 warmup frames. Both the
default LLVM and scalar Rust builds reported zero allocations and zero
allocated bytes in all 146 cases. These short allocation runs do not establish
Raspberry Pi latency.

A catalog comparison on the same host used 300 pixels, 1,000 measured frames,
200 warmup frames, and audio input. The table reports p95 latency per direct
renderer call, summarized across all 54 animations:

| Profile | Median p95 | Maximum p95 | Nonzero-allocation cases |
| --- | ---: | ---: | ---: |
| Python | 708,641 ns | 2,253,035 ns | n/a |
| Native C | 9,944 ns | 53,259 ns | n/a |
| Rust release (LLVM) | 2,024 ns | 16,080 ns | 0 / 54 |
| Rust scalar | 2,014 ns | 17,904 ns | 0 / 54 |

All 54 C kernels were available; the x86 build did not enable NEON. A separate
1,000-frame allocation sweep covered all 54 animations and 19 effect layers;
both Rust profiles reported zero allocations and zero allocated bytes across
all 146 cases. These x86 results do not establish Raspberry Pi performance or
make a target-specific C fast-path decision.

### Native Raspberry Pi 5 renderer run

User-supplied artifacts recorded a Raspberry Pi 5 Model B Rev 1.0
(`aarch64`), Linux `7.0.0-1020-raspi`, Python 3.12.15, Rust 1.99.1 / LLVM
23.1.1, and GCC 15.2.0 on 2026-11-01. Each C profile ran once with 300 pixels,
1,000 measured frames, 200 warmup frames, and audio input. Values below are
median p95 nanoseconds per direct renderer call across 54 animations:

| C build | Python | Native C | Rust LLVM | Rust scalar |
| --- | ---: | ---: | ---: | ---: |
| Scalar (`render_neon_enabled=false`) | 2,503,907 | 30,871 | 8,148 | 7,750 |
| NEON (`render_neon_enabled=true`) | 2,494,231 | 29,834 | 8,001 | 7,917 |

All 54 cases were available in every profile. Rust reported zero allocations
and zero allocated bytes in each animation. The separate sweep covered all 54
animations and 19 effects; both Rust profiles reported zero allocations in
all 73 cases. For this single run, scalar C was faster than both Rust builds
for `drop_explosion`; with the NEON C build, Rust was faster in all 54
per-animation p95 comparisons. These results are preliminary: the artifacts do
not record the source revision, CPU governor, or temperature, and they do not
measure scheduling or physical output transmission.

### AArch64 emulated build and allocation check

On 2026-11-01, the locked Rust workspace tests were cross-compiled for
`aarch64-unknown-linux-gnu` and run under QEMU 11.1.0 on an x86-64 host; all 88
tests passed. The optimized workspace engine was also identified as an AArch64
ELF and started under QEMU. The Rust renderer benchmark reported zero
steady-state allocations for both a 300-pixel audio-reactive `rainbow` case and
the same case with a `hard_beat` effect layer (100 measured frames, 10 warmup
frames). QEMU latency numbers are excluded because emulation does not provide
representative ARM CPU performance. This verifies ARM64 compilation and these
allocation paths only; it does not replace native ARM64 CI or Raspberry Pi 4/5
performance and hardware validation.

On an AArch64 target, repeat the Rust build with
`RUSTFLAGS="-C target-cpu=native"`, and build the C extension there with
`LUMISTRIPE_BUILD_EXTENSIONS=render LUMISTRIPE_NATIVE_OPTIMIZATION=1` to
measure the target's native-CPU compiler profile. Compare the C NEON and scalar
builds by setting `LUMISTRIPE_DISABLE_RENDER_NEON=1` for the scalar build, then
rebuilding without it for the NEON build. The benchmark JSON records
`native_c_neon_enabled` for each run. For example:

```bash
LUMISTRIPE_BUILD_EXTENSIONS=render LUMISTRIPE_NATIVE_OPTIMIZATION=1 \
  UV_CACHE_DIR=/tmp/lumistripe-uv-cache \
  uv run --directory packages/lumistripe-core --with setuptools \
  python setup.py build_ext --inplace --force
```

Repeat that command with `LUMISTRIPE_DISABLE_RENDER_NEON=1` for the scalar C
variant. Run `scripts/benchmark_rust_renderers.py --native-c` after each build
and retain the JSON output with the Rust autovectorized and scalar results.

Record the machine metadata and benchmark JSON when comparing CPU and
Raspberry Pi results. `docs/performance/arm64-simd-validation.md` contains
native `rpi02` measurements for isolated C correction and screen-composition
kernels; its exact Pi model was not recorded. Those are not Rust catalog
renderer results or GPIO/SPI output timings. The native Pi 5 renderer sample
is documented above; Pi 4 renderer results are still missing.

Regenerate or check the catalog and golden files with:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/export_rust_animation_catalog.py --check
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/export_rust_animation_golden.py --check
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/export_rust_effect_golden.py --check
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/export_rust_layout_golden.py --check
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/export_rust_transition_golden.py --check
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/export_rust_power_golden.py --check
```

The Python 3.12 CI job runs these checks so changes to the Python catalog,
native-C renderer, effects, transitions, output correction, or power behavior
cannot silently leave stale Rust catalogs or golden frames behind.

## Remaining work

Runtime scene routing now covers Static, Cycling, Dynamic, and Solid scenes
across all three topology layouts. Rust owns Dynamic scheduler policy while
Python continues music-structure classification and fallback behavior. Multi-strip benchmarks
cover renderer throughput but not the complete runtime or output transfer
timing.

### Raspberry Pi validation status and procedure

| Validation | Pi 4 | Pi 5 |
| --- | --- | --- |
| Native Rust/Python/C catalog and effect benchmark | Not run | One supplied run; source revision and thermal metadata missing |
| Physical SPI/GPIO output, timing, and shutdown | Not performed | Not performed |

For each board, record `/proc/device-tree/model`, `uname -a`, CPU governor and
temperature, compiler/Rust versions, and the JSON produced by the renderer
benchmark scripts. The Pi 5 workflow builds target-native LLVM and scalar Rust,
and scalar and NEON C profiles; its assertions verify the selected C profile
and complete animation coverage. Its environment artifact also records the
source commit, board and CPU details, governor, and thermal-zone readings when
the kernel exposes them. Run the same profiles on Pi 4 and retain its JSON
separately.

Physical output validation is a distinct run. With a connected strip and its
known-good device configuration, start the web runtime with the Rust hardware
backend enabled, for example:

```bash
RUSTFLAGS="-C target-cpu=native" \
  cargo build --release --locked --manifest-path rust/Cargo.toml \
  -p lumistripe-engine --bin lumistripe-engine
uv run lumistripe-web --engine-backend rust \
  --engine-binary rust/target/release/lumistripe-engine \
  --hardware --ignore-saved-stripes --output-backend spi \
  --spi-device /dev/spidev0.0 --pixels 300
```

Exercise representative animations, transitions, zone mapping, color
correction, power limiting, blackout, and normal shutdown on each available
physical backend. For GPIO, use `--output-backend gpio` with the board's
configured chip and data/clock pins. Record the dashboard's separate
scheduling, Rust-render, queue, and transmission measurements. Observe
SPI/GPIO signals and frame timing on the physical pins with suitable
measurement equipment. Record the board, strip, output configuration, and
observed results. The `mock_spi` benchmarks and mock hardware tests cover
software paths only; they do not establish physical GPIO/SPI timing safety.
No such physical run has been performed in this environment.
