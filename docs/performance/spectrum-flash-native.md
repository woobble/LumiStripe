# Native SpectrumFlash Rendering

SpectrumFlash now has an optional scalar C kernel in the existing `render`
extension. Python still owns reactive audio analysis, flash decay, hue phase,
settings, and effect lifecycle. The native path renders the RGBA pixels into the
existing reusable effect buffer. Builds without the render extension continue
to use the Python renderer.

The kernel reproduces the existing `Hsla` conversion, palette-controller
mapping, brightness-scaled alpha truncation, adjacent-band blend, and warm-flash
overwrite behavior. Native output is checked byte-for-byte against the Python
path for all four palettes, brightness values 0, 0.37, and 1, and lengths 1,
150, 300, and 600 LEDs.

## Reproduction

Build the render extension and run the parity/performance checks:

```bash
LUMISTRIPE_BUILD_EXTENSIONS=render uv build --package lumistripe-core \
  --wheel --out-dir /tmp/lumistripe-render-wheel
PYTHONPATH=packages/lumistripe-core/build/lib.linux-x86_64-cpython-312 \
  uv run python -m pytest -q packages/lumistripe-core/tests/test_spectrum_flash_native.py
PYTHONPATH=packages/lumistripe-core/build/lib.linux-x86_64-cpython-312 \
  uv run python packages/lumistripe-core/benchmarks/benchmark_spectrum_flash.py
```

Use the matching `build/lib.<platform>-<python ABI>` directory for other
operating systems or Python versions.

The benchmark compares Python and native effect rendering for static and audio
standalone playback at 150, 300, and 600 LEDs. It also measures the complete
`LayeredRenderer` path at each size with zero, one, and two active effects. It
reports p50/p95/p99, effective FPS, process CPU percentage, traced peak Python
allocations, and the share of samples exceeding a 60 Hz deadline. Output parity
is asserted for every comparison.

## Host Results

Run on Linux x86-64, CPython 3.12.15, with 100 measured iterations and 20 warmup
iterations. Results are simulated in-memory rendering and exclude physical LED
output, SPI transmission, and audio capture.

| Case | Backend | p50 (µs) | p95 (µs) | p99 (µs) | Effective FPS | Traced peak (bytes) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Audio, 300 LEDs, standalone | Python | 919.258 | 1,012.182 | 1,050.573 | 1,087.8 | 1,844 |
| Audio, 300 LEDs, standalone | Native | 10.629 | 13.936 | 14.788 | 94,081.4 | 1,120 |
| Audio, 600 LEDs, standalone | Python | 1,913.235 | 2,034.713 | 2,458.555 | 522.7 | 1,844 |
| Audio, 600 LEDs, standalone | Native | 17.552 | 17.793 | 20.889 | 56,973.7 | 1,120 |
| Audio, 600 LEDs, one effect | Python | 1,904.109 | 2,081.761 | 2,103.391 | 525.2 | 2,652 |
| Audio, 600 LEDs, one effect | Native | 31.248 | 36.178 | 38.623 | 32,002.0 | 2,128 |
| Audio, 600 LEDs, two effects | Python | 1,941.698 | 2,102.780 | 2,132.806 | 514.0 | 2,652 |
| Audio, 600 LEDs, two effects | Native | 43.711 | 46.557 | 47.499 | 22,877.1 | 2,308 |

At 600 LEDs, the standalone audio p95 fell 99.1%; the two-effect layered p95 fell
97.8%. Peak traced Python allocations also fell in these cases. The zero-effect
layered cases were effectively unchanged, as expected: they do not invoke
SpectrumFlash.

These are host-only microbenchmark results, not Raspberry Pi playback
measurements. The high calculated effective FPS represents kernel throughput,
not a claim about sustainable frame rate after audio, scheduling, zones, power
limiting, and SPI output are included. Run the same benchmark on target devices
before using these results to predict appliance-level CPU or latency changes.
