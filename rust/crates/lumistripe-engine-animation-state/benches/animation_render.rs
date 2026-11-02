use criterion::{BenchmarkId, Criterion, Throughput, criterion_group, criterion_main};
use lumistripe_engine_animation_state::{AnimationInstance, RenderingInput};
use lumistripe_engine_render_primitives::{AudioInput, FrameBuffer};
use serde_json::Map;
use std::hint::black_box;
use std::time::Duration;

fn benchmark_animation_frames(criterion: &mut Criterion) {
    let mut group = criterion.benchmark_group("rust_animation_frame");
    group.sample_size(10);
    group.warm_up_time(Duration::from_secs(1));
    group.measurement_time(Duration::from_secs(1));

    for pixel_count in [150, 300, 600] {
        group.throughput(Throughput::Elements(pixel_count as u64));
        for animation_name in ["rainbow_cycle", "comet", "shockwave", "drop_explosion"] {
            let mut animation =
                AnimationInstance::new(animation_name, &Map::new()).expect("known animation");
            animation.set_random_seed(7);
            let mut frame = FrameBuffer::new(pixel_count).expect("valid pixel count");
            let input = RenderingInput {
                timestamp_ns: 1_000_000_000,
                delta_ns: 16_666_667,
                brightness: 1.0,
                audio: Some(AudioInput {
                    rms: 0.55,
                    bands: [0.85, 0.72, 0.58, 0.44, 0.31, 0.23, 0.16, 0.1],
                    beat: true,
                    beat_strength: 0.9,
                }),
            };

            group.bench_with_input(
                BenchmarkId::new(animation_name, pixel_count),
                &pixel_count,
                |bencher, _| {
                    bencher.iter(|| {
                        animation
                            .render_next(black_box(input), &mut frame)
                            .expect("animation renders");
                        black_box(frame.as_bytes());
                    });
                },
            );
        }
    }
    group.finish();
}

criterion_group!(benches, benchmark_animation_frames);
criterion_main!(benches);
