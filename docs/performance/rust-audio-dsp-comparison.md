# Rust audio DSP comparison

## Synthetic x86_64 run

Measured 2026-11-02 on Linux 7.2.11, x86_64, glibc 2.44, Python 3.12.15. The
Python run used LumiStripe's native-C `_audio` extension. Both programs used
the same repeated 44.1 kHz PCM batch: a 0.32-amplitude 80 Hz tone plus a
0.08-amplitude 1.6 kHz tone. Each batch size was warmed for 32 calls and then
processed 4096 total 512-sample hops. This is DSP throughput evidence, not a
live capture measurement; the host has no `/dev/snd` device.

Per-call latency is milliseconds. Rust's third column includes one
`MusicStructureTracker` update for each completed analysis frame.

| Hops per call | Python/native-C p50 / p95 / p99 | Rust DSP p50 / p95 / p99 | Rust DSP + structure p50 / p95 / p99 | Python C p95 / Rust DSP+structure p95 |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.0253 / 0.0273 / 0.0373 | 0.0078 / 0.0085 / 0.0096 | 0.0086 / 0.0096 / 0.0109 | 2.8× |
| 4 | 0.0755 / 0.0777 / 0.0790 | 0.0311 / 0.0342 / 0.0370 | 0.0340 / 0.0374 / 0.0401 | 2.1× |
| 16 | 0.2838 / 0.2878 / 0.2940 | 0.1369 / 0.1422 / 0.1540 | 0.1603 / 0.1719 / 0.1750 | 1.7× |

The Rust DSP and DSP-plus-structure passes each recorded zero allocator calls
over the measured 4096 calls at all three batch sizes. Beat events are drained
into a fixed-size stack buffer. Python reached 447×, 612×, and 653× real-time
headroom for one, four, and sixteen hops; Rust DSP-plus-structure reached
1320×, 1344×, and 1149×. CPU utilization was approximately one core for both.
Peak process RSS was 46.4 MiB for Python/native-C and 56.0 MiB for Rust in this
run; these process totals include interpreter/runtime overhead and do not
measure incremental audio-state memory. Python `tracemalloc` observed 2.4 KiB
to 14.6 KiB of transient Python-managed peak bytes; it cannot see native C
allocator activity.

The Rust p95 values are lower in this synthetic test. These results do not
establish live PortAudio-versus-CPAL callback latency, device compatibility,
capture jitter, or Raspberry Pi performance. Run the capture harnesses on the
same device and record the selected host, input, sample rate, callback period,
CPU governor, temperature, and source revision before comparing capture paths.

## Reproduction

Run both harnesses on the same machine and keep the iteration count fixed:

```bash
UV_CACHE_DIR=/tmp/lumistripe-uv-cache uv run python scripts/benchmark_audio_dsp.py \
  --iterations 4096
CARGO_HOME=/tmp/lumistripe-cargo-home cargo run --release \
  --manifest-path rust/Cargo.toml -p lumistripe-engine-audio \
  --bin benchmark_audio -- --iterations 4096
```

The detailed JSON includes wall time, real-time headroom, CPU utilization,
process peak RSS, p50/p95/p99 feed latency, and Rust allocator counts. Python
heap figures are diagnostic only because `tracemalloc` does not instrument the
native C extension.
