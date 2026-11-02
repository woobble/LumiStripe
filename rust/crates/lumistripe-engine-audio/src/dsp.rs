use crate::config::{AUDIO_BANDS, AUDIO_HOP_SIZE, AUDIO_WINDOW_SIZE, AudioConfig};
use rustfft::num_complex::Complex;
use rustfft::{Fft, FftPlanner};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::Arc;

const HALF_WINDOW: usize = AUDIO_WINDOW_SIZE / 2;
pub const MAX_AUDIO_BEAT_EVENTS: usize = 64;
const BEAT_EVENT_REFRACTORY_SECONDS: f32 = 0.18;
const RMS_HISTORY_SIZE: usize = 200;
const MAX_IOI_BUFFER: usize = 12;
const WINDOW_SCALE: f32 = AUDIO_WINDOW_SIZE as f32 / 2.0;
const ONSET_THRESHOLD: f32 = 0.12;
const BPM_INTERVAL_MIN: f32 = 0.2;
const BPM_INTERVAL_MAX: f32 = 2.0;
const PROGRAM_ATTACK_SECONDS: f32 = 0.25;
const PROGRAM_RELEASE_SECONDS: f32 = 6.0;
const IMPACT_ATTACK: f32 = 0.4;
const IMPACT_RELEASE: f32 = 0.08;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct AudioFrame {
    pub rms: f32,
    pub bands: [f32; AUDIO_BANDS],
    pub beat: bool,
    pub beat_strength: f32,
    pub sequence: u64,
    pub timestamp: f64,
    pub fresh: bool,
}

impl Default for AudioFrame {
    fn default() -> Self {
        Self {
            rms: 0.0,
            bands: [0.0; AUDIO_BANDS],
            beat: false,
            beat_strength: 0.0,
            sequence: 0,
            timestamp: 0.0,
            fresh: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct MusicFeatures {
    pub bpm: f32,
    pub bpm_confidence: f32,
    pub energy: f32,
    pub volume: f32,
    pub energy_level: f32,
    pub bass: f32,
    pub brightness: f32,
    pub onset_strength: f32,
    pub dynamic_range: f32,
    pub beat: bool,
    pub beat_strength: f32,
    pub bands: [f32; AUDIO_BANDS],
    pub bass_energy: f32,
    pub mid_energy: f32,
    pub treble_energy: f32,
    pub spectral_centroid: f32,
    pub spectral_flux: f32,
    pub beat_confidence: f32,
    pub rolling_loudness: f32,
    pub program_loudness: f32,
    pub musical_impact: f32,
    pub silence: bool,
    pub drop_detected: bool,
    pub section_change: bool,
}

impl Default for MusicFeatures {
    fn default() -> Self {
        Self {
            bpm: 120.0,
            bpm_confidence: 0.0,
            energy: 0.0,
            volume: 0.0,
            energy_level: 0.0,
            bass: 0.0,
            brightness: 0.0,
            onset_strength: 0.0,
            dynamic_range: 0.0,
            beat: false,
            beat_strength: 0.0,
            bands: [0.0; AUDIO_BANDS],
            bass_energy: 0.0,
            mid_energy: 0.0,
            treble_energy: 0.0,
            spectral_centroid: 0.0,
            spectral_flux: 0.0,
            beat_confidence: 0.0,
            rolling_loudness: 0.0,
            program_loudness: 0.0,
            musical_impact: 0.0,
            silence: false,
            drop_detected: false,
            section_change: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct AudioBeatEvent {
    pub sequence: u64,
    pub sample_position: u64,
    pub timestamp: f64,
    pub strength: f32,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct AudioProcessorStats {
    pub feed_count: u64,
    pub samples_seen: u64,
    pub fft_count: u64,
    pub sample_abs_sum: f32,
    pub normalization_gain: f32,
    pub input_rms: f32,
    pub program_loudness: f32,
    pub musical_impact: f32,
    pub input_peak: f32,
    pub clipping_fraction: f32,
    pub microphone_gate_open: bool,
    pub native_dropped_beat_events: u64,
    pub queued_dropped_beat_events: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AudioProcessorError {
    InvalidSampleRate,
    SampleCounterOverflow,
}

impl std::fmt::Display for AudioProcessorError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidSampleRate => {
                formatter.write_str("sample rate must be finite and positive")
            }
            Self::SampleCounterOverflow => formatter.write_str("audio sample counter overflow"),
        }
    }
}

impl std::error::Error for AudioProcessorError {}

/// Stateful 2048-point / 512-hop audio processor.
///
/// All FFT, filter, and history buffers are allocated when the processor is
/// constructed. ``feed_samples`` performs no heap allocation.
pub struct AudioProcessor {
    config: AudioConfig,
    sample_rate: f32,
    fft: Arc<dyn Fft<f32>>,
    fft_buffer: Vec<Complex<f32>>,
    fft_scratch: Vec<Complex<f32>>,
    window: [f32; AUDIO_WINDOW_SIZE],
    frequencies: [f32; HALF_WINDOW],
    band_slices: [(usize, usize); AUDIO_BANDS],
    magnitudes: [f32; HALF_WINDOW],
    raw_buffer: [f32; AUDIO_WINDOW_SIZE],
    buffer: [f32; AUDIO_WINDOW_SIZE],
    buffer_pos: usize,
    previous_bass_energy: f32,
    smoothed_rms: f32,
    smoothed_bands: [f32; AUDIO_BANDS],
    beat_envelope: f32,
    level_estimate: f32,
    normalization_gain: f32,
    input_rms: f32,
    musical_impact: f32,
    feed_count: u64,
    samples_seen: u64,
    sample_abs_sum: f32,
    rms_history: [f32; RMS_HISTORY_SIZE],
    rms_history_count: u64,
    onset_history: [f32; 512],
    onset_history_count: u64,
    previous_rms: f32,
    previous_feature_bands: [f32; AUDIO_BANDS],
    previous_onset: f32,
    smooth_onset: f32,
    last_onset_sample_position: u64,
    ioi_buffer: [f32; MAX_IOI_BUFFER],
    ioi_buffer_len: usize,
    bpm: f32,
    brightness: f32,
    dynamic_range: f32,
    high_pass_input: f32,
    high_pass_output: f32,
    microphone_gate_open: bool,
    gate_envelope: f32,
    gate_hold: f32,
    input_peak: f32,
    clipping_fraction: f32,
    frame_rms: f32,
    frame_bands: [f32; AUDIO_BANDS],
    frame_beat: bool,
    frame_beat_strength: f32,
    frame_timestamp: f64,
    beat_events: VecDeque<AudioBeatEvent>,
    native_dropped_beat_events: u64,
    queued_dropped_beat_events: u64,
    last_beat_event_sample_position: u64,
    features: MusicFeatures,
    silence_frames: u32,
    drop_cooldown_frames: u32,
    section_cooldown_frames: u32,
    previous_drop_bass: f32,
    section_energy: f32,
    section_mid: f32,
    section_treble: f32,
    section_brightness: f32,
}

impl AudioProcessor {
    pub fn new(config: AudioConfig, sample_rate: f32) -> Result<Self, AudioProcessorError> {
        if !sample_rate.is_finite() || !(1.0..=i32::MAX as f32).contains(&sample_rate) {
            return Err(AudioProcessorError::InvalidSampleRate);
        }
        let mut planner = FftPlanner::<f32>::new();
        let fft = planner.plan_fft_forward(AUDIO_WINDOW_SIZE);
        let scratch_len = fft.get_inplace_scratch_len();
        let mut processor = Self {
            config,
            sample_rate,
            fft,
            fft_buffer: vec![Complex::default(); AUDIO_WINDOW_SIZE],
            fft_scratch: vec![Complex::default(); scratch_len],
            window: [0.0; AUDIO_WINDOW_SIZE],
            frequencies: [0.0; HALF_WINDOW],
            band_slices: [(0, 1); AUDIO_BANDS],
            magnitudes: [0.0; HALF_WINDOW],
            raw_buffer: [0.0; AUDIO_WINDOW_SIZE],
            buffer: [0.0; AUDIO_WINDOW_SIZE],
            buffer_pos: 0,
            previous_bass_energy: 0.0,
            smoothed_rms: 0.0,
            smoothed_bands: [0.0; AUDIO_BANDS],
            beat_envelope: 0.0,
            level_estimate: 0.0,
            normalization_gain: 1.0,
            input_rms: 0.0,
            musical_impact: 0.0,
            feed_count: 0,
            samples_seen: 0,
            sample_abs_sum: 0.0,
            rms_history: [0.0; RMS_HISTORY_SIZE],
            rms_history_count: 0,
            onset_history: [0.0; 512],
            onset_history_count: 0,
            previous_rms: 0.0,
            previous_feature_bands: [0.0; AUDIO_BANDS],
            previous_onset: 0.0,
            smooth_onset: 0.0,
            last_onset_sample_position: 0,
            ioi_buffer: [0.0; MAX_IOI_BUFFER],
            ioi_buffer_len: 0,
            bpm: 120.0,
            brightness: 0.0,
            dynamic_range: 0.0,
            high_pass_input: 0.0,
            high_pass_output: 0.0,
            microphone_gate_open: false,
            gate_envelope: 0.0,
            gate_hold: 0.0,
            input_peak: 0.0,
            clipping_fraction: 0.0,
            frame_rms: 0.0,
            frame_bands: [0.0; AUDIO_BANDS],
            frame_beat: false,
            frame_beat_strength: 0.0,
            frame_timestamp: 0.0,
            beat_events: VecDeque::with_capacity(MAX_AUDIO_BEAT_EVENTS),
            native_dropped_beat_events: 0,
            queued_dropped_beat_events: 0,
            last_beat_event_sample_position: 0,
            features: MusicFeatures::default(),
            silence_frames: 0,
            drop_cooldown_frames: 0,
            section_cooldown_frames: 0,
            previous_drop_bass: 0.0,
            section_energy: 0.0,
            section_mid: 0.0,
            section_treble: 0.0,
            section_brightness: 0.0,
        };
        processor.initialize_fft_tables();
        Ok(processor)
    }

    fn initialize_fft_tables(&mut self) {
        for index in 0..AUDIO_WINDOW_SIZE {
            self.window[index] = 0.5
                * (1.0
                    - (2.0 * std::f32::consts::PI * index as f32 / (AUDIO_WINDOW_SIZE - 1) as f32)
                        .cos());
        }
        for index in 0..HALF_WINDOW {
            self.frequencies[index] = index as f32 * self.sample_rate / AUDIO_WINDOW_SIZE as f32;
        }
        let limits = [
            (20.0, 60.0),
            (60.0, 120.0),
            (120.0, 250.0),
            (250.0, 500.0),
            (500.0, 1_000.0),
            (1_000.0, 2_500.0),
            (2_500.0, 6_000.0),
            (6_000.0, 16_000.0),
        ];
        for (band, (lower, upper)) in limits.into_iter().enumerate() {
            let start = self
                .frequencies
                .partition_point(|frequency| *frequency < lower);
            let mut end = self
                .frequencies
                .partition_point(|frequency| *frequency <= upper);
            let start = start.min(HALF_WINDOW - 1);
            if end < start + 1 {
                end = start + 1;
            }
            self.band_slices[band] = (start, end.min(HALF_WINDOW));
        }
    }

    /// Feed mono float PCM. ``capture_end_seconds`` is monotonic time at the
    /// final sample in this slice, in the process clock domain.
    pub fn feed_samples(
        &mut self,
        samples: &[f32],
        capture_end_seconds: f64,
    ) -> Result<(), AudioProcessorError> {
        self.feed_samples_with_observer(samples, capture_end_seconds, |_, _| {})
    }

    /// Feed PCM and observe every completed FFT frame in order.
    ///
    /// The callback runs synchronously on the DSP worker after each 512-sample
    /// hop. It must stay bounded; audio-device callbacks never call this method.
    pub fn feed_samples_with_observer<F>(
        &mut self,
        samples: &[f32],
        capture_end_seconds: f64,
        mut on_frame: F,
    ) -> Result<(), AudioProcessorError>
    where
        F: FnMut(AudioFrame, MusicFeatures),
    {
        if samples.is_empty() {
            return Ok(());
        }
        let sample_count =
            u64::try_from(samples.len()).map_err(|_| AudioProcessorError::SampleCounterOverflow)?;
        self.samples_seen
            .checked_add(sample_count)
            .ok_or(AudioProcessorError::SampleCounterOverflow)?;
        self.feed_count = self.feed_count.saturating_add(1);
        let feed_end_sample_position = self.samples_seen + sample_count;
        let capture_end_seconds = if capture_end_seconds.is_finite() {
            capture_end_seconds
        } else {
            0.0
        };
        let mut absolute_sum = 0.0_f32;
        for sample in samples.iter().copied() {
            if !sample.is_finite() {
                continue;
            }
            let magnitude = sample.abs();
            if magnitude >= f32::MAX - absolute_sum {
                absolute_sum = f32::MAX;
                break;
            }
            absolute_sum += magnitude;
        }
        self.sample_abs_sum = if absolute_sum >= f32::MAX - self.sample_abs_sum {
            f32::MAX
        } else {
            self.sample_abs_sum + absolute_sum
        };

        let high_pass_coefficient =
            (-2.0 * std::f32::consts::PI * self.config.high_pass_hz / self.sample_rate).exp();
        let mut offset = 0;
        while offset < samples.len() {
            let remaining = AUDIO_WINDOW_SIZE - self.buffer_pos;
            let chunk = remaining.min(samples.len() - offset);
            for (index, raw_sample) in samples[offset..offset + chunk].iter().copied().enumerate() {
                let sample = if raw_sample.is_finite() {
                    raw_sample
                } else {
                    0.0
                };
                let buffer_index = self.buffer_pos + index;
                self.raw_buffer[buffer_index] = sample;
                let conditioned =
                    if self.config.microphone_enabled && self.config.high_pass_hz > 0.0 {
                        let filtered = high_pass_coefficient
                            * (self.high_pass_output + sample - self.high_pass_input);
                        self.high_pass_input = sample;
                        self.high_pass_output = filtered;
                        filtered
                    } else {
                        sample
                    };
                self.buffer[buffer_index] = conditioned;
            }
            self.buffer_pos += chunk;
            offset += chunk;
            self.samples_seen += chunk as u64;
            if self.buffer_pos == AUDIO_WINDOW_SIZE {
                self.process_fft(capture_end_seconds, feed_end_sample_position);
                on_frame(self.frame(), self.features());
                self.buffer
                    .copy_within(AUDIO_HOP_SIZE..AUDIO_WINDOW_SIZE, 0);
                self.raw_buffer
                    .copy_within(AUDIO_HOP_SIZE..AUDIO_WINDOW_SIZE, 0);
                self.buffer_pos = AUDIO_WINDOW_SIZE - AUDIO_HOP_SIZE;
            }
        }
        Ok(())
    }

    pub fn mark_discontinuity(&mut self, skipped_samples: u64) -> Result<(), AudioProcessorError> {
        self.samples_seen = self
            .samples_seen
            .checked_add(skipped_samples)
            .ok_or(AudioProcessorError::SampleCounterOverflow)?;
        Ok(())
    }

    pub fn frame(&self) -> AudioFrame {
        AudioFrame {
            rms: self.frame_rms,
            bands: self.frame_bands,
            beat: self.frame_beat,
            beat_strength: self.frame_beat_strength,
            sequence: self.rms_history_count,
            timestamp: self.features_timestamp(),
            fresh: self.rms_history_count > 0,
        }
    }

    fn features_timestamp(&self) -> f64 {
        self.frame_timestamp
    }

    pub fn features(&self) -> MusicFeatures {
        self.features
    }

    pub fn normalization_gain(&self) -> f32 {
        self.normalization_gain
    }

    pub fn stats(&self) -> AudioProcessorStats {
        AudioProcessorStats {
            feed_count: self.feed_count,
            samples_seen: self.samples_seen,
            fft_count: self.rms_history_count,
            sample_abs_sum: self.sample_abs_sum,
            normalization_gain: self.normalization_gain,
            input_rms: self.input_rms,
            program_loudness: self.level_estimate,
            musical_impact: self.musical_impact,
            input_peak: self.input_peak,
            clipping_fraction: self.clipping_fraction,
            microphone_gate_open: self.microphone_gate_open,
            native_dropped_beat_events: self.native_dropped_beat_events,
            queued_dropped_beat_events: self.queued_dropped_beat_events,
        }
    }

    pub fn drain_beat_events(&mut self) -> Vec<AudioBeatEvent> {
        self.beat_events.drain(..).collect()
    }

    /// Drain beat events into a fixed-size caller buffer without allocating.
    pub fn drain_beat_events_into(
        &mut self,
        events: &mut [AudioBeatEvent; MAX_AUDIO_BEAT_EVENTS],
    ) -> usize {
        let mut count = 0;
        while count < events.len() {
            let Some(event) = self.beat_events.pop_front() else {
                break;
            };
            events[count] = event;
            count += 1;
        }
        count
    }

    fn process_fft(&mut self, capture_end_seconds: f64, feed_end_sample_position: u64) {
        let mut input_peak = 0.0_f32;
        let mut clipped = 0_u32;
        for sample in self.raw_buffer.iter().copied() {
            let magnitude = sample.abs();
            input_peak = input_peak.max(magnitude);
            if magnitude >= 0.995 {
                clipped += 1;
            }
        }
        self.input_peak = input_peak.min(1.0);
        self.clipping_fraction = clipped as f32 / AUDIO_WINDOW_SIZE as f32;

        let mut normalized = [0.0_f32; AUDIO_WINDOW_SIZE];
        if self.config.dc_block_enabled {
            let mean = self.buffer.iter().copied().sum::<f32>() / AUDIO_WINDOW_SIZE as f32;
            for (target, sample) in normalized.iter_mut().zip(self.buffer.iter().copied()) {
                *target = sample - mean;
            }
        } else {
            normalized.copy_from_slice(&self.buffer);
        }

        let input_level = (normalized.iter().map(|sample| sample * sample).sum::<f32>()
            / AUDIO_WINDOW_SIZE as f32)
            .sqrt()
            .min(1.0);
        self.input_rms = input_level;
        let hop_seconds = AUDIO_HOP_SIZE as f32 / self.sample_rate;
        let mut gate = 1.0_f32;
        if self.config.microphone_enabled {
            let open_level = (self.config.input_noise_floor * 2.0).max(0.0001);
            let close_level = open_level * 0.7;
            if input_level >= open_level {
                self.microphone_gate_open = true;
                self.gate_hold = 0.12;
            } else if input_level < close_level {
                self.gate_hold = (self.gate_hold - hop_seconds).max(0.0);
                if self.gate_hold <= 0.0 {
                    self.microphone_gate_open = false;
                }
            }
            let target = if self.microphone_gate_open { 1.0 } else { 0.0 };
            let seconds = if self.microphone_gate_open {
                0.01
            } else {
                0.08
            };
            self.gate_envelope +=
                (target - self.gate_envelope) * (1.0 - (-hop_seconds / seconds).exp());
            gate = self.gate_envelope;
            if !self.microphone_gate_open && gate < 0.0001 {
                gate = 0.0;
            }
        }

        let reference_floor = self
            .config
            .music_threshold
            .max(self.config.target_level / self.config.music_max_gain.max(1.0));
        let reference_target = input_level.max(reference_floor);
        let reference_attack = 1.0 - (-hop_seconds / PROGRAM_ATTACK_SECONDS).exp();
        let reference_release = 1.0 - (-hop_seconds / PROGRAM_RELEASE_SECONDS).exp();
        self.level_estimate = smooth(
            self.level_estimate.max(reference_floor),
            reference_target,
            reference_attack,
            reference_release,
        );

        let input_activity_threshold = if self.config.microphone_enabled {
            self.config.input_noise_floor * 2.0
        } else {
            self.config.music_threshold
        };
        let mut impact_target = 0.0;
        if input_level >= input_activity_threshold
            && (!self.config.microphone_enabled || self.microphone_gate_open)
        {
            impact_target = clamp01(input_level / self.level_estimate.max(reference_floor));
        }
        self.musical_impact = smooth(
            self.musical_impact,
            impact_target,
            IMPACT_ATTACK,
            IMPACT_RELEASE,
        );

        if self.config.normalization_enabled {
            let effective_level = self.level_estimate.max(reference_floor);
            let target_gain = if input_level < input_activity_threshold
                || (self.config.microphone_enabled && !self.microphone_gate_open)
            {
                1.0
            } else {
                (self.config.target_level / effective_level)
                    .min(self.config.music_max_gain)
                    .max(self.config.min_gain)
                    .min(self.config.max_gain)
            };
            let gain_factor = if target_gain > self.normalization_gain {
                self.config.adapt_attack
            } else {
                self.config.adapt_release
            };
            self.normalization_gain = smooth(
                self.normalization_gain,
                target_gain,
                gain_factor,
                gain_factor,
            );
            if self.config.microphone_enabled {
                let peak = normalized
                    .iter()
                    .map(|sample| sample.abs())
                    .fold(0.0_f32, f32::max);
                if peak > 0.0 {
                    self.normalization_gain = self.normalization_gain.min(0.95 / peak);
                }
            }
            for sample in &mut normalized {
                *sample = (*sample * self.normalization_gain).clamp(-1.0, 1.0);
            }
        }
        for sample in &mut normalized {
            *sample *= gate;
        }
        let raw_rms = (normalized.iter().map(|sample| sample * sample).sum::<f32>()
            / AUDIO_WINDOW_SIZE as f32)
            .sqrt()
            .min(1.0);

        for index in 0..AUDIO_WINDOW_SIZE {
            self.fft_buffer[index] = Complex::new(normalized[index] * self.window[index], 0.0);
        }
        self.fft
            .process_with_scratch(&mut self.fft_buffer, &mut self.fft_scratch);
        for (index, magnitude) in self.magnitudes.iter_mut().enumerate() {
            *magnitude = self.fft_buffer[index].norm() / WINDOW_SCALE;
        }

        let mut raw_bands = [0.0_f32; AUDIO_BANDS];
        for (band, (start, end)) in self.band_slices.iter().copied().enumerate() {
            let count = end.saturating_sub(start);
            if count == 0 {
                continue;
            }
            let value = (self.magnitudes[start..end]
                .iter()
                .map(|magnitude| magnitude * magnitude)
                .sum::<f32>()
                / count as f32)
                .sqrt();
            let compressed = if value > 0.0 {
                value / (value + 0.12)
            } else {
                0.0
            };
            raw_bands[band] = (compressed * 1.2).min(1.0);
        }
        let gated_rms = apply_noise_floor(raw_rms, self.config.noise_floor);
        let mut gated_bands = [0.0_f32; AUDIO_BANDS];
        for (gated, raw) in gated_bands.iter_mut().zip(raw_bands) {
            *gated = apply_noise_floor(raw, self.config.noise_floor);
        }

        let mut bass = gated_bands[0];
        if self.config.microphone_enabled {
            let mut sum = 0.0;
            let mut count = 0;
            for index in 1..HALF_WINDOW {
                let frequency = self.frequencies[index];
                if (40.0..=180.0).contains(&frequency) {
                    sum += self.magnitudes[index] * self.magnitudes[index];
                    count += 1;
                }
            }
            let value = if count > 0 {
                (sum / count as f32).sqrt()
            } else {
                0.0
            };
            bass = apply_noise_floor(
                (1.2 * value / (value + 0.12)).min(1.0),
                self.config.noise_floor,
            );
        }
        let bass_rise = bass - self.previous_bass_energy;
        let threshold = (self.previous_bass_energy * 1.12).max(self.config.noise_floor + 0.01);
        let beat = bass > threshold && bass_rise > 0.015;
        let raw_beat_strength = if beat {
            (bass_rise * 1.6).min(1.0)
        } else {
            0.0
        };
        self.previous_bass_energy = self.previous_bass_energy * 0.9 + bass * 0.1;

        let (rms, bands, beat_strength) = if self.config.smoothing_enabled {
            self.smoothed_rms = smooth(
                self.smoothed_rms,
                gated_rms,
                self.config.rms_attack,
                self.config.rms_release,
            );
            for (current, target) in self.smoothed_bands.iter_mut().zip(gated_bands) {
                *current = smooth(
                    *current,
                    target,
                    self.config.band_attack,
                    self.config.band_release,
                );
            }
            self.beat_envelope = smooth(
                self.beat_envelope,
                raw_beat_strength,
                1.0,
                self.config.beat_release,
            );
            (self.smoothed_rms, self.smoothed_bands, self.beat_envelope)
        } else {
            self.smoothed_rms = gated_rms;
            self.smoothed_bands = gated_bands;
            self.beat_envelope = raw_beat_strength;
            (gated_rms, gated_bands, raw_beat_strength)
        };
        self.frame_rms = rms;
        self.frame_bands = bands;
        self.compute_features(rms, bands, beat, beat_strength);
        self.frame_beat = self.features.beat;
        self.frame_beat_strength = self.features.beat_strength;
        self.rms_history_count = self.rms_history_count.saturating_add(1);

        let refractory_samples = (BEAT_EVENT_REFRACTORY_SECONDS * self.sample_rate) as u64;
        if self.frame_beat
            && (self.last_beat_event_sample_position == 0
                || self
                    .samples_seen
                    .saturating_sub(self.last_beat_event_sample_position)
                    >= refractory_samples)
        {
            let event_position = self.samples_seen;
            let timestamp = capture_end_seconds
                + (event_position as i128 - feed_end_sample_position as i128) as f64
                    / self.sample_rate as f64;
            let event = AudioBeatEvent {
                sequence: self.rms_history_count,
                sample_position: event_position,
                timestamp,
                strength: self.frame_beat_strength,
            };
            if self.beat_events.len() >= MAX_AUDIO_BEAT_EVENTS {
                self.beat_events.pop_front();
                self.queued_dropped_beat_events = self.queued_dropped_beat_events.saturating_add(1);
            }
            self.beat_events.push_back(event);
            self.last_beat_event_sample_position = event_position;
        }
        self.frame_timestamp = capture_end_seconds
            + (self.samples_seen as i128 - feed_end_sample_position as i128) as f64
                / self.sample_rate as f64;
    }

    fn compute_features(
        &mut self,
        rms: f32,
        bands: [f32; AUDIO_BANDS],
        beat: bool,
        beat_strength: f32,
    ) {
        self.rms_history[self.rms_history_count as usize % RMS_HISTORY_SIZE] = rms;
        let history_len = (self.rms_history_count + 1).min(RMS_HISTORY_SIZE as u64) as usize;
        self.rms_history_count = self.rms_history_count.saturating_add(0);

        let weights = [0.2, 0.3, 0.8, 1.0, 1.15, 1.35, 1.5, 1.6];
        let mut weighted_band_energy = 0.0;
        let mut raw_flux = 0.0;
        for band in 0..AUDIO_BANDS {
            let change = (bands[band] - self.previous_feature_bands[band]).max(0.0);
            weighted_band_energy += bands[band] * weights[band];
            raw_flux += change * weights[band];
            self.previous_feature_bands[band] = bands[band];
        }
        let spectral_flux = (raw_flux / weighted_band_energy.max(0.08)).min(1.0);
        let onset_raw = ((rms - self.previous_rms).max(0.0) * 0.55 + spectral_flux * 0.85).min(1.0);
        self.previous_rms = rms;
        self.smooth_onset = smooth(self.smooth_onset, onset_raw, 0.3, 0.08);
        self.onset_history[self.onset_history_count as usize % self.onset_history.len()] =
            self.smooth_onset;
        self.onset_history_count = self.onset_history_count.saturating_add(1);

        self.features.rolling_loudness = smooth(self.features.rolling_loudness, rms, 0.08, 0.025);
        self.features.program_loudness = self.level_estimate;
        self.features.musical_impact = self.musical_impact;
        let silence_threshold = (self.config.silence_floor * 3.0)
            .max(self.config.noise_floor * 0.8)
            .max(0.012);
        let silent_now =
            self.features.rolling_loudness <= silence_threshold && rms <= silence_threshold * 1.4;

        if !silent_now
            && self.smooth_onset > ONSET_THRESHOLD
            && self.smooth_onset > self.previous_onset * 1.4
        {
            let interval_samples = self
                .samples_seen
                .saturating_sub(self.last_onset_sample_position);
            let interval_seconds = interval_samples as f32 / self.sample_rate;
            if interval_seconds > BPM_INTERVAL_MIN && interval_seconds < BPM_INTERVAL_MAX {
                if self.ioi_buffer_len < MAX_IOI_BUFFER {
                    self.ioi_buffer[self.ioi_buffer_len] = interval_seconds;
                    self.ioi_buffer_len += 1;
                } else {
                    self.ioi_buffer.copy_within(1..MAX_IOI_BUFFER, 0);
                    self.ioi_buffer[MAX_IOI_BUFFER - 1] = interval_seconds;
                }
                let mut ordered = [0.0_f32; MAX_IOI_BUFFER];
                ordered[..self.ioi_buffer_len]
                    .copy_from_slice(&self.ioi_buffer[..self.ioi_buffer_len]);
                ordered[..self.ioi_buffer_len].sort_by(f32::total_cmp);
                self.bpm = 60.0 / ordered[self.ioi_buffer_len / 2];
            }
            self.last_onset_sample_position = self.samples_seen;
        }
        self.previous_onset = self.smooth_onset;

        let total_magnitude = self.magnitudes.iter().copied().sum::<f32>();
        let centroid_norm = if total_magnitude > 1e-9 {
            let weighted_sum = self
                .frequencies
                .iter()
                .zip(self.magnitudes.iter())
                .map(|(frequency, magnitude)| frequency * magnitude)
                .sum::<f32>();
            weighted_sum / total_magnitude / (self.sample_rate / 2.0)
        } else {
            0.0
        };
        let total_band_energy = bands.iter().copied().sum::<f32>();
        let (off_bass_share, high_share) = if total_band_energy > 1e-9 {
            (
                bands[2..].iter().copied().sum::<f32>() / total_band_energy,
                bands[5..].iter().copied().sum::<f32>() / total_band_energy,
            )
        } else {
            (0.0, 0.0)
        };
        self.brightness =
            (centroid_norm * 0.35 + off_bass_share * 0.75 + high_share * 0.35).min(1.0);

        if history_len > 10 {
            let mut ordered = [0.0_f32; RMS_HISTORY_SIZE];
            ordered[..history_len].copy_from_slice(&self.rms_history[..history_len]);
            ordered[..history_len].sort_by(f32::total_cmp);
            let p95 = percentile_sorted(&ordered[..history_len], 95.0);
            let p10 = percentile_sorted(&ordered[..history_len], 10.0);
            self.dynamic_range = (p95 - p10).max(0.0);
        } else {
            self.dynamic_range = 0.0;
        }

        let bass = (bands[0] + bands[1]) * 0.5;
        let mid = (bands[2] + bands[3] + bands[4]) / 3.0;
        let treble = (bands[5] + bands[6] + bands[7]) / 3.0;
        let bass_delta = bass - self.previous_drop_bass;
        if silent_now {
            self.silence_frames = (self.silence_frames + 1).min(8);
        } else {
            self.silence_frames = 0;
        }
        let silent = self.silence_frames >= 8;

        self.drop_cooldown_frames = self.drop_cooldown_frames.saturating_sub(1);
        let mut drop_detected = false;
        if self.drop_cooldown_frames == 0
            && !silent
            && bass > self.config.drop_bass_threshold
            && bass_delta > self.config.drop_bass_delta_threshold
            && self.smooth_onset > self.config.drop_onset_threshold
            && (beat || beat_strength > 0.08 || self.features.rolling_loudness < rms * 0.85)
        {
            drop_detected = true;
            self.drop_cooldown_frames = 24;
        }
        self.previous_drop_bass = smooth(self.previous_drop_bass, bass, 0.18, 0.06);

        if self.rms_history_count == 0 {
            self.section_energy = rms;
            self.section_mid = mid;
            self.section_treble = treble;
            self.section_brightness = self.brightness;
        }
        self.section_cooldown_frames = self.section_cooldown_frames.saturating_sub(1);
        let section_delta = (rms - self.section_energy).abs() * 0.9
            + (mid - self.section_mid).abs() * 0.45
            + (treble - self.section_treble).abs() * 0.45
            + (self.brightness - self.section_brightness).abs() * 0.35;
        let mut section_change = false;
        if self.rms_history_count + 1 > 20
            && self.section_cooldown_frames == 0
            && !silent
            && section_delta > self.config.section_change_threshold
            && self.smooth_onset > self.config.section_onset_threshold
        {
            section_change = true;
            self.section_cooldown_frames = 60;
            self.section_energy = rms;
            self.section_mid = mid;
            self.section_treble = treble;
            self.section_brightness = self.brightness;
        } else {
            self.section_energy = smooth(self.section_energy, rms, 0.018, 0.018);
            self.section_mid = smooth(self.section_mid, mid, 0.018, 0.018);
            self.section_treble = smooth(self.section_treble, treble, 0.018, 0.018);
            self.section_brightness =
                smooth(self.section_brightness, self.brightness, 0.018, 0.018);
        }

        let features_beat = !silent && beat;
        let features_beat_strength = if silent { 0.0 } else { beat_strength };
        let beat_confidence = if silent {
            0.0
        } else {
            clamp01(beat_strength.max(self.smooth_onset * 0.55))
        };
        self.features = MusicFeatures {
            bpm: self.bpm,
            bpm_confidence: beat_confidence * 0.6
                + ((self.bpm - 40.0) / 200.0).clamp(0.0, 1.0) * 0.4,
            energy: rms,
            volume: rms,
            energy_level: rms,
            bass,
            brightness: self.brightness,
            onset_strength: self.smooth_onset,
            dynamic_range: self.dynamic_range,
            beat: features_beat,
            beat_strength: features_beat_strength,
            bands,
            bass_energy: bass,
            mid_energy: mid,
            treble_energy: treble,
            spectral_centroid: clamp01(centroid_norm),
            spectral_flux: clamp01(spectral_flux),
            beat_confidence,
            rolling_loudness: self.features.rolling_loudness,
            program_loudness: self.features.program_loudness,
            musical_impact: self.features.musical_impact,
            silence: silent,
            drop_detected,
            section_change,
        };
    }
}

fn smooth(current: f32, target: f32, attack: f32, release: f32) -> f32 {
    let factor = if target >= current { attack } else { release }.clamp(0.0, 1.0);
    current + (target - current) * factor
}

fn clamp01(value: f32) -> f32 {
    value.clamp(0.0, 1.0)
}

fn apply_noise_floor(value: f32, floor: f32) -> f32 {
    let floor = floor.clamp(0.0, 0.99);
    if value <= floor {
        return 0.0;
    }
    ((value - floor) / (1.0 - floor)).clamp(0.0, 1.0)
}

fn percentile_sorted(sorted: &[f32], percentile: f32) -> f32 {
    let index = percentile / 100.0 * (sorted.len() - 1) as f32;
    let lower = index as usize;
    let fraction = index - lower as f32;
    if lower + 1 < sorted.len() {
        sorted[lower] + fraction * (sorted[lower + 1] - sorted[lower])
    } else {
        sorted[lower]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn beat_events_drain_into_fixed_buffer_in_sequence_order() {
        let mut processor = AudioProcessor::new(AudioConfig::default(), 44_100.0).unwrap();
        for sequence in 1..=3 {
            processor.beat_events.push_back(AudioBeatEvent {
                sequence,
                sample_position: sequence * 512,
                timestamp: sequence as f64 / 10.0,
                strength: sequence as f32 / 3.0,
            });
        }

        let mut events = [AudioBeatEvent::default(); MAX_AUDIO_BEAT_EVENTS];
        let count = processor.drain_beat_events_into(&mut events);
        assert_eq!(count, 3);
        assert_eq!(
            events[..count]
                .iter()
                .map(|event| event.sequence)
                .collect::<Vec<_>>(),
            [1, 2, 3]
        );
        assert_eq!(processor.drain_beat_events_into(&mut events), 0);
    }
}
