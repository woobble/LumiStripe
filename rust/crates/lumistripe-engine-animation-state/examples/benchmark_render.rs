use std::alloc::{GlobalAlloc, Layout, System};
use std::hint::black_box;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::Instant;

use lumistripe_engine_animation_state::{AnimationInstance, RenderingInput};
use lumistripe_engine_effects::EffectStack;
use lumistripe_engine_render_primitives::{AudioInput, FrameBuffer};
use serde::Deserialize;
use serde_json::{Map, Value, json};

#[derive(Deserialize)]
struct EffectArguments {
    name: String,
    #[serde(default)]
    settings: Map<String, Value>,
    #[serde(default = "one")]
    intensity: f32,
}

struct CountingAllocator;

static TRACK_ALLOCATIONS: AtomicBool = AtomicBool::new(false);
static ALLOCATION_COUNT: AtomicUsize = AtomicUsize::new(0);
static ALLOCATED_BYTES: AtomicUsize = AtomicUsize::new(0);

#[global_allocator]
static ALLOCATOR: CountingAllocator = CountingAllocator;

// SAFETY: all operations delegate to the process allocator with the same layouts.
unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if TRACK_ALLOCATIONS.load(Ordering::Relaxed) && !pointer.is_null() {
            ALLOCATION_COUNT.fetch_add(1, Ordering::Relaxed);
            ALLOCATED_BYTES.fetch_add(layout.size(), Ordering::Relaxed);
        }
        pointer
    }

    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        unsafe { System.dealloc(pointer, layout) };
    }

    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        let replacement = unsafe { System.realloc(pointer, layout, new_size) };
        if TRACK_ALLOCATIONS.load(Ordering::Relaxed) && !replacement.is_null() {
            ALLOCATION_COUNT.fetch_add(1, Ordering::Relaxed);
            ALLOCATED_BYTES.fetch_add(new_size, Ordering::Relaxed);
        }
        replacement
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let animation = option(&arguments, "--animation", "rainbow");
    let pixels = number(&arguments, "--pixels", 80usize)?;
    let outputs = number(&arguments, "--outputs", 1usize)?;
    let frames = number(&arguments, "--frames", 2_000usize)?;
    let warmup = number(&arguments, "--warmup", 200usize)?;
    let delta_ns = number(&arguments, "--delta-ns", 20_000_000u64)?;
    let base_intensity = number(&arguments, "--base-intensity", 1.0f32)?;
    let max_overlay_strength = number(&arguments, "--max-overlay-strength", 0.65f32)?;
    let audio_enabled = arguments.iter().any(|argument| argument == "--audio");
    let settings: Map<String, Value> =
        serde_json::from_str(&option(&arguments, "--settings", "{}"))?;
    let effects: Vec<EffectArguments> =
        serde_json::from_str(&option(&arguments, "--effects", "[]"))?;
    if pixels == 0 || outputs == 0 || frames == 0 {
        return Err("--pixels, --outputs, and --frames must be positive".into());
    }

    let mut instances = Vec::with_capacity(outputs);
    let mut destinations = Vec::with_capacity(outputs);
    let mut base_frames = Vec::with_capacity(outputs);
    let mut effect_stacks = Vec::with_capacity(outputs);
    for output_index in 0..outputs {
        let mut instance = AnimationInstance::new(&animation, &settings)?;
        instance.set_random_seed(12_345u32.wrapping_add(output_index as u32));
        instances.push(instance);
        destinations.push(FrameBuffer::new(pixels)?);
        base_frames.push(if effects.is_empty() {
            None
        } else {
            Some(FrameBuffer::new(pixels)?)
        });
        if effects.is_empty() {
            effect_stacks.push(None);
        } else {
            let mut stack = EffectStack::new(max_overlay_strength)?;
            for effect in &effects {
                stack.push(&effect.name, &effect.settings, effect.intensity, pixels)?;
            }
            effect_stacks.push(Some(stack));
        }
    }
    let input = RenderingInput {
        brightness: 0.73,
        delta_ns,
        audio: audio_enabled.then_some(AudioInput {
            rms: 0.44,
            bands: [0.82, 0.64, 0.48, 0.31, 0.57, 0.26, 0.41, 0.19],
            beat: false,
            beat_strength: 0.0,
        }),
        ..RenderingInput::default()
    };
    let mut samples_ns = Vec::with_capacity(frames);

    for frame in 0..warmup {
        let mut current = input;
        current.timestamp_ns = frame as u64 * delta_ns;
        if let Some(audio) = &mut current.audio {
            audio.beat = frame % 8 == 0;
            audio.beat_strength = if audio.beat { 0.91 } else { 0.12 };
        }
        for output_index in 0..outputs {
            render(
                &mut instances[output_index],
                &mut effect_stacks[output_index],
                &mut base_frames[output_index],
                &mut destinations[output_index],
                current,
                base_intensity,
            )?;
        }
    }

    ALLOCATION_COUNT.store(0, Ordering::Relaxed);
    ALLOCATED_BYTES.store(0, Ordering::Relaxed);
    TRACK_ALLOCATIONS.store(true, Ordering::Release);
    for frame in 0..frames {
        let mut current = input;
        current.timestamp_ns = (warmup + frame) as u64 * delta_ns;
        if let Some(audio) = &mut current.audio {
            audio.beat = frame % 8 == 0;
            audio.beat_strength = if audio.beat { 0.91 } else { 0.12 };
        }
        let started = Instant::now();
        for output_index in 0..outputs {
            render(
                &mut instances[output_index],
                &mut effect_stacks[output_index],
                &mut base_frames[output_index],
                &mut destinations[output_index],
                current,
                base_intensity,
            )?;
        }
        samples_ns.push(started.elapsed().as_nanos() as u64);
        for destination in &destinations {
            black_box(destination.as_bytes());
        }
    }
    TRACK_ALLOCATIONS.store(false, Ordering::Release);

    samples_ns.sort_unstable();
    let total_ns: u128 = samples_ns.iter().map(|sample| u128::from(*sample)).sum();
    let output = json!({
        "renderer": "rust",
        "animation": animation,
        "effects": effects.iter().map(|effect| &effect.name).collect::<Vec<_>>(),
        "audio": audio_enabled,
        "outputs": outputs,
        "pixels": pixels,
        "total_pixels": pixels * outputs,
        "frames": frames,
        "warmup_frames": warmup,
        "mean_frame_ns": total_ns as f64 / frames as f64,
        "p50_frame_ns": percentile(&samples_ns, 0.50),
        "p95_frame_ns": percentile(&samples_ns, 0.95),
        "p99_frame_ns": percentile(&samples_ns, 0.99),
        "allocations_per_frame": ALLOCATION_COUNT.load(Ordering::Relaxed) as f64 / frames as f64,
        "allocated_bytes_per_frame": ALLOCATED_BYTES.load(Ordering::Relaxed) as f64 / frames as f64,
        "allocations_per_output_frame": ALLOCATION_COUNT.load(Ordering::Relaxed) as f64 / (frames * outputs) as f64,
        "allocated_bytes_per_output_frame": ALLOCATED_BYTES.load(Ordering::Relaxed) as f64 / (frames * outputs) as f64,
    });
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

fn render(
    instance: &mut AnimationInstance,
    effect_stack: &mut Option<EffectStack>,
    base_frame: &mut Option<FrameBuffer>,
    destination: &mut FrameBuffer,
    input: RenderingInput,
    base_intensity: f32,
) -> Result<(), Box<dyn std::error::Error>> {
    if let Some(stack) = effect_stack {
        let base = base_frame
            .as_mut()
            .expect("effect base frame was allocated");
        instance.render_next(input, base)?;
        stack.render_and_composite(base, destination, input, base_intensity)?;
    } else {
        instance.render_next(input, destination)?;
    }
    Ok(())
}

fn one() -> f32 {
    1.0
}

fn option(arguments: &[String], name: &str, default: &str) -> String {
    arguments
        .windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| pair[1].clone())
        .unwrap_or_else(|| default.to_owned())
}

fn number<T: std::str::FromStr>(
    arguments: &[String],
    name: &str,
    default: T,
) -> Result<T, Box<dyn std::error::Error>>
where
    T::Err: std::error::Error + 'static,
{
    let Some(value) = arguments
        .windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| &pair[1])
    else {
        return Ok(default);
    };
    Ok(value.parse()?)
}

fn percentile(values: &[u64], quantile: f64) -> u64 {
    let index = ((values.len() as f64 * quantile).ceil() as usize)
        .saturating_sub(1)
        .min(values.len().saturating_sub(1));
    values.get(index).copied().unwrap_or_default()
}
