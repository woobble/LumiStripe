//! Bounded ingress for decoded PCM supplied by playback providers.
//!
//! Producers do conversion and one try-push per fixed batch. The DSP worker
//! owns the queue consumer; a busy producer lock or full queue drops samples
//! and records the gap instead of waiting for analysis.

use crate::capture::MAX_CAPTURE_BATCH_SAMPLES;
use rtrb::{Consumer, Producer, RingBuffer};
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU16, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, TryLockError};

pub const PCM_INGRESS_QUEUE_CAPACITY: usize = 8;

/// Mono PCM batch shared by device capture and decoded-PCM inputs.
#[derive(Clone, Copy, Debug)]
pub struct AudioPcmBatch {
    pub samples: [f32; MAX_CAPTURE_BATCH_SAMPLES],
    pub len: usize,
    pub sample_rate: u32,
    /// Number of interleaved source channels before mono downmixing.
    pub channels: u16,
    pub capture_end_ns: u64,
    /// Monotonic time when this batch entered the bounded analysis queue.
    pub submitted_at_ns: u64,
    pub sample_position: u64,
    pub stream_generation: u64,
    pub dropped_samples_before: u64,
}

impl AudioPcmBatch {
    pub(crate) fn empty(sample_rate: u32, channels: u16) -> Self {
        Self {
            samples: [0.0; MAX_CAPTURE_BATCH_SAMPLES],
            len: 0,
            sample_rate,
            channels,
            capture_end_ns: 0,
            submitted_at_ns: 0,
            sample_position: 0,
            stream_generation: 0,
            dropped_samples_before: 0,
        }
    }
}

/// Playback state reported by the provider for direct-input fallback policy.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum PcmPlaybackState {
    #[default]
    Idle = 0,
    Loading = 1,
    Playing = 2,
    Paused = 3,
    Failed = 4,
}

impl PcmPlaybackState {
    fn from_raw(value: u8) -> Self {
        match value {
            1 => Self::Loading,
            2 => Self::Playing,
            3 => Self::Paused,
            4 => Self::Failed,
            _ => Self::Idle,
        }
    }
}

/// Snapshot of the decoded PCM queue and its loss counters.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct PcmIngressStats {
    pub enabled: bool,
    pub playback_state: PcmPlaybackState,
    pub sample_rate: u32,
    pub channels: u16,
    pub stream_generation: u64,
    pub batches_received: u64,
    pub samples_received: u64,
    pub queue_occupancy: usize,
    pub queue_capacity: usize,
    pub dropped_batches: u64,
    pub dropped_samples: u64,
    pub discontinuities: u64,
    pub invalid_samples: u64,
    pub unsupported_packets: u64,
    pub stale_batches: u64,
    pub stale_samples: u64,
    pub last_input_timestamp_ns: Option<u64>,
    pub estimated_ingress_delay_ns: u64,
}

struct IngressShared {
    enabled: AtomicBool,
    playback_state: AtomicU8,
    generation: AtomicU64,
    queue_occupancy: AtomicU64,
    batches_received: AtomicU64,
    samples_received: AtomicU64,
    dropped_batches: AtomicU64,
    dropped_samples: AtomicU64,
    pending_dropped_samples: AtomicU64,
    discontinuities: AtomicU64,
    invalid_samples: AtomicU64,
    unsupported_packets: AtomicU64,
    stale_batches: AtomicU64,
    stale_samples: AtomicU64,
    sample_rate: AtomicU64,
    channels: AtomicU16,
    last_input_timestamp_ns: AtomicU64,
}

impl Default for IngressShared {
    fn default() -> Self {
        Self {
            enabled: AtomicBool::new(false),
            playback_state: AtomicU8::new(PcmPlaybackState::Idle as u8),
            generation: AtomicU64::new(0),
            queue_occupancy: AtomicU64::new(0),
            batches_received: AtomicU64::new(0),
            samples_received: AtomicU64::new(0),
            dropped_batches: AtomicU64::new(0),
            dropped_samples: AtomicU64::new(0),
            pending_dropped_samples: AtomicU64::new(0),
            discontinuities: AtomicU64::new(0),
            invalid_samples: AtomicU64::new(0),
            unsupported_packets: AtomicU64::new(0),
            stale_batches: AtomicU64::new(0),
            stale_samples: AtomicU64::new(0),
            sample_rate: AtomicU64::new(0),
            channels: AtomicU16::new(0),
            last_input_timestamp_ns: AtomicU64::new(0),
        }
    }
}

/// Cloneable, nonblocking producer handle used by playback sinks.
#[derive(Clone)]
pub struct PcmIngressSender {
    producer: Arc<Mutex<Producer<AudioPcmBatch>>>,
    shared: Arc<IngressShared>,
}

impl std::fmt::Debug for PcmIngressSender {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PcmIngressSender")
            .field("stats", &self.stats())
            .finish()
    }
}

/// Single consumer handle owned by the Rust audio worker.
pub struct PcmIngressReceiver {
    consumer: Consumer<AudioPcmBatch>,
    shared: Arc<IngressShared>,
}

impl std::fmt::Debug for PcmIngressReceiver {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PcmIngressReceiver")
            .field("stats", &self.stats())
            .finish()
    }
}

/// Construct a fixed-capacity decoded PCM queue.
pub fn bounded_pcm_ingress() -> (PcmIngressSender, PcmIngressReceiver) {
    let (producer, consumer) = RingBuffer::new(PCM_INGRESS_QUEUE_CAPACITY);
    let shared = Arc::new(IngressShared::default());
    (
        PcmIngressSender {
            producer: Arc::new(Mutex::new(producer)),
            shared: Arc::clone(&shared),
        },
        PcmIngressReceiver { consumer, shared },
    )
}

impl PcmIngressSender {
    /// Enable or disable analysis and invalidate batches from the prior mode.
    pub fn set_enabled(&self, enabled: bool) -> u64 {
        let previous = self.shared.enabled.swap(enabled, Ordering::AcqRel);
        if previous != enabled {
            self.begin_generation()
        } else {
            self.generation()
        }
    }

    /// Mark a seek, track transition, or source restart as a new PCM stream.
    pub fn begin_generation(&self) -> u64 {
        self.shared
            .pending_dropped_samples
            .store(0, Ordering::Release);
        self.shared.generation.fetch_add(1, Ordering::AcqRel) + 1
    }

    pub fn generation(&self) -> u64 {
        self.shared.generation.load(Ordering::Acquire)
    }

    pub fn set_playback_state(&self, state: PcmPlaybackState) {
        self.shared
            .playback_state
            .store(state as u8, Ordering::Release);
    }

    /// Count a decoder packet format that the analysis tap cannot consume.
    pub fn record_unsupported_packet(&self) {
        self.shared
            .unsupported_packets
            .fetch_add(1, Ordering::Relaxed);
    }

    /// Convert interleaved decoded f64 samples to bounded mono f32 batches.
    ///
    /// The timestamp is monotonic queue-submission time, not speaker
    /// presentation time. sample_position is measured in mono frames.
    pub fn submit_interleaved_f64_at(
        &self,
        samples: &[f64],
        sample_rate: u32,
        channels: u16,
        stream_generation: u64,
        sample_position: u64,
        submitted_at_ns: u64,
    ) -> bool {
        if !self.shared.enabled.load(Ordering::Acquire) {
            return false;
        }
        if stream_generation != self.generation() {
            self.record_stale_samples(samples.len(), channels);
            return false;
        }
        let channels = usize::from(channels);
        if sample_rate == 0 || channels == 0 || samples.len() % channels != 0 {
            let dropped = samples.len().div_ceil(channels.max(1)) as u64;
            self.record_drop(dropped);
            return false;
        }

        let frame_count = samples.len() / channels;
        self.shared.batches_received.fetch_add(1, Ordering::Relaxed);
        self.shared
            .samples_received
            .fetch_add(frame_count as u64, Ordering::Relaxed);
        self.shared
            .sample_rate
            .store(u64::from(sample_rate), Ordering::Relaxed);
        self.shared
            .channels
            .store(channels as u16, Ordering::Relaxed);
        self.shared
            .last_input_timestamp_ns
            .store(submitted_at_ns, Ordering::Relaxed);

        let mut offset = 0;
        while offset < frame_count {
            if stream_generation != self.generation()
                || !self.shared.enabled.load(Ordering::Acquire)
            {
                self.record_stale_samples(frame_count - offset, channels as u16);
                return false;
            }
            let batch_frames = (frame_count - offset).min(MAX_CAPTURE_BATCH_SAMPLES);
            let mut batch = AudioPcmBatch::empty(sample_rate, channels as u16);
            batch.len = batch_frames;
            batch.sample_position = sample_position.saturating_add(offset as u64);
            batch.stream_generation = stream_generation;
            batch.submitted_at_ns = submitted_at_ns;
            batch.capture_end_ns = submitted_at_ns.saturating_sub(
                ((frame_count - offset - batch_frames) as u128 * 1_000_000_000_u128
                    / u128::from(sample_rate))
                .min(u64::MAX as u128) as u64,
            );
            for frame in 0..batch_frames {
                let start = (offset + frame) * channels;
                let mut sum = 0.0_f64;
                let mut valid = true;
                for sample in &samples[start..start + channels] {
                    if sample.is_finite() {
                        sum += *sample / channels as f64;
                    } else {
                        valid = false;
                    }
                }
                let mono = if valid { sum as f32 } else { 0.0 };
                if valid && mono.is_finite() {
                    batch.samples[frame] = mono;
                } else {
                    batch.samples[frame] = 0.0;
                    self.shared.invalid_samples.fetch_add(1, Ordering::Relaxed);
                }
            }
            batch.dropped_samples_before = self
                .shared
                .pending_dropped_samples
                .swap(0, Ordering::AcqRel);
            let previous_drops = batch.dropped_samples_before;
            if self.try_enqueue(batch) {
                offset += batch_frames;
            } else {
                let dropped_frames = (frame_count - offset) as u64;
                self.shared
                    .pending_dropped_samples
                    .fetch_add(previous_drops, Ordering::Relaxed);
                self.record_drop(dropped_frames);
                return false;
            }
        }
        true
    }

    fn try_enqueue(&self, batch: AudioPcmBatch) -> bool {
        let mut producer = match self.producer.try_lock() {
            Ok(producer) => producer,
            Err(TryLockError::Poisoned(error)) => error.into_inner(),
            Err(TryLockError::WouldBlock) => return false,
        };
        self.shared.queue_occupancy.fetch_add(1, Ordering::Relaxed);
        if producer.push(batch).is_ok() {
            true
        } else {
            self.shared.queue_occupancy.fetch_sub(1, Ordering::Relaxed);
            false
        }
    }

    fn record_drop(&self, samples: u64) {
        self.shared.dropped_batches.fetch_add(1, Ordering::Relaxed);
        self.shared
            .dropped_samples
            .fetch_add(samples, Ordering::Relaxed);
        self.shared
            .pending_dropped_samples
            .fetch_add(samples, Ordering::Relaxed);
        self.shared.discontinuities.fetch_add(1, Ordering::Relaxed);
    }

    fn record_stale_samples(&self, sample_count: usize, channels: u16) {
        let samples = sample_count.div_ceil(usize::from(channels.max(1))) as u64;
        self.shared.stale_batches.fetch_add(1, Ordering::Relaxed);
        self.shared
            .stale_samples
            .fetch_add(samples, Ordering::Relaxed);
    }

    pub fn stats(&self) -> PcmIngressStats {
        stats(&self.shared)
    }
}

impl PcmIngressReceiver {
    pub fn try_pop(&mut self) -> Option<AudioPcmBatch> {
        loop {
            let batch = self.consumer.pop().ok()?;
            self.shared.queue_occupancy.fetch_sub(1, Ordering::Relaxed);
            if !self.shared.enabled.load(Ordering::Acquire)
                || batch.stream_generation != self.shared.generation.load(Ordering::Acquire)
            {
                self.shared.stale_batches.fetch_add(1, Ordering::Relaxed);
                self.shared
                    .stale_samples
                    .fetch_add(batch.len as u64, Ordering::Relaxed);
                continue;
            }
            return Some(batch);
        }
    }

    pub fn discard_queued(&mut self) {
        while let Ok(batch) = self.consumer.pop() {
            self.shared.queue_occupancy.fetch_sub(1, Ordering::Relaxed);
            self.shared.stale_batches.fetch_add(1, Ordering::Relaxed);
            self.shared
                .stale_samples
                .fetch_add(batch.len as u64, Ordering::Relaxed);
        }
    }

    pub fn stats(&self) -> PcmIngressStats {
        stats(&self.shared)
    }
}

fn stats(shared: &IngressShared) -> PcmIngressStats {
    let sample_rate = shared.sample_rate.load(Ordering::Relaxed) as u32;
    let queue_occupancy = shared.queue_occupancy.load(Ordering::Relaxed) as usize;
    let estimated_ingress_delay_ns = if sample_rate == 0 {
        0
    } else {
        (queue_occupancy as u128 * MAX_CAPTURE_BATCH_SAMPLES as u128 * 1_000_000_000_u128
            / u128::from(sample_rate))
        .min(u64::MAX as u128) as u64
    };
    let last_timestamp = shared.last_input_timestamp_ns.load(Ordering::Relaxed);
    PcmIngressStats {
        enabled: shared.enabled.load(Ordering::Relaxed),
        playback_state: PcmPlaybackState::from_raw(shared.playback_state.load(Ordering::Relaxed)),
        sample_rate,
        channels: shared.channels.load(Ordering::Relaxed),
        stream_generation: shared.generation.load(Ordering::Relaxed),
        batches_received: shared.batches_received.load(Ordering::Relaxed),
        samples_received: shared.samples_received.load(Ordering::Relaxed),
        queue_occupancy,
        queue_capacity: PCM_INGRESS_QUEUE_CAPACITY,
        dropped_batches: shared.dropped_batches.load(Ordering::Relaxed),
        dropped_samples: shared.dropped_samples.load(Ordering::Relaxed),
        discontinuities: shared.discontinuities.load(Ordering::Relaxed),
        invalid_samples: shared.invalid_samples.load(Ordering::Relaxed),
        unsupported_packets: shared.unsupported_packets.load(Ordering::Relaxed),
        stale_batches: shared.stale_batches.load(Ordering::Relaxed),
        stale_samples: shared.stale_samples.load(Ordering::Relaxed),
        last_input_timestamp_ns: (last_timestamp > 0).then_some(last_timestamp),
        estimated_ingress_delay_ns,
    }
}

#[cfg(test)]
mod tests {
    use super::{PcmPlaybackState, bounded_pcm_ingress};
    use crate::capture::MAX_CAPTURE_BATCH_SAMPLES;

    fn enabled_ingress() -> (super::PcmIngressSender, super::PcmIngressReceiver, u64) {
        let (sender, receiver) = bounded_pcm_ingress();
        let generation = sender.set_enabled(true);
        sender.set_playback_state(PcmPlaybackState::Playing);
        (sender, receiver, generation)
    }

    #[test]
    fn downmixes_stereo_and_preserves_rate_and_timestamp() {
        let (sender, mut receiver, generation) = enabled_ingress();
        assert!(sender.submit_interleaved_f64_at(
            &[1.0, -1.0, 0.25, 0.75],
            48_000,
            2,
            generation,
            17,
            123_000,
        ));
        let batch = receiver.try_pop().unwrap();
        assert_eq!(&batch.samples[..batch.len], &[0.0, 0.5]);
        assert_eq!(batch.sample_rate, 48_000);
        assert_eq!(batch.channels, 2);
        assert_eq!(batch.sample_position, 17);
        assert_eq!(batch.stream_generation, generation);
        assert_eq!(batch.submitted_at_ns, 123_000);
        assert_eq!(receiver.stats().samples_received, 2);
    }

    #[test]
    fn variable_packets_split_into_fixed_batches_in_order() {
        let (sender, mut receiver, generation) = enabled_ingress();
        let input = (0..MAX_CAPTURE_BATCH_SAMPLES + 13)
            .flat_map(|value| [value as f64, value as f64])
            .collect::<Vec<_>>();
        assert!(sender.submit_interleaved_f64_at(&input, 44_100, 2, generation, 500, 1_000_000,));
        let first = receiver.try_pop().unwrap();
        let second = receiver.try_pop().unwrap();
        assert_eq!(first.len, MAX_CAPTURE_BATCH_SAMPLES);
        assert_eq!(second.len, 13);
        assert_eq!(first.sample_position, 500);
        assert_eq!(
            second.sample_position,
            500 + MAX_CAPTURE_BATCH_SAMPLES as u64
        );
        assert_eq!(first.samples[0], 0.0);
        assert_eq!(
            first.samples[first.len - 1],
            (MAX_CAPTURE_BATCH_SAMPLES - 1) as f32
        );
        assert_eq!(second.samples[12], (MAX_CAPTURE_BATCH_SAMPLES + 12) as f32);
        assert!(second.capture_end_ns >= first.capture_end_ns);
    }

    #[test]
    fn full_queue_drops_without_blocking_and_propagates_a_gap() {
        let (sender, mut receiver, generation) = enabled_ingress();
        let packet = [0.25; MAX_CAPTURE_BATCH_SAMPLES * 2];
        for _ in 0..8 {
            assert!(sender.submit_interleaved_f64_at(&packet, 44_100, 2, generation, 0, 1_000,));
        }
        assert!(!sender.submit_interleaved_f64_at(
            &packet[..2],
            44_100,
            2,
            generation,
            8 * MAX_CAPTURE_BATCH_SAMPLES as u64,
            2_000,
        ));
        assert_eq!(sender.stats().queue_occupancy, 8);
        assert_eq!(sender.stats().dropped_samples, 1);
        receiver.try_pop().unwrap();
        assert!(sender.submit_interleaved_f64_at(
            &packet[..2],
            44_100,
            2,
            generation,
            8 * MAX_CAPTURE_BATCH_SAMPLES as u64 + 1,
            3_000,
        ));
        let recovered = loop {
            let batch = receiver.try_pop().unwrap();
            if batch.sample_position == 8 * MAX_CAPTURE_BATCH_SAMPLES as u64 + 1 {
                break batch;
            }
        };
        assert!(recovered.dropped_samples_before > 0);
        assert_eq!(sender.stats().discontinuities, 1);
    }

    #[test]
    fn rejects_nonfinite_values_as_silence_and_counts_them() {
        let (sender, mut receiver, generation) = enabled_ingress();
        assert!(sender.submit_interleaved_f64_at(
            &[f64::NAN, 0.5, 0.25, 0.25],
            44_100,
            2,
            generation,
            0,
            1_000,
        ));
        let batch = receiver.try_pop().unwrap();
        assert_eq!(&batch.samples[..batch.len], &[0.0, 0.25]);
        assert_eq!(sender.stats().invalid_samples, 1);
    }

    #[test]
    fn generation_changes_reject_queued_and_future_stale_samples() {
        let (sender, mut receiver, generation) = enabled_ingress();
        assert!(sender.submit_interleaved_f64_at(&[0.2, 0.2], 44_100, 2, generation, 0, 1_000,));
        let new_generation = sender.begin_generation();
        assert!(new_generation > generation);
        assert!(receiver.try_pop().is_none());
        assert!(!sender.submit_interleaved_f64_at(&[0.3, 0.3], 44_100, 2, generation, 1, 2_000,));
        assert_eq!(sender.stats().stale_batches, 2);
    }

    #[test]
    fn disabling_and_reenabling_invalidates_prior_batches_and_retains_state() {
        let (sender, mut receiver, generation) = enabled_ingress();
        assert!(sender.submit_interleaved_f64_at(&[0.1, 0.1], 44_100, 2, generation, 0, 1_000,));
        sender.set_playback_state(PcmPlaybackState::Paused);
        sender.set_enabled(false);
        assert!(receiver.try_pop().is_none());
        assert_eq!(receiver.stats().playback_state, PcmPlaybackState::Paused);
        let next_generation = sender.set_enabled(true);
        assert!(next_generation > generation);
    }
}
