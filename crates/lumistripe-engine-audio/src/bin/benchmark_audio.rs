use lumistripe_engine_audio::{
    AUDIO_HOP_SIZE, AUDIO_SAMPLE_RATE, AudioBeatEvent, AudioConfig, AudioPcmBatch, AudioProcessor,
    MAX_AUDIO_BEAT_EVENTS, MAX_CAPTURE_BATCH_SAMPLES, MusicStructureTracker, PcmIngressReceiver,
    PcmIngressSender, bounded_pcm_ingress,
};
use rtrb::{Consumer, Producer, RingBuffer};
use serde::Serialize;
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Instant;

struct CountingAllocator;

static TRACK_ALLOCATIONS: AtomicBool = AtomicBool::new(false);
static ALLOCATION_COUNT: AtomicU64 = AtomicU64::new(0);

#[global_allocator]
static ALLOCATOR: CountingAllocator = CountingAllocator;

unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if TRACK_ALLOCATIONS.load(Ordering::Relaxed) {
            ALLOCATION_COUNT.fetch_add(1, Ordering::Relaxed);
        }
        // SAFETY: this delegates the exact layout and result contract to the
        // process's system allocator.
        unsafe { System.alloc(layout) }
    }

    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        // SAFETY: `pointer` and `layout` came from the corresponding allocation.
        unsafe { System.dealloc(pointer, layout) };
    }

    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        if TRACK_ALLOCATIONS.load(Ordering::Relaxed) {
            ALLOCATION_COUNT.fetch_add(1, Ordering::Relaxed);
        }
        // SAFETY: this delegates the exact allocation contract to the system allocator.
        unsafe { System.realloc(pointer, layout, new_size) }
    }
}

#[derive(Serialize)]
struct BenchmarkReport {
    platform: &'static str,
    architecture: &'static str,
    rust_audio_version: &'static str,
    iterations_512_sample_hops: usize,
    input: &'static str,
    measurement_scope: &'static str,
    process_peak_rss_mb: Option<f64>,
    results: Vec<PipelineResult>,
}

#[derive(Serialize)]
struct PipelineResult {
    pipeline: &'static str,
    hops_per_call: usize,
    calls: usize,
    simulated_audio_seconds: f64,
    wall_seconds: f64,
    realtime_headroom_x: f64,
    cpu_utilization_percent: Option<f64>,
    feed_p50_ms: f64,
    feed_p95_ms: f64,
    feed_p99_ms: f64,
    steady_state_allocations: u64,
    allocations_per_call: f64,
}

fn main() {
    let iterations = std::env::args()
        .skip_while(|argument| argument != "--iterations")
        .nth(1)
        .map(|value| {
            value
                .parse::<usize>()
                .expect("--iterations must be an integer")
        })
        .unwrap_or(4096);
    assert!(iterations >= 64, "--iterations must be at least 64");

    let mut results = Vec::new();
    for hops_per_call in [1, 4, 16] {
        let batch_samples = AUDIO_HOP_SIZE * hops_per_call;
        let calls = iterations * AUDIO_HOP_SIZE / batch_samples;
        let input = waveform(batch_samples);
        results.push(measure("dsp", hops_per_call, calls, &input, false));
        results.push(measure(
            "dsp_plus_structure",
            hops_per_call,
            calls,
            &input,
            true,
        ));
        results.push(measure_transport(
            "monitor_capture_queue_plus_dsp",
            hops_per_call,
            calls,
            &input,
            false,
        ));
        results.push(measure_transport(
            "decoded_pcm_conversion_queue_plus_dsp",
            hops_per_call,
            calls,
            &input,
            true,
        ));
    }

    let report = BenchmarkReport {
        platform: std::env::consts::OS,
        architecture: std::env::consts::ARCH,
        rust_audio_version: env!("CARGO_PKG_VERSION"),
        iterations_512_sample_hops: iterations,
        input: "0.32 amplitude 80 Hz plus 0.08 amplitude 1600 Hz at 44100 Hz",
        measurement_scope: "synthetic software path only; excludes Spotify decoding, PipeWire scheduling, hardware playback/capture, and LED output",
        process_peak_rss_mb: peak_rss_mb(),
        results,
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&report).expect("serialize report")
    );
}

enum TransportHarness {
    Monitor {
        producer: Producer<AudioPcmBatch>,
        consumer: Consumer<AudioPcmBatch>,
        interleaved: Vec<f32>,
    },
    Direct {
        sender: PcmIngressSender,
        receiver: PcmIngressReceiver,
        generation: u64,
        interleaved: Vec<f64>,
    },
}

impl TransportHarness {
    fn new(direct: bool, input: &[f32]) -> Self {
        if direct {
            let (sender, receiver) = bounded_pcm_ingress();
            let generation = sender.set_enabled(true);
            let interleaved = input
                .iter()
                .flat_map(|sample| [f64::from(*sample), f64::from(*sample)])
                .collect();
            Self::Direct {
                sender,
                receiver,
                generation,
                interleaved,
            }
        } else {
            let (producer, consumer) = RingBuffer::new(8);
            let interleaved = input.iter().flat_map(|sample| [*sample, *sample]).collect();
            Self::Monitor {
                producer,
                consumer,
                interleaved,
            }
        }
    }

    fn transfer(
        &mut self,
        input: &[f32],
        sample_position: u64,
        input_offset_samples: usize,
    ) -> AudioPcmBatch {
        let timestamp_ns = sample_position
            .saturating_add(input.len() as u64)
            .saturating_mul(1_000_000_000)
            / u64::from(AUDIO_SAMPLE_RATE as u32);
        match self {
            Self::Monitor {
                producer,
                consumer,
                interleaved,
            } => {
                let mut batch = AudioPcmBatch {
                    samples: [0.0; MAX_CAPTURE_BATCH_SAMPLES],
                    len: input.len(),
                    sample_rate: AUDIO_SAMPLE_RATE as u32,
                    channels: 2,
                    capture_end_ns: timestamp_ns,
                    submitted_at_ns: timestamp_ns,
                    sample_position,
                    stream_generation: 0,
                    dropped_samples_before: 0,
                };
                let interleaved_start = input_offset_samples * 2;
                for (frame, channels) in interleaved
                    [interleaved_start..interleaved_start + input.len() * 2]
                    .chunks_exact(2)
                    .enumerate()
                {
                    batch.samples[frame] = (channels[0] + channels[1]) * 0.5;
                }
                producer
                    .push(batch)
                    .expect("synthetic monitor queue has room");
                consumer.pop().expect("synthetic monitor batch was queued")
            }
            Self::Direct {
                sender,
                receiver,
                generation,
                interleaved,
            } => {
                let input_start = input_offset_samples * 2;
                let input_end = input_start + input.len() * 2;
                let accepted = sender.submit_interleaved_f64_at(
                    &interleaved[input_start..input_end],
                    AUDIO_SAMPLE_RATE as u32,
                    2,
                    *generation,
                    sample_position,
                    timestamp_ns,
                );
                assert!(accepted, "synthetic direct PCM queue has room");
                receiver
                    .try_pop()
                    .expect("synthetic direct PCM batch was queued")
            }
        }
    }
}

fn measure_transport(
    pipeline: &'static str,
    hops_per_call: usize,
    calls: usize,
    input: &[f32],
    direct: bool,
) -> PipelineResult {
    let mut processor = AudioProcessor::new(AudioConfig::default(), AUDIO_SAMPLE_RATE)
        .expect("valid audio processor configuration");
    let mut tracker = MusicStructureTracker::new();
    let mut transport = TransportHarness::new(direct, input);
    let mut sample_position = 0_u64;
    for _ in 0..32 {
        process_transport_call(
            &mut transport,
            input,
            &mut processor,
            &mut tracker,
            &mut sample_position,
        );
    }

    let mut elapsed_ns = Vec::with_capacity(calls);
    let cpu_started = thread_cpu_time_ns();
    let started = Instant::now();
    for _ in 0..calls {
        let call_started = Instant::now();
        process_transport_call(
            &mut transport,
            input,
            &mut processor,
            &mut tracker,
            &mut sample_position,
        );
        elapsed_ns.push(call_started.elapsed().as_nanos() as u64);
    }
    let wall_seconds = started.elapsed().as_secs_f64();
    let cpu_seconds = cpu_started
        .zip(thread_cpu_time_ns())
        .map(|(before, after)| after.saturating_sub(before) as f64 / 1_000_000_000.0);
    let simulated_audio_seconds = (calls * input.len()) as f64 / AUDIO_SAMPLE_RATE as f64;
    let (steady_state_allocations, allocations_per_call) =
        count_transport_allocations(input, calls, direct);
    elapsed_ns.sort_unstable();

    PipelineResult {
        pipeline,
        hops_per_call,
        calls,
        simulated_audio_seconds,
        wall_seconds,
        realtime_headroom_x: simulated_audio_seconds / wall_seconds.max(1e-9),
        cpu_utilization_percent: cpu_seconds
            .map(|seconds| seconds / wall_seconds.max(1e-9) * 100.0),
        feed_p50_ms: percentile_ms(&elapsed_ns, 50),
        feed_p95_ms: percentile_ms(&elapsed_ns, 95),
        feed_p99_ms: percentile_ms(&elapsed_ns, 99),
        steady_state_allocations,
        allocations_per_call,
    }
}

fn count_transport_allocations(input: &[f32], calls: usize, direct: bool) -> (u64, f64) {
    let mut processor = AudioProcessor::new(AudioConfig::default(), AUDIO_SAMPLE_RATE)
        .expect("valid audio processor configuration");
    let mut tracker = MusicStructureTracker::new();
    let mut transport = TransportHarness::new(direct, input);
    let mut sample_position = 0_u64;
    for _ in 0..32 {
        process_transport_call(
            &mut transport,
            input,
            &mut processor,
            &mut tracker,
            &mut sample_position,
        );
    }

    let before = ALLOCATION_COUNT.load(Ordering::Relaxed);
    TRACK_ALLOCATIONS.store(true, Ordering::Relaxed);
    for _ in 0..calls {
        process_transport_call(
            &mut transport,
            input,
            &mut processor,
            &mut tracker,
            &mut sample_position,
        );
    }
    TRACK_ALLOCATIONS.store(false, Ordering::Relaxed);
    let allocations = ALLOCATION_COUNT
        .load(Ordering::Relaxed)
        .saturating_sub(before);
    (allocations, allocations as f64 / calls as f64)
}

fn process_transport_call(
    transport: &mut TransportHarness,
    input: &[f32],
    processor: &mut AudioProcessor,
    tracker: &mut MusicStructureTracker,
    sample_position: &mut u64,
) {
    for (batch_index, samples) in input.chunks(MAX_CAPTURE_BATCH_SAMPLES).enumerate() {
        let batch = transport.transfer(
            samples,
            *sample_position,
            batch_index * MAX_CAPTURE_BATCH_SAMPLES,
        );
        process(
            processor,
            tracker,
            sample_position,
            &batch.samples[..batch.len],
            true,
        );
    }
}

fn measure(
    pipeline: &'static str,
    hops_per_call: usize,
    calls: usize,
    input: &[f32],
    include_structure: bool,
) -> PipelineResult {
    let mut processor = AudioProcessor::new(AudioConfig::default(), AUDIO_SAMPLE_RATE)
        .expect("valid audio processor configuration");
    let mut tracker = MusicStructureTracker::new();
    let mut sample_position = 0_u64;
    for _ in 0..32 {
        process(
            &mut processor,
            &mut tracker,
            &mut sample_position,
            input,
            include_structure,
        );
    }

    let mut elapsed_ns = Vec::with_capacity(calls);
    let cpu_started = thread_cpu_time_ns();
    let started = Instant::now();
    for _ in 0..calls {
        let call_started = Instant::now();
        process(
            &mut processor,
            &mut tracker,
            &mut sample_position,
            input,
            include_structure,
        );
        elapsed_ns.push(call_started.elapsed().as_nanos() as u64);
    }
    let wall_seconds = started.elapsed().as_secs_f64();
    let cpu_seconds = cpu_started
        .zip(thread_cpu_time_ns())
        .map(|(before, after)| after.saturating_sub(before) as f64 / 1_000_000_000.0);
    let simulated_audio_seconds = (calls * input.len()) as f64 / AUDIO_SAMPLE_RATE as f64;
    let (steady_state_allocations, allocations_per_call) =
        count_allocations(input, calls, include_structure);
    elapsed_ns.sort_unstable();

    PipelineResult {
        pipeline,
        hops_per_call,
        calls,
        simulated_audio_seconds,
        wall_seconds,
        realtime_headroom_x: simulated_audio_seconds / wall_seconds.max(1e-9),
        cpu_utilization_percent: cpu_seconds
            .map(|seconds| seconds / wall_seconds.max(1e-9) * 100.0),
        feed_p50_ms: percentile_ms(&elapsed_ns, 50),
        feed_p95_ms: percentile_ms(&elapsed_ns, 95),
        feed_p99_ms: percentile_ms(&elapsed_ns, 99),
        steady_state_allocations,
        allocations_per_call,
    }
}

fn count_allocations(input: &[f32], calls: usize, include_structure: bool) -> (u64, f64) {
    let mut processor = AudioProcessor::new(AudioConfig::default(), AUDIO_SAMPLE_RATE)
        .expect("valid audio processor configuration");
    let mut tracker = MusicStructureTracker::new();
    let mut sample_position = 0_u64;
    for _ in 0..32 {
        process(
            &mut processor,
            &mut tracker,
            &mut sample_position,
            input,
            include_structure,
        );
    }

    let before = ALLOCATION_COUNT.load(Ordering::Relaxed);
    TRACK_ALLOCATIONS.store(true, Ordering::Relaxed);
    for _ in 0..calls {
        process(
            &mut processor,
            &mut tracker,
            &mut sample_position,
            input,
            include_structure,
        );
    }
    TRACK_ALLOCATIONS.store(false, Ordering::Relaxed);
    let allocations = ALLOCATION_COUNT
        .load(Ordering::Relaxed)
        .saturating_sub(before);
    (allocations, allocations as f64 / calls as f64)
}

fn process(
    processor: &mut AudioProcessor,
    tracker: &mut MusicStructureTracker,
    sample_position: &mut u64,
    input: &[f32],
    include_structure: bool,
) {
    *sample_position += input.len() as u64;
    let capture_end_seconds = *sample_position as f64 / AUDIO_SAMPLE_RATE as f64;
    if include_structure {
        processor
            .feed_samples_with_observer(input, capture_end_seconds, |frame, features| {
                tracker
                    .update(&features, frame.timestamp)
                    .expect("benchmark timestamps are monotonic");
            })
            .expect("valid benchmark samples");
    } else {
        processor
            .feed_samples(input, capture_end_seconds)
            .expect("valid benchmark samples");
    }
    std::hint::black_box(processor.frame());
    let mut beat_events = [AudioBeatEvent::default(); MAX_AUDIO_BEAT_EVENTS];
    let beat_event_count = processor.drain_beat_events_into(&mut beat_events);
    std::hint::black_box(&beat_events[..beat_event_count]);
}

fn waveform(sample_count: usize) -> Vec<f32> {
    (0..sample_count)
        .map(|index| {
            let time = index as f32 / AUDIO_SAMPLE_RATE;
            0.32 * (std::f32::consts::TAU * 80.0 * time).sin()
                + 0.08 * (std::f32::consts::TAU * 1_600.0 * time).sin()
        })
        .collect()
}

fn percentile_ms(samples: &[u64], percentile: usize) -> f64 {
    let index = samples
        .len()
        .saturating_mul(percentile)
        .div_ceil(100)
        .saturating_sub(1)
        .min(samples.len().saturating_sub(1));
    samples.get(index).copied().unwrap_or(0) as f64 / 1_000_000.0
}

fn thread_cpu_time_ns() -> Option<u64> {
    #[cfg(target_os = "linux")]
    {
        let mut time = libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        // SAFETY: `time` is a valid writable timespec and the Linux thread CPU
        // clock is supported on the documented benchmark platforms.
        let result = unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, &mut time) };
        if result == 0 {
            return Some(
                (time.tv_sec as u64)
                    .saturating_mul(1_000_000_000)
                    .saturating_add(time.tv_nsec as u64),
            );
        }
    }
    None
}

fn peak_rss_mb() -> Option<f64> {
    #[cfg(target_os = "linux")]
    {
        let mut usage = std::mem::MaybeUninit::<libc::rusage>::zeroed();
        // SAFETY: `usage` points to writable storage for a `rusage` value.
        let result = unsafe { libc::getrusage(libc::RUSAGE_SELF, usage.as_mut_ptr()) };
        if result == 0 {
            // SAFETY: successful getrusage initialized the structure.
            let usage = unsafe { usage.assume_init() };
            return Some(usage.ru_maxrss as f64 / 1024.0);
        }
    }
    None
}
