use criterion::{BenchmarkId, Criterion, Throughput, criterion_group, criterion_main};
use lumistripe_engine_audio::{AudioConfig, AudioProcessor, MusicStructureTracker};
use std::hint::black_box;

const SAMPLE_RATE: f32 = 44_100.0;
const HOP_SIZE: usize = 512;

fn audio_pipeline(c: &mut Criterion) {
    let input: [f32; HOP_SIZE] = std::array::from_fn(|index| {
        let time = index as f32 / SAMPLE_RATE;
        0.32 * (std::f32::consts::TAU * 80.0 * time).sin()
            + 0.08 * (std::f32::consts::TAU * 1_600.0 * time).sin()
    });
    let mut group = c.benchmark_group("audio_pipeline_512_sample_hop");
    group.throughput(Throughput::Elements(HOP_SIZE as u64));

    group.bench_function(BenchmarkId::new("dsp", HOP_SIZE), |bencher| {
        let mut processor = AudioProcessor::new(AudioConfig::default(), SAMPLE_RATE).unwrap();
        let mut sample_position = 0_u64;
        bencher.iter(|| {
            sample_position += HOP_SIZE as u64;
            processor
                .feed_samples(
                    black_box(&input),
                    sample_position as f64 / SAMPLE_RATE as f64,
                )
                .unwrap();
            black_box(processor.frame());
        });
    });

    group.bench_function(
        BenchmarkId::new("dsp_plus_structure", HOP_SIZE),
        |bencher| {
            let mut processor = AudioProcessor::new(AudioConfig::default(), SAMPLE_RATE).unwrap();
            let mut tracker = MusicStructureTracker::new();
            let mut sample_position = 0_u64;
            bencher.iter(|| {
                sample_position += HOP_SIZE as u64;
                processor
                    .feed_samples_with_observer(
                        black_box(&input),
                        sample_position as f64 / SAMPLE_RATE as f64,
                        |frame, features| {
                            black_box(
                                tracker
                                    .update(&features, frame.timestamp)
                                    .expect("benchmark timestamps are monotonic"),
                            );
                        },
                    )
                    .unwrap();
                black_box(processor.frame());
            });
        },
    );
    group.finish();
}

criterion_group!(benches, audio_pipeline);
criterion_main!(benches);
