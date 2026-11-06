//! Bounded ingress for decoded PCM supplied by playback providers.
//!
//! Producers do conversion and one try-push per fixed batch. The DSP worker
//! owns the queue consumer; a busy producer lock or full queue drops samples
//! and records the gap instead of waiting for analysis.

use crate::capture::MAX_CAPTURE_BATCH_SAMPLES;
use rtrb::{Consumer, Producer, RingBuffer};
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU16, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, TryLockError};

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
    generation_exhausted: AtomicBool,
    playback_state: AtomicU8,
    generation: AtomicU64,
    generation_transition: Mutex<()>,
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
            generation_exhausted: AtomicBool::new(false),
            playback_state: AtomicU8::new(PcmPlaybackState::Idle as u8),
            generation: AtomicU64::new(0),
            generation_transition: Mutex::new(()),
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
        let _transition = lock_unpoisoned(&self.shared.generation_transition);
        if enabled && self.shared.generation_exhausted.load(Ordering::Acquire) {
            self.shared.enabled.store(false, Ordering::Release);
            return self.generation();
        }
        let previous = self.shared.enabled.swap(enabled, Ordering::AcqRel);
        if previous != enabled {
            self.advance_generation()
        } else {
            self.generation()
        }
    }

    /// Mark a seek, track transition, or source restart as a new PCM stream.
    pub fn begin_generation(&self) -> u64 {
        let _transition = lock_unpoisoned(&self.shared.generation_transition);
        self.advance_generation()
    }

    fn advance_generation(&self) -> u64 {
        self.shared
            .pending_dropped_samples
            .store(0, Ordering::Release);
        match self.shared.generation.fetch_update(
            Ordering::AcqRel,
            Ordering::Acquire,
            |generation| generation.checked_add(1),
        ) {
            Ok(previous) => previous + 1,
            Err(generation) => {
                self.shared
                    .generation_exhausted
                    .store(true, Ordering::Release);
                self.shared.enabled.store(false, Ordering::Release);
                generation
            }
        }
    }

    pub fn generation(&self) -> u64 {
        self.shared.generation.load(Ordering::Acquire)
    }

    pub fn set_playback_state(&self, state: PcmPlaybackState) {
        self.shared
            .playback_state
            .store(state as u8, Ordering::Release);
    }

    /// Run one consumer update while its generation is current.
    ///
    /// Generation transitions wait for this bounded DSP update to finish, so
    /// a stream transition cannot interleave with processing or publishing an
    /// obsolete batch. Playback producers do not take this lock.
    pub fn process_current_generation<R>(
        &self,
        stream_generation: u64,
        process: impl FnOnce() -> R,
    ) -> Option<R> {
        let _transition = lock_unpoisoned(&self.shared.generation_transition);
        if !self.shared.enabled.load(Ordering::Acquire) || stream_generation != self.generation() {
            return None;
        }
        Some(process())
    }

    /// Count a decoder packet format that the analysis tap cannot consume.
    pub fn record_unsupported_packet(&self) {
        atomic_saturating_add(&self.shared.unsupported_packets, 1);
    }

    /// Record a packet discarded because its sample position cannot be used.
    pub fn record_discontinuity(&self, samples: u64) {
        self.record_drop(samples);
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

        if channels > 2 {
            self.record_unsupported_packet();
            self.record_drop(samples.len().div_ceil(channels) as u64);
            return false;
        }

        if samples.is_empty() {
            return true;
        }

        let frame_count = samples.len() / channels;
        let Some(end_position) = sample_position.checked_add(frame_count as u64) else {
            self.record_drop(frame_count as u64);
            return false;
        };
        if !store_monotonic_timestamp(&self.shared.last_input_timestamp_ns, submitted_at_ns) {
            self.record_drop(frame_count as u64);
            return false;
        }
        atomic_saturating_add(&self.shared.batches_received, 1);
        atomic_saturating_add(&self.shared.samples_received, frame_count as u64);
        self.shared
            .sample_rate
            .store(u64::from(sample_rate), Ordering::Relaxed);
        self.shared
            .channels
            .store(channels as u16, Ordering::Relaxed);

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
            batch.sample_position = sample_position + offset as u64;
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
                    atomic_saturating_add(&self.shared.invalid_samples, 1);
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
                atomic_saturating_add(&self.shared.pending_dropped_samples, previous_drops);
                self.record_drop(dropped_frames);
                return false;
            }
        }
        debug_assert_eq!(end_position, sample_position + frame_count as u64);
        true
    }

    fn try_enqueue(&self, batch: AudioPcmBatch) -> bool {
        let mut producer = match self.producer.try_lock() {
            Ok(producer) => producer,
            Err(TryLockError::Poisoned(error)) => error.into_inner(),
            Err(TryLockError::WouldBlock) => return false,
        };
        atomic_saturating_add(&self.shared.queue_occupancy, 1);
        if producer.push(batch).is_ok() {
            true
        } else {
            atomic_saturating_sub(&self.shared.queue_occupancy, 1);
            false
        }
    }

    fn record_drop(&self, samples: u64) {
        atomic_saturating_add(&self.shared.dropped_batches, 1);
        atomic_saturating_add(&self.shared.dropped_samples, samples);
        atomic_saturating_add(&self.shared.pending_dropped_samples, samples);
        atomic_saturating_add(&self.shared.discontinuities, 1);
    }

    fn record_stale_samples(&self, sample_count: usize, channels: u16) {
        let samples = sample_count.div_ceil(usize::from(channels.max(1))) as u64;
        atomic_saturating_add(&self.shared.stale_batches, 1);
        atomic_saturating_add(&self.shared.stale_samples, samples);
    }

    pub fn stats(&self) -> PcmIngressStats {
        stats(&self.shared)
    }
}

impl PcmIngressReceiver {
    pub fn try_pop(&mut self) -> Option<AudioPcmBatch> {
        loop {
            let batch = self.consumer.pop().ok()?;
            atomic_saturating_sub(&self.shared.queue_occupancy, 1);
            if !self.shared.enabled.load(Ordering::Acquire)
                || batch.stream_generation != self.shared.generation.load(Ordering::Acquire)
            {
                atomic_saturating_add(&self.shared.stale_batches, 1);
                atomic_saturating_add(&self.shared.stale_samples, batch.len as u64);
                continue;
            }
            return Some(batch);
        }
    }

    pub fn discard_queued(&mut self) {
        while let Ok(batch) = self.consumer.pop() {
            atomic_saturating_sub(&self.shared.queue_occupancy, 1);
            atomic_saturating_add(&self.shared.stale_batches, 1);
            atomic_saturating_add(&self.shared.stale_samples, batch.len as u64);
        }
    }

    pub fn stats(&self) -> PcmIngressStats {
        stats(&self.shared)
    }
}

fn lock_unpoisoned<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn atomic_saturating_add(counter: &AtomicU64, amount: u64) {
    let _ = counter.fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
        Some(value.saturating_add(amount))
    });
}

fn atomic_saturating_sub(counter: &AtomicU64, amount: u64) {
    let _ = counter.fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
        Some(value.saturating_sub(amount))
    });
}

fn store_monotonic_timestamp(timestamp: &AtomicU64, value: u64) -> bool {
    let mut previous = timestamp.load(Ordering::Relaxed);
    loop {
        if value < previous {
            return false;
        }
        match timestamp.compare_exchange_weak(previous, value, Ordering::Relaxed, Ordering::Relaxed)
        {
            Ok(_) => return true,
            Err(current) => previous = current,
        }
    }
}

fn stats(shared: &IngressShared) -> PcmIngressStats {
    let sample_rate = shared.sample_rate.load(Ordering::Relaxed) as u32;
    // The producer reserves occupancy before push, so a concurrent snapshot
    // can observe one speculative slot when the bounded queue is full.
    let queue_occupancy = shared
        .queue_occupancy
        .load(Ordering::Relaxed)
        .min(PCM_INGRESS_QUEUE_CAPACITY as u64) as usize;
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
    use super::{PCM_INGRESS_QUEUE_CAPACITY, PcmPlaybackState, bounded_pcm_ingress};
    use crate::capture::MAX_CAPTURE_BATCH_SAMPLES;
    use std::sync::{Arc, Barrier, mpsc};
    use std::thread;

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

    #[test]
    fn rejects_unsupported_channel_layouts_and_malformed_packets() {
        let (sender, mut receiver, generation) = enabled_ingress();

        assert!(!sender.submit_interleaved_f64_at(
            &[0.1, 0.2, 0.3],
            44_100,
            3,
            generation,
            0,
            1_000,
        ));
        assert!(!sender.submit_interleaved_f64_at(
            &[0.1, 0.2, 0.3],
            44_100,
            2,
            generation,
            0,
            2_000,
        ));

        let stats = sender.stats();
        assert_eq!(stats.unsupported_packets, 1);
        assert_eq!(stats.dropped_batches, 2);
        assert_eq!(stats.dropped_samples, 3);
        assert_eq!(stats.discontinuities, 2);
        assert_eq!(stats.queue_occupancy, 0);
        assert!(receiver.try_pop().is_none());
    }

    #[test]
    fn rejects_timestamp_regressions_without_rewinding_last_input_time() {
        let (sender, mut receiver, generation) = enabled_ingress();
        assert!(sender.submit_interleaved_f64_at(&[0.1, 0.1], 44_100, 2, generation, 0, 20_000,));
        assert!(!sender.submit_interleaved_f64_at(&[0.2, 0.2], 44_100, 2, generation, 1, 19_999,));

        let stats = sender.stats();
        assert_eq!(stats.last_input_timestamp_ns, Some(20_000));
        assert_eq!(stats.dropped_samples, 1);
        assert_eq!(stats.discontinuities, 1);
        assert_eq!(receiver.try_pop().unwrap().sample_position, 0);
        assert!(receiver.try_pop().is_none());
    }

    #[test]
    fn busy_producer_lock_drops_instead_of_waiting() {
        let (sender, _receiver, generation) = enabled_ingress();
        let _producer = sender.producer.lock().unwrap();
        assert!(!sender.submit_interleaved_f64_at(&[0.25, 0.25], 44_100, 2, generation, 0, 1_000,));
        assert_eq!(sender.stats().queue_occupancy, 0);
        assert_eq!(sender.stats().dropped_samples, 1);
        assert_eq!(sender.stats().discontinuities, 1);
    }

    #[test]
    fn generation_exhaustion_disables_ingress_without_wrapping() {
        let (sender, mut receiver, _) = enabled_ingress();
        sender
            .shared
            .generation
            .store(u64::MAX, std::sync::atomic::Ordering::Release);

        assert_eq!(sender.begin_generation(), u64::MAX);
        assert!(
            sender
                .shared
                .generation_exhausted
                .load(std::sync::atomic::Ordering::Acquire)
        );
        assert!(!sender.stats().enabled);
        assert_eq!(sender.set_enabled(true), u64::MAX);
        assert!(!sender.submit_interleaved_f64_at(&[0.1, 0.1], 44_100, 2, u64::MAX, 0, 1_000,));
        assert!(receiver.try_pop().is_none());
    }

    #[test]
    fn a_generation_transition_waits_for_the_current_consumer_update() {
        let (sender, mut receiver, generation) = enabled_ingress();
        let entered = Arc::new(Barrier::new(2));
        let release = Arc::new(Barrier::new(2));
        let processing_sender = sender.clone();
        let processing_entered = Arc::clone(&entered);
        let processing_release = Arc::clone(&release);
        let processed = thread::spawn(move || {
            processing_sender.process_current_generation(generation, || {
                processing_entered.wait();
                processing_release.wait();
            })
        });

        entered.wait();
        let producer_sender = sender.clone();
        let (submitted_tx, submitted_rx) = mpsc::channel();
        let producer = thread::spawn(move || {
            let mut accepted = 0;
            for position in 0..=PCM_INGRESS_QUEUE_CAPACITY {
                accepted += usize::from(producer_sender.submit_interleaved_f64_at(
                    &[0.2],
                    44_100,
                    1,
                    generation,
                    position as u64,
                    100 + position as u64,
                ));
            }
            submitted_tx.send(accepted).unwrap();
        });
        let accepted = submitted_rx
            .recv_timeout(std::time::Duration::from_secs(1))
            .expect("PCM producer must not wait for a slow consumer update");
        producer.join().unwrap();
        assert_eq!(accepted, PCM_INGRESS_QUEUE_CAPACITY);
        assert_eq!(sender.stats().queue_occupancy, PCM_INGRESS_QUEUE_CAPACITY);
        assert_eq!(sender.stats().dropped_samples, 1);

        let transitioning_sender = sender.clone();
        let (started_tx, started_rx) = mpsc::channel();
        let transition = thread::spawn(move || {
            started_tx.send(()).unwrap();
            transitioning_sender.begin_generation()
        });
        started_rx.recv().unwrap();
        release.wait();

        assert!(processed.join().unwrap().is_some());
        let new_generation = transition.join().unwrap();
        assert!(new_generation > generation);
        assert!(receiver.try_pop().is_none());
        assert_eq!(receiver.stats().queue_occupancy, 0);
        assert!(
            sender
                .process_current_generation(generation, || panic!("obsolete generation processed"))
                .is_none()
        );
    }
}
