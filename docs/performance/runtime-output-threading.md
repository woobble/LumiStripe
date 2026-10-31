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
factory only receives the initial runtime settings. An initial Raspberry Pi 4
SPI run completed on the candidate, but a matched baseline is still pending;
GPIO and physical audio-input measurements have not been run.

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

### Preliminary candidate result

The Pi 4 Model B Rev 1.4 (`aarch64`, Python 3.12.13) completed the candidate
matrix with two mirrored SPI outputs at 1 MHz, a 15-watt budget, and demo
audio. The candidate was an earlier copy without the output-latency fields
added below, so latency still needs a repeat run.

| LEDs total | Mode | Render FPS | Output FPS | Dropped sets | Output failures | Missed deadlines | CPU |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | Static | 58.9 | 58.9 | 0 | 0 | 5.9% | 33.5% |
| 100 | Dynamic | 54.5 | 54.4 | 1 | 0 | 6.7% | 67.0% |
| 300 | Static | 59.3 | 59.3 | 0 | 0 | 6.0% | 23.5% |
| 300 | Dynamic | 50.1 | 50.1 | 0 | 0 | 6.9% | 45.4% |
| 600 | Static | 59.3 | 57.7 | 26 | 0 | 5.8% | 23.9% |
| 600 | Dynamic | 52.6 | 51.1 | 22 | 0 | 6.5% | 47.9% |

The baseline command reached the first 100-LED dynamic case, where the
`set_mode()` command future timed out after five seconds. The script writes
its JSON only after all requested cases finish, so that attempt produced no
baseline file even though the preceding 100-LED static case had completed.
The split static/dynamic commands above preserve the static results if dynamic
startup times out again. The dynamic baseline remains unmeasured until that
case completes or the timeout is diagnosed.

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
