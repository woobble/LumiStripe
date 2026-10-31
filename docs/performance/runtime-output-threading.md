# Runtime output threading

The hardware runtime now has separate render and LED output owners. The
`RuntimeWorker` renders into software controllers and performs zone mapping,
color correction, power limiting, and preview preparation. `LedOutputThread`
creates and exclusively accesses the physical SPI or GPIO controllers. It
copies complete frame sets from `LatestFrameExchange` into those devices,
flushes each strip, and owns blackout clearing, reconfiguration, recovery, and
shutdown.

`LatestFrameExchange` preallocates two full frame-set slots when the topology
is created. The renderer copies each complete corrected RGBA set into a free
slot and returns immediately. If a transfer is in flight, a newer frame
replaces the pending one; all strips in a consumed set share one sequence.
The output thread holds a slot until every configured strip has returned from
its transfer attempt. This bounds memory and queue delay while preventing the
renderer from changing bytes being encoded by a driver.

Topology commands enqueue an output-thread reconfiguration and keep rendering
while the current transfer completes. The output thread blacks and closes the
old devices, then creates the replacement devices. The renderer installs the
new software topology after the output thread acknowledges it. A failed setup
rebuilds the prior physical topology before rejecting the command. Blackout,
calibration flashes, stripe tests, and shutdown black frames use the same
output-thread boundary.

Render metrics and output metrics are reported independently. Dashboard state
includes output rate, end-to-end frame-set latency (latest and rolling p95),
output-thread heartbeat age, dropped output frames, output failures, and the
latest output error. Latency is measured from frame submission through
completion of the full output set; p95 uses the most recent 128 completed
sets. The runtime watchdog monitors the output thread independently and
requests a process restart if it stops making progress.

The synchronous in-memory simulator path remains unchanged. Hardware
`controller_factory` implementations are also created and accessed by the
output thread, so injected physical controllers follow the same ownership and
frame-exchange rules as the default SPI/GPIO drivers. Live stripe topology
editing still requires the default topology factory because an injected
factory only receives the initial runtime settings. Raspberry Pi 4 SPI
measurements now cover the candidate with native rendering enabled and disabled,
plus a pre-refactor static baseline. Baseline dynamic startup times out; GPIO
and physical audio-input measurements have not been run.

## Raspberry Pi 4 before/after run

The pre-refactor baseline for this change is commit `5d66cec`. Keep the same
Pi, power supply, SPI wiring, software profile, and command line for both
runs. This matrix uses two equal SPI outputs, mirrored rendering, a safe
15-watt power budget, and synthetic demo audio. Adjust the SPI device paths
and power budget to match the test rig; keep power limiting enabled.

On the Pi, from a checkout that contains the candidate changes, create and
prepare a detached baseline worktree:

```bash
git worktree add --detach /tmp/lumistripe-before-output-thread 5d66cec
cd /tmp/lumistripe-before-output-thread
UV_CACHE_DIR=/tmp/lumistripe-uv-cache LUMISTRIPE_BUILD_EXTENSIONS=spi \
  uv sync --locked --all-extras --dev --reinstall-package lumistripe-core
```

Run the baseline static matrix first, so its results are saved even if a
separate dynamic startup later fails:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --led-counts 100,300,600 --layouts mirrored --zone-counts 1 \
  --modes static --power-limits on --power-budget-watts 15 \
  --audio-source demo --warmup 5 --duration 15 \
  --output /tmp/lumistripe-pi4-before-output-thread-static.json
```

Run the baseline dynamic matrix separately:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --led-counts 100,300,600 --layouts mirrored --zone-counts 1 \
  --modes dynamic --power-limits on --power-budget-watts 15 \
  --audio-source demo --warmup 5 --duration 15 \
  --output /tmp/lumistripe-pi4-before-output-thread-dynamic.json
```

Then run the exact same matrix from the candidate checkout, after building its
SPI extension:

```bash
cd /path/to/lumistripe-candidate
UV_CACHE_DIR=/tmp/lumistripe-uv-cache LUMISTRIPE_BUILD_EXTENSIONS=spi \
  uv sync --locked --all-extras --dev --reinstall-package lumistripe-core
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --led-counts 100,300,600 --layouts mirrored --zone-counts 1 \
  --modes static --power-limits on --power-budget-watts 15 \
  --audio-source demo --warmup 5 --duration 15 \
  --output /tmp/lumistripe-pi4-after-output-thread-static.json
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --led-counts 100,300,600 --layouts mirrored --zone-counts 1 \
  --modes dynamic --power-limits on --power-budget-watts 15 \
  --audio-source demo --warmup 5 --duration 15 \
  --output /tmp/lumistripe-pi4-after-output-thread-dynamic.json
```

Compare `effective_fps`, `actual_frame_interval_ms`, `missed_deadline_rate`,
`cpu_utilization_percent`, and the common stage percentiles in `timings_ms`.
The candidate report also has output frame rate, end-to-end output latency
(latest and p95 over at most 128 recent frame sets), dropped frame sets,
failures, and output heartbeat age. The baseline has no separate output-thread
counters; its frame cadence includes physical transmission time. Both files
record the Pi model, architecture, Python version, and whether physical SPI
was enabled.

### Candidate Pi 4 results with native rendering

The Pi 4 Model B Rev 1.4 (`aarch64`, Python 3.12.13) completed the candidate
static and dynamic runs with two mirrored SPI outputs at 1 MHz, a 15-watt
budget, and synthetic demo audio. These runs report
`render_native_enabled=true`. Output latency is submission-to-completion for a
full frame set; p95 summarizes the most recent 128 sets.

| LEDs total | Mode | Render FPS | Delivered FPS | Latest / p95 latency | Dropped sets | Missed deadlines | CPU |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | Static | 58.9 | 58.9 | 3.0 / 24.2 ms | 0 | 5.9% | 33.5% |
| 100 | Dynamic | 54.3 | 54.3 | 3.1 / 23.7 ms | 0 | 6.9% | 66.0% |
| 300 | Static | 59.1 | 59.1 | 8.3 / 23.0 ms | 0 | 5.9% | 23.8% |
| 300 | Dynamic | 55.1 | 55.1 | 8.3 / 30.3 ms | 0 | 6.3% | 50.1% |
| 600 | Static | 59.0 | 57.3 | 19.4 / 40.5 ms | 27 | 5.9% | 24.8% |
| 600 | Dynamic | 52.1 | 49.9 | 23.2 / 40.1 ms | 32 | 6.4% | 49.7% |

All six cases reported zero output failures. At 600 LEDs, the output path
approaches its transfer limit: delivered frame rate falls below render rate,
and the latest-wins exchange drops stale sets while keeping storage bounded.
The output latency p95 reaches about 40 ms, while the measured render-side
`_flush_power_limited_outputs` p95 remains below 2.1 ms. Renderer deadline
misses are still around 6%; these measurements do not attribute those misses
to SPI waiting. Demo audio is synthetic and does not measure live capture or
audio-to-light latency.

An earlier candidate run had similar static rates and a different 300-LED
dynamic cadence (20 ms expected interval versus 16 ms in this run), so its
dynamic results should not be compared directly with this table.

### Matched static comparison with the NumPy renderer

The baseline and candidate fallback runs both report
`render_native_enabled=false`. On the synchronous baseline, render FPS is also
the physical output FPS. The baseline `_flush_power_limited_outputs` timing
includes SPI transfers; on the candidate it covers preparation and frame
publication only. Candidate delivered FPS and output latency therefore show
what the independent output thread actually completed.

| LEDs | Baseline render/output FPS | Candidate render/delivered FPS | Baseline flush / candidate prep p95 | Candidate latest / p95 output latency | Baseline / candidate missed deadlines | Baseline / candidate CPU | Candidate dropped sets |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 58.6 / 58.6 | 59.1 / 59.2 | 4.57 / 1.25 ms | 3.1 / 17.2 ms | 5.9% / 5.9% | 31.4% / 32.2% | 0 |
| 300 | 56.4 / 56.4 | 58.6 / 58.4 | 10.04 / 1.52 ms | 8.3 / 30.2 ms | 12.9% / 5.9% | 50.6% / 53.1% | 2 |
| 600 | 32.3 / 32.3 | 57.1 / 30.6 | 18.23 / 2.06 ms | 44.2 / 52.2 ms | 100% / 7.5% | 50.5% / 81.6% | 399 |

At 100 and 300 LEDs, the fallback candidate keeps rendering near 59 FPS and
delivers nearly every frame. At 600 LEDs, render cadence rises to 57.1 FPS,
but output delivers 30.6 FPS and drops 399 stale sets, slightly below the
baseline's 32.3 FPS; CPU use reaches 81.6%. This shows that rendering no longer
waits for slow output, but this software-rendering profile does not improve
600-LED physical delivery. The production native-render run above delivered
57.3 FPS at 600 LEDs. This comparison matches the renderer setting, but it
does not isolate output threading: the candidate also has the updated SPI
encoder, including its GIL release.

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --led-counts 100,300,600 --layouts mirrored --zone-counts 1 \
  --modes static --power-limits on --power-budget-watts 15 \
  --audio-source demo --warmup 5 --duration 15 --disable-native-render \
  --output /tmp/lumistripe-pi4-after-output-thread-static-numpy.json
```

The candidate dynamic NumPy-fallback run used:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_runtime_pipeline.py \
  --hardware --spi-devices /dev/spidev0.0,/dev/spidev0.1 \
  --led-counts 100,300,600 --layouts mirrored --zone-counts 1 \
  --modes dynamic --power-limits on --power-budget-watts 15 \
  --audio-source demo --warmup 5 --duration 15 --disable-native-render \
  --output /tmp/lumistripe-pi4-after-output-thread-dynamic-numpy.json
```

| LEDs | Render / delivered FPS | Latest / p95 output latency | Dropped sets | Missed deadlines | CPU |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 52.3 / 52.2 | 24.0 / 19.0 ms | 1 | 6.8% | 56.8% |
| 300 | 49.5 / 44.1 | 8.7 / 41.7 ms | 82 | 31.2% | 82.6% |
| 600 | 42.9 / 38.3 | 35.0 / 56.6 ms | 70 | 38.5% | 81.8% |

Both baseline dynamic attempts timed out while waiting for the `set_mode()`
command future at the first 100-LED case. The isolated attempt confirms the
failure is repeatable; it does not provide baseline dynamic frame-rate data.
The dynamic NumPy-fallback results show substantial CPU use and missed render
deadlines at 300 and 600 LEDs. Synthetic demo audio does not measure live
capture or audio-to-light latency.

## Native backend GIL behavior

### SPI

`SPIStripe.force_flush()` encodes into a byte buffer and calls
`spidev.SpiDev.writebytes2()`. py-spidev's buffer-protocol implementation
releases the GIL around each POSIX `write()` call, so the kernel transfer does
not hold Python execution. LumiStripe's `_sm16716.encode_into()` now also
releases the GIL around the zero-fill and bit-packing loop after validating and
pinning both buffers. Its low-level contract requires callers not to mutate
either buffer until the call returns. The hardware output thread owns the
controller pixel buffer and encoder payload throughout that call, while the
renderer owns separate exchange slots, so this boundary prevents a race.

The locked optional dependency is py-spidev 3.8, and that version is installed
in the development environment. Its compiled extension is available there, but
the implementation source was checked in the
[py-spidev repository](https://github.com/doceme/py-spidev/blob/master/spidev_module.c).
The development host is x86_64, so the Pi 4 deployment image still needs
hardware validation.

### GPIO

The `/dev/gpiomem` backend converts RGBA pixels into RGB bytes while holding
the GIL, then releases it for the complete GPIO bit-clock transfer in
`_gpiomem.c`. Its `flushing` guard rejects overlapping flush, GPIO writes, or
close operations while that transfer is active.

The libgpiod fallback uses a Python loop to pulse the lines. Its `Request`
binding converts the Python values while holding the GIL and releases it around
the native `gpiod_line_request_set_values_subset()` call. This yields during
each line update, but the per-pulse Python loop and value conversion still
need measurement at larger LED counts. The environment used for this audit has
libgpiod 2.5.0 installed; the implementation is in the
[official request binding](https://kernel.googlesource.com/pub/scm/libs/libgpiod/libgpiod/+/40252603516b510b8c728a7d22b1ac99dcefbe1d/bindings/python/gpiod/ext/request.c).

## Validation boundary

Unit tests use blocking and recording fake controllers to verify owner-thread
construction, writes, reconfiguration, close, latest-frame replacement,
atomic multi-strip sets, continued rendering during blocked output, and the
separate output watchdog. These tests do not measure physical SPI or GPIO
throughput. The available development host is x86_64, so Raspberry Pi 4
measurements with SPI, `/dev/gpiomem`, libgpiod, multiple strip lengths, and
audio processing remain outstanding. Include render rate, output rate, dropped
frames, output heartbeat, and CPU use in those measurements.
