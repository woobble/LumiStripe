//! Linux audio-device discovery and callback-safe capture.
//!
//! CPAL provides ALSA directly and optional PulseAudio and PipeWire hosts. Input
//! callbacks downmix into fixed-size batches and hand them to a bounded SPSC ring;
//! processing, recovery, and device changes stay on the control worker.

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{
    BufferSize, Device, FromSample, Host, HostId, InputCallbackInfo, Sample, SampleFormat,
    SizedSample, Stream, StreamConfig,
};
use rtrb::{Consumer, Producer, RingBuffer};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

const CAPTURE_QUEUE_CAPACITY: usize = 8;
const CAPTURE_TIMING_WINDOW: usize = 64;
pub const MAX_CAPTURE_BATCH_SAMPLES: usize = 2048;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct InputAudioDevice {
    pub selector: String,
    pub host: String,
    pub name: String,
    pub is_default: bool,
    pub sample_rate: u32,
    pub channels: u16,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct AudioCaptureStats {
    pub callback_count: u64,
    pub callback_errors: u64,
    pub overflowed_batches: u64,
    pub dropped_samples: u64,
    pub last_callback_ns: Option<u64>,
    pub last_error_ns: Option<u64>,
    pub capture_latency_p50_ns: u64,
    pub capture_latency_p95_ns: u64,
    pub capture_latency_p99_ns: u64,
    pub callback_duration_p50_ns: u64,
    pub callback_duration_p95_ns: u64,
    pub callback_duration_p99_ns: u64,
    pub callback_interval_jitter_p95_ns: u64,
}

#[derive(Clone, Copy, Debug)]
pub struct CapturedAudioBatch {
    pub samples: [f32; MAX_CAPTURE_BATCH_SAMPLES],
    pub len: usize,
    pub capture_end_ns: u64,
    pub sample_position: u64,
    pub dropped_samples_before: u64,
}

impl CapturedAudioBatch {
    fn empty() -> Self {
        Self {
            samples: [0.0; MAX_CAPTURE_BATCH_SAMPLES],
            len: 0,
            capture_end_ns: 0,
            sample_position: 0,
            dropped_samples_before: 0,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AudioCaptureError(pub String);

impl std::fmt::Display for AudioCaptureError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for AudioCaptureError {}

struct CaptureCounters {
    callback_count: AtomicU64,
    callback_errors: AtomicU64,
    overflowed_batches: AtomicU64,
    dropped_samples: AtomicU64,
    pending_dropped_samples: AtomicU64,
    sample_position: AtomicU64,
    last_callback_ns: AtomicU64,
    last_error_ns: AtomicU64,
    callback_timing_index: AtomicU64,
    last_callback_expected_interval_ns: AtomicU64,
    callback_capture_latency_ns: [AtomicU64; CAPTURE_TIMING_WINDOW],
    callback_duration_ns: [AtomicU64; CAPTURE_TIMING_WINDOW],
    callback_interval_jitter_ns: [AtomicU64; CAPTURE_TIMING_WINDOW],
}

impl Default for CaptureCounters {
    fn default() -> Self {
        Self {
            callback_count: AtomicU64::new(0),
            callback_errors: AtomicU64::new(0),
            overflowed_batches: AtomicU64::new(0),
            dropped_samples: AtomicU64::new(0),
            pending_dropped_samples: AtomicU64::new(0),
            sample_position: AtomicU64::new(0),
            last_callback_ns: AtomicU64::new(0),
            last_error_ns: AtomicU64::new(0),
            callback_timing_index: AtomicU64::new(0),
            last_callback_expected_interval_ns: AtomicU64::new(0),
            callback_capture_latency_ns: std::array::from_fn(|_| AtomicU64::new(0)),
            callback_duration_ns: std::array::from_fn(|_| AtomicU64::new(0)),
            callback_interval_jitter_ns: std::array::from_fn(|_| AtomicU64::new(0)),
        }
    }
}

impl CaptureCounters {
    fn stats(&self) -> AudioCaptureStats {
        AudioCaptureStats {
            callback_count: self.callback_count.load(Ordering::Relaxed),
            callback_errors: self.callback_errors.load(Ordering::Relaxed),
            overflowed_batches: self.overflowed_batches.load(Ordering::Relaxed),
            dropped_samples: self.dropped_samples.load(Ordering::Relaxed),
            last_callback_ns: nonzero(self.last_callback_ns.load(Ordering::Relaxed)),
            last_error_ns: nonzero(self.last_error_ns.load(Ordering::Relaxed)),
            capture_latency_p50_ns: sample_percentile(&self.callback_capture_latency_ns, 50),
            capture_latency_p95_ns: sample_percentile(&self.callback_capture_latency_ns, 95),
            capture_latency_p99_ns: sample_percentile(&self.callback_capture_latency_ns, 99),
            callback_duration_p50_ns: sample_percentile(&self.callback_duration_ns, 50),
            callback_duration_p95_ns: sample_percentile(&self.callback_duration_ns, 95),
            callback_duration_p99_ns: sample_percentile(&self.callback_duration_ns, 99),
            callback_interval_jitter_p95_ns: sample_percentile(
                &self.callback_interval_jitter_ns,
                95,
            ),
        }
    }
}

/// A running CPAL stream and its single-consumer PCM queue.
pub struct AudioCapture {
    _stream: Stream,
    consumer: Consumer<CapturedAudioBatch>,
    counters: Arc<CaptureCounters>,
    pub device: InputAudioDevice,
}

impl AudioCapture {
    pub fn open(selector: Option<&str>) -> Result<Self, AudioCaptureError> {
        let (device, host_id, description) = select_input_device(selector)?;
        let supported = device
            .default_input_config()
            .map_err(|error| AudioCaptureError(format!("could not read input format: {error}")))?;
        let sample_rate = supported.sample_rate();
        let channels = supported.channels();
        let stream_config = StreamConfig {
            channels,
            sample_rate,
            // Keep the host's period choice. The DSP's fixed 512-sample hop is
            // independent of callback size, and server hosts may reject fixed periods.
            buffer_size: BufferSize::Default,
        };
        let (producer, consumer) = RingBuffer::new(CAPTURE_QUEUE_CAPACITY);
        let counters = Arc::new(CaptureCounters::default());
        let error_counters = Arc::clone(&counters);
        let stream = build_stream(
            &device,
            stream_config,
            supported.sample_format(),
            producer,
            Arc::clone(&counters),
            move |_error| {
                error_counters
                    .callback_errors
                    .fetch_add(1, Ordering::Relaxed);
                error_counters
                    .last_error_ns
                    .store(monotonic_ns(), Ordering::Relaxed);
            },
        )?;
        stream
            .play()
            .map_err(|error| AudioCaptureError(format!("could not start input stream: {error}")))?;
        Ok(Self {
            _stream: stream,
            consumer,
            counters,
            device: InputAudioDevice {
                selector: description.0,
                host: host_id.name().to_owned(),
                name: description.1,
                is_default: description.2,
                sample_rate,
                channels,
            },
        })
    }

    pub fn try_pop(&mut self) -> Option<CapturedAudioBatch> {
        self.consumer.pop().ok()
    }

    pub fn stats(&self) -> AudioCaptureStats {
        self.counters.stats()
    }

    pub fn sample_rate(&self) -> u32 {
        self.device.sample_rate
    }
}

pub fn enumerate_input_devices() -> Result<Vec<InputAudioDevice>, AudioCaptureError> {
    let hosts = ordered_hosts()?;
    let mut result = Vec::new();
    for (host_id, host) in hosts {
        let default_id = host
            .default_input_device()
            .and_then(|device| device.id().ok())
            .map(|id| id.id().to_owned());
        let devices = match host.input_devices() {
            Ok(devices) => devices,
            Err(_) => continue,
        };
        for device in devices {
            let name = device.to_string();
            let Ok(id) = device.id() else {
                continue;
            };
            let Ok(config) = device.default_input_config() else {
                continue;
            };
            let id = id.id().to_owned();
            result.push(InputAudioDevice {
                selector: format!("{}:{id}", host_id.name()),
                host: host_id.name().to_owned(),
                name,
                is_default: default_id.as_deref() == Some(id.as_str()),
                sample_rate: config.sample_rate(),
                channels: config.channels(),
            });
        }
    }
    Ok(result)
}

fn select_input_device(
    selector: Option<&str>,
) -> Result<(Device, HostId, (String, String, bool)), AudioCaptureError> {
    let hosts = ordered_hosts()?;
    let selector = selector.map(str::trim).filter(|value| !value.is_empty());
    let mut candidates: Vec<(Device, HostId, (String, String, bool))> = Vec::new();
    if selector.is_none() {
        for (host_id, host) in &hosts {
            let default = host
                .default_input_device()
                .and_then(|device| device.id().ok().map(|id| id.id().to_owned()));
            if let Some(device) = host.default_input_device() {
                return describe_selected_device(device, *host_id, default.as_deref());
            }
            let devices = match host.input_devices() {
                Ok(devices) => devices,
                Err(_) => continue,
            };
            for device in devices {
                let name = device.to_string();
                let id = device.id().ok().map(|id| id.id().to_owned());
                let is_default = id
                    .as_deref()
                    .is_some_and(|id| Some(id) == default.as_deref());
                let id = id.unwrap_or_else(|| name.clone());
                candidates.push((
                    device,
                    *host_id,
                    (format!("{}:{id}", host_id.name()), name, is_default),
                ));
            }
        }
        return candidates
            .into_iter()
            .next()
            .ok_or_else(|| AudioCaptureError("no audio input device is available".to_owned()));
    }

    let selector = selector.expect("selector was checked above");
    let lowered = selector.to_lowercase();
    let mut matching = Vec::new();
    let mut global_index = 0_usize;
    for (host_id, host) in hosts {
        let default = host
            .default_input_device()
            .and_then(|device| device.id().ok().map(|id| id.id().to_owned()));
        let devices = match host.input_devices() {
            Ok(devices) => devices,
            Err(_) => continue,
        };
        for device in devices {
            let index = global_index;
            global_index += 1;
            let name = device.to_string();
            let index_match = selector.parse::<usize>().ok() == Some(index);
            let id = device.id().ok().map(|id| id.id().to_owned());
            let is_default = id
                .as_deref()
                .is_some_and(|id| Some(id) == default.as_deref());
            let description = (
                id.as_deref()
                    .map_or_else(|| name.clone(), |id| format!("{}:{id}", host_id.name())),
                name.clone(),
                is_default,
            );
            let name_match = name.to_lowercase().contains(&lowered);
            let id_match = description.0.eq_ignore_ascii_case(selector);
            if name_match || id_match || index_match {
                // Prefer an exact name/ID over a partial match while retaining
                // host order for devices exported by multiple Linux APIs.
                let score = if name.eq_ignore_ascii_case(selector) || id_match {
                    0
                } else if index_match {
                    1
                } else {
                    2
                };
                matching.push((device, host_id, description, score));
            }
        }
    }
    matching
        .into_iter()
        .min_by_key(|(_, _, _, score)| *score)
        .map(|(device, host_id, description, _)| (device, host_id, description))
        .ok_or_else(|| AudioCaptureError(format!("no input device matches '{selector}'")))
}

fn describe_selected_device(
    device: Device,
    host_id: HostId,
    default_id: Option<&str>,
) -> Result<(Device, HostId, (String, String, bool)), AudioCaptureError> {
    let name = device.to_string();
    let id = device
        .id()
        .map_err(|error| AudioCaptureError(format!("could not read input device ID: {error}")))?;
    let is_default = default_id == Some(id.id());
    Ok((
        device,
        host_id,
        (format!("{}:{}", host_id.name(), id.id()), name, is_default),
    ))
}

fn ordered_hosts() -> Result<Vec<(HostId, Host)>, AudioCaptureError> {
    let mut ids = cpal::available_hosts();
    ids.sort_by_key(|id| {
        let name = id.name().to_ascii_lowercase();
        if name.contains("pipewire") {
            0
        } else if name.contains("pulse") {
            1
        } else if name.contains("alsa") {
            2
        } else {
            3
        }
    });
    let mut result = Vec::new();
    for id in ids {
        if let Ok(host) = cpal::host_from_id(id) {
            result.push((id, host));
        }
    }
    if result.is_empty() {
        Err(AudioCaptureError(
            "no supported audio host is available".to_owned(),
        ))
    } else {
        Ok(result)
    }
}

fn build_stream<E>(
    device: &Device,
    config: StreamConfig,
    format: SampleFormat,
    producer: Producer<CapturedAudioBatch>,
    counters: Arc<CaptureCounters>,
    error_callback: E,
) -> Result<Stream, AudioCaptureError>
where
    E: FnMut(cpal::Error) + Send + 'static,
{
    macro_rules! build {
        ($sample:ty) => {
            build_typed_stream::<$sample, E>(device, config, producer, counters, error_callback)
        };
    }
    match format {
        SampleFormat::F32 => build!(f32),
        SampleFormat::F64 => build!(f64),
        SampleFormat::I8 => build!(i8),
        SampleFormat::I16 => build!(i16),
        SampleFormat::I24 => build!(cpal::I24),
        SampleFormat::I32 => build!(i32),
        SampleFormat::I64 => build!(i64),
        SampleFormat::U8 => build!(u8),
        SampleFormat::U16 => build!(u16),
        SampleFormat::U24 => build!(cpal::U24),
        SampleFormat::U32 => build!(u32),
        SampleFormat::U64 => build!(u64),
        unsupported => Err(AudioCaptureError(format!(
            "capture sample format {unsupported:?} is not supported"
        ))),
    }
}

fn build_typed_stream<T, E>(
    device: &Device,
    config: StreamConfig,
    mut producer: Producer<CapturedAudioBatch>,
    counters: Arc<CaptureCounters>,
    error_callback: E,
) -> Result<Stream, AudioCaptureError>
where
    T: SizedSample,
    f32: FromSample<T>,
    E: FnMut(cpal::Error) + Send + 'static,
{
    let channels = usize::from(config.channels);
    let sample_rate = config.sample_rate;
    device
        .build_input_stream::<T, _, _>(
            config,
            move |input, info: &InputCallbackInfo| {
                capture_callback(input, info, channels, sample_rate, &mut producer, &counters)
            },
            error_callback,
            Some(Duration::from_secs(2)),
        )
        .map_err(|error| {
            AudioCaptureError(format!(
                "could not create {:?} capture stream: {error}",
                T::FORMAT
            ))
        })
}

fn capture_callback<T>(
    input: &[T],
    info: &InputCallbackInfo,
    channels: usize,
    sample_rate: u32,
    producer: &mut Producer<CapturedAudioBatch>,
    counters: &CaptureCounters,
) where
    T: Copy,
    f32: FromSample<T>,
{
    if channels == 0 || sample_rate == 0 {
        counters.callback_errors.fetch_add(1, Ordering::Relaxed);
        counters
            .last_error_ns
            .store(monotonic_ns(), Ordering::Relaxed);
        return;
    }
    let start_ns = monotonic_ns();
    let frames = input.len() / channels;
    let expected_interval_ns =
        (frames as u128 * 1_000_000_000_u128 / sample_rate as u128).min(u64::MAX as u128) as u64;
    let previous_callback_ns = counters.last_callback_ns.swap(start_ns, Ordering::Relaxed);
    let previous_expected_interval_ns = counters
        .last_callback_expected_interval_ns
        .swap(expected_interval_ns, Ordering::Relaxed);
    let timing_index = counters
        .callback_timing_index
        .fetch_add(1, Ordering::Relaxed);
    let timing_slot = timing_index as usize % CAPTURE_TIMING_WINDOW;
    if previous_callback_ns != 0 {
        let actual_interval_ns = start_ns.saturating_sub(previous_callback_ns);
        counters.callback_interval_jitter_ns[timing_slot].store(
            actual_interval_ns.abs_diff(previous_expected_interval_ns),
            Ordering::Relaxed,
        );
    }
    let capture_latency_ns = info
        .timestamp()
        .callback
        .saturating_duration_since(info.timestamp().capture)
        .as_nanos()
        .min(u64::MAX as u128) as u64;
    counters.callback_capture_latency_ns[timing_slot].store(capture_latency_ns, Ordering::Relaxed);
    let capture_start_ns = start_ns.saturating_sub(capture_latency_ns);
    let first_sample = counters
        .sample_position
        .fetch_add(frames as u64, Ordering::Relaxed);
    queue_captured_frames(
        input,
        channels,
        sample_rate,
        capture_start_ns,
        first_sample,
        producer,
        counters,
    );
    let callback_duration_ns = monotonic_ns().saturating_sub(start_ns);
    counters.callback_duration_ns[timing_slot].store(callback_duration_ns, Ordering::Relaxed);
    counters.callback_count.fetch_add(1, Ordering::Relaxed);
}

fn queue_captured_frames<T>(
    input: &[T],
    channels: usize,
    sample_rate: u32,
    capture_start_ns: u64,
    first_sample: u64,
    producer: &mut Producer<CapturedAudioBatch>,
    counters: &CaptureCounters,
) where
    T: Copy,
    f32: FromSample<T>,
{
    let frames = input.len() / channels;
    let mut offset_frames = 0;
    while offset_frames < frames {
        let batch_frames = (frames - offset_frames).min(MAX_CAPTURE_BATCH_SAMPLES);
        let mut batch = CapturedAudioBatch::empty();
        let sample_offset = offset_frames * channels;
        for frame in 0..batch_frames {
            let channel_offset = sample_offset + frame * channels;
            let mut mono = 0.0_f32;
            for channel in 0..channels {
                mono += f32::from_sample(input[channel_offset + channel]);
            }
            batch.samples[frame] = mono / channels as f32;
        }
        batch.len = batch_frames;
        batch.sample_position = first_sample + offset_frames as u64;
        batch.capture_end_ns = capture_start_ns.saturating_add(
            ((offset_frames + batch_frames) as u128 * 1_000_000_000_u128 / sample_rate as u128)
                .min(u64::MAX as u128) as u64,
        );
        batch.dropped_samples_before = counters.pending_dropped_samples.swap(0, Ordering::Relaxed);
        enqueue_batch(producer, counters, batch);
        offset_frames += batch_frames;
    }
}

fn enqueue_batch(
    producer: &mut Producer<CapturedAudioBatch>,
    counters: &CaptureCounters,
    batch: CapturedAudioBatch,
) {
    if let Err(rtrb::PushError::Full(batch)) = producer.push(batch) {
        counters.overflowed_batches.fetch_add(1, Ordering::Relaxed);
        let samples_dropped = batch.len as u64;
        counters
            .dropped_samples
            .fetch_add(samples_dropped, Ordering::Relaxed);
        counters.pending_dropped_samples.fetch_add(
            batch.dropped_samples_before.saturating_add(samples_dropped),
            Ordering::Relaxed,
        );
    }
}

fn sample_percentile(samples: &[AtomicU64; CAPTURE_TIMING_WINDOW], percentile: usize) -> u64 {
    let mut ordered = [0_u64; CAPTURE_TIMING_WINDOW];
    let mut count = 0;
    for sample in samples {
        let value = sample.load(Ordering::Relaxed);
        if value > 0 {
            ordered[count] = value;
            count += 1;
        }
    }
    if count == 0 {
        return 0;
    }
    ordered[..count].sort_unstable();
    let index = (count * percentile).div_ceil(100).saturating_sub(1);
    ordered[index]
}

fn nonzero(value: u64) -> Option<u64> {
    (value != 0).then_some(value)
}

#[cfg(target_os = "linux")]
pub fn monotonic_time_ns() -> u64 {
    monotonic_ns()
}

#[cfg(target_os = "linux")]
fn monotonic_ns() -> u64 {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: `time` is a valid writable timespec and CLOCK_MONOTONIC is supported
    // on the Linux targets used by this runtime.
    let result = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) };
    if result == 0 {
        (time.tv_sec as u64)
            .saturating_mul(1_000_000_000)
            .saturating_add(time.tv_nsec as u64)
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{AudioConfig, AudioProcessor};

    #[test]
    fn callback_percentiles_use_nearest_rank_and_ignore_empty_slots() {
        let counters = CaptureCounters::default();
        for (index, value) in [1, 2, 3, 4].into_iter().enumerate() {
            counters.callback_duration_ns[index].store(value, Ordering::Relaxed);
        }

        let stats = counters.stats();
        assert_eq!(stats.callback_duration_p50_ns, 2);
        assert_eq!(stats.callback_duration_p95_ns, 4);
        assert_eq!(stats.callback_duration_p99_ns, 4);
        assert_eq!(stats.capture_latency_p95_ns, 0);
    }

    #[test]
    fn captured_stereo_frames_are_downmixed_and_batched_without_sample_loss() {
        let frame_count = MAX_CAPTURE_BATCH_SAMPLES + 3;
        let input = (0..frame_count)
            .flat_map(|frame| {
                let left = frame as f32 / 10.0;
                [left, left + 2.0]
            })
            .collect::<Vec<_>>();
        let (mut producer, mut consumer) = RingBuffer::new(2);
        let counters = CaptureCounters::default();
        let capture_start_ns = 123_456;

        queue_captured_frames(
            &input,
            2,
            48_000,
            capture_start_ns,
            17,
            &mut producer,
            &counters,
        );

        let first = consumer.pop().unwrap();
        assert_eq!(first.len, MAX_CAPTURE_BATCH_SAMPLES);
        assert_eq!(first.sample_position, 17);
        assert_eq!(
            first.capture_end_ns,
            capture_start_ns + 2_048 * 1_000_000_000 / 48_000
        );
        assert!((first.samples[0] - 1.0).abs() < 1e-6);
        assert!((first.samples[2_047] - 205.7).abs() < 1e-4);
        assert_eq!(first.dropped_samples_before, 0);

        let second = consumer.pop().unwrap();
        assert_eq!(second.len, 3);
        assert_eq!(
            second.sample_position,
            17 + MAX_CAPTURE_BATCH_SAMPLES as u64
        );
        assert_eq!(
            second.capture_end_ns,
            capture_start_ns + 2_051 * 1_000_000_000 / 48_000
        );
        assert!((second.samples[0] - 205.8).abs() < 1e-4);
        assert!((second.samples[2] - 206.0).abs() < 1e-4);
        assert_eq!(second.dropped_samples_before, 0);
        assert!(consumer.pop().is_err());

        let mut processor = AudioProcessor::new(AudioConfig::default(), 48_000.0).unwrap();
        for batch in [first, second] {
            processor
                .feed_samples(
                    &batch.samples[..batch.len],
                    batch.capture_end_ns as f64 / 1_000_000_000.0,
                )
                .unwrap();
        }
        assert_eq!(processor.stats().samples_seen, frame_count as u64);
    }

    #[test]
    fn full_capture_queue_counts_batches_and_carries_sample_gap_forward() {
        let (mut producer, mut consumer) = RingBuffer::new(2);
        let counters = CaptureCounters::default();
        for sample_position in [0, 8] {
            let mut batch = CapturedAudioBatch::empty();
            batch.len = 8;
            batch.sample_position = sample_position;
            producer.push(batch).unwrap();
        }

        let mut dropped = CapturedAudioBatch::empty();
        dropped.len = 6;
        dropped.sample_position = 16;
        dropped.dropped_samples_before = 3;
        enqueue_batch(&mut producer, &counters, dropped);
        assert_eq!(counters.overflowed_batches.load(Ordering::Relaxed), 1);
        assert_eq!(counters.dropped_samples.load(Ordering::Relaxed), 6);
        assert_eq!(counters.pending_dropped_samples.load(Ordering::Relaxed), 9);

        consumer.pop().unwrap();
        let mut recovered = CapturedAudioBatch::empty();
        recovered.len = 4;
        recovered.sample_position = 22;
        recovered.dropped_samples_before =
            counters.pending_dropped_samples.swap(0, Ordering::Relaxed);
        enqueue_batch(&mut producer, &counters, recovered);
        let queued = consumer.pop().unwrap();
        assert_eq!(queued.sample_position, 8);
        let queued = consumer.pop().unwrap();
        assert_eq!(queued.sample_position, 22);
        assert_eq!(queued.dropped_samples_before, 9);
    }
}

#[cfg(not(target_os = "linux"))]
pub fn monotonic_time_ns() -> u64 {
    monotonic_ns()
}

#[cfg(not(target_os = "linux"))]
fn monotonic_ns() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}
