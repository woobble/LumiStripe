use crate::dsp::MusicFeatures;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

const HISTORY_SECONDS: f64 = 8.0;
const HISTORY_MAX_SAMPLES: usize = 256;
const SMOOTHING_TAU_SECONDS: f64 = 0.45;
const MAX_CONTIGUOUS_GAP_SECONDS: f64 = 2.0;
const BUILDUP_WINDOW_SECONDS: f64 = 2.5;
const BUILDUP_MIN_SPAN_SECONDS: f64 = 0.8;
const BUILDUP_MIN_RISE: f64 = 0.12;
const BUILDUP_MIN_SLOPE: f64 = 0.045;
const NATIVE_BUILDUP_WINDOW_SECONDS: f64 = 8.0;
const NATIVE_BUILDUP_MIN_SPAN_SECONDS: f64 = 1.2;
const NATIVE_BUILDUP_MIN_RISE: f64 = 0.008;
const NATIVE_BUILDUP_MIN_SLOPE: f64 = 0.005;
const NATIVE_GROOVE_BUILDUP_ARM_SECONDS: f64 = 4.0;
const NATIVE_BUILDUP_CONFIRM_SECONDS: f64 = 1.2;
const BUILDUP_CONFIRM_SECONDS: f64 = 0.3;
const BREAKDOWN_ENERGY: f64 = 0.12;
const BREAKDOWN_CONFIRM_SECONDS: f64 = 0.65;
const GROOVE_ENERGY: f64 = 0.14;
const GROOVE_CONFIRM_SECONDS: f64 = 0.35;
const DROP_COOLDOWN_SECONDS: f64 = 4.0;
const DROP_HOLD_SECONDS: f64 = 1.0;
const NATIVE_DROP_HOLD_SECONDS: f64 = 2.5;
const NATIVE_DROP_MIN_BUILDUP_SECONDS: f64 = 8.0;
const NATIVE_DROP_ENERGY_JUMP: f64 = 0.02;
const NATIVE_DROP_COMPARISON_SECONDS: f64 = 1.5;
const GROOVE_REFERENCE_TAU_SECONDS: f64 = 6.0;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MusicStructurePhase {
    Idle,
    Breakdown,
    Groove,
    Buildup,
    Drop,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct MusicStructureState {
    pub phase: MusicStructurePhase,
    pub confidence: f64,
    pub buildup_progress: f64,
    pub drop_event: bool,
}

impl Default for MusicStructureState {
    fn default() -> Self {
        Self {
            phase: MusicStructurePhase::Idle,
            confidence: 0.0,
            buildup_progress: 0.0,
            drop_event: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct StructureUpdate {
    pub state: MusicStructureState,
    pub phase_changed: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StructureTrackerError;

impl std::fmt::Display for StructureTrackerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("music-structure timestamp must be finite and monotonic")
    }
}

impl std::error::Error for StructureTrackerError {}

#[derive(Clone, Copy, Debug, Default)]
struct Sample {
    timestamp: f64,
    energy: f64,
    raw_energy: f64,
    bass: f64,
    onset: f64,
}

/// Bounded port of the Python ``MusicStructureTracker``.
///
/// The history is reserved at construction and per-update medians use fixed
/// stack buffers, so the steady-state update path does not allocate.
pub struct MusicStructureTracker {
    history: VecDeque<Sample>,
    groove_reference_energy: f64,
    last_timestamp: Option<f64>,
    smoothed_energy: f64,
    uses_rolling_energy: bool,
    phase: MusicStructurePhase,
    phase_since: f64,
    candidate_phase: Option<MusicStructurePhase>,
    candidate_since: f64,
    last_drop_at: Option<f64>,
    buildup_progress: f64,
    buildup_start_energy: f64,
    confidence: f64,
}

impl Default for MusicStructureTracker {
    fn default() -> Self {
        Self::new()
    }
}

impl MusicStructureTracker {
    pub fn new() -> Self {
        Self {
            history: VecDeque::with_capacity(HISTORY_MAX_SAMPLES),
            groove_reference_energy: 0.0,
            last_timestamp: None,
            smoothed_energy: 0.0,
            uses_rolling_energy: false,
            phase: MusicStructurePhase::Idle,
            phase_since: 0.0,
            candidate_phase: None,
            candidate_since: 0.0,
            last_drop_at: None,
            buildup_progress: 0.0,
            buildup_start_energy: 0.0,
            confidence: 0.0,
        }
    }

    pub fn reset(&mut self) {
        self.history.clear();
        self.groove_reference_energy = 0.0;
        self.last_timestamp = None;
        self.smoothed_energy = 0.0;
        self.uses_rolling_energy = false;
        self.phase = MusicStructurePhase::Idle;
        self.phase_since = 0.0;
        self.candidate_phase = None;
        self.candidate_since = 0.0;
        self.last_drop_at = None;
        self.buildup_progress = 0.0;
        self.buildup_start_energy = 0.0;
        self.confidence = 0.0;
    }

    pub fn state(&self) -> MusicStructureState {
        self.state_with_drop(false)
    }

    pub fn update(
        &mut self,
        features: &MusicFeatures,
        now_seconds: f64,
    ) -> Result<StructureUpdate, StructureTrackerError> {
        if !now_seconds.is_finite()
            || self
                .last_timestamp
                .is_some_and(|last_timestamp| now_seconds < last_timestamp)
        {
            return Err(StructureTrackerError);
        }
        let previous_phase = self.phase;
        let previous_timestamp = self.last_timestamp;
        let mut elapsed = previous_timestamp.map_or(0.0, |previous| now_seconds - previous);
        let gap_reset = elapsed > MAX_CONTIGUOUS_GAP_SECONDS;
        if gap_reset {
            self.clear_signal_state();
            elapsed = 0.0;
        }

        let (energy, raw_energy, bass, onset, silent) = feature_values(features);
        self.uses_rolling_energy = features.rolling_loudness > 0.003;
        if silent {
            self.history.clear();
            self.groove_reference_energy = 0.0;
            self.smoothed_energy = 0.0;
            self.uses_rolling_energy = false;
            self.phase = MusicStructurePhase::Idle;
            self.phase_since = now_seconds;
            self.candidate_phase = None;
            self.candidate_since = now_seconds;
            self.last_drop_at = None;
            self.buildup_progress = 0.0;
            self.buildup_start_energy = 0.0;
            self.confidence = 1.0;
            self.last_timestamp = Some(now_seconds);
            self.push_history(Sample {
                timestamp: now_seconds,
                bass,
                onset,
                ..Sample::default()
            });
            return Ok(StructureUpdate {
                state: self.state_with_drop(false),
                phase_changed: self.phase != previous_phase,
            });
        }

        if gap_reset || previous_timestamp.is_none() {
            self.smoothed_energy = energy;
        } else if elapsed > 0.0 {
            let alpha = 1.0 - (-elapsed / SMOOTHING_TAU_SECONDS).exp();
            self.smoothed_energy += (energy - self.smoothed_energy) * alpha;
        }
        self.last_timestamp = Some(now_seconds);
        self.push_history(Sample {
            timestamp: now_seconds,
            energy: self.smoothed_energy,
            raw_energy,
            bass,
            onset,
        });

        while self
            .history
            .front()
            .is_some_and(|sample| now_seconds - sample.timestamp > HISTORY_SECONDS)
        {
            self.history.pop_front();
        }

        if energy <= 0.008 {
            self.transition(MusicStructurePhase::Idle, now_seconds);
            self.buildup_progress = 0.0;
            self.confidence = 0.9;
            return Ok(StructureUpdate {
                state: self.state_with_drop(false),
                phase_changed: self.phase != previous_phase,
            });
        }

        if self.phase == MusicStructurePhase::Groove && elapsed > 0.0 {
            let alpha = 1.0 - (-elapsed / GROOVE_REFERENCE_TAU_SECONDS).exp();
            if self.groove_reference_energy <= 0.0 {
                self.groove_reference_energy = self.smoothed_energy;
            } else if self.smoothed_energy > self.groove_reference_energy {
                self.groove_reference_energy +=
                    (self.smoothed_energy - self.groove_reference_energy) * alpha;
            }
        }

        if self.drop_confirmed(features, now_seconds) {
            self.transition(MusicStructurePhase::Drop, now_seconds);
            self.last_drop_at = Some(now_seconds);
            self.buildup_progress = 1.0;
            self.confidence = 1.0;
            return Ok(StructureUpdate {
                state: self.state_with_drop(true),
                phase_changed: self.phase != previous_phase,
            });
        }

        let drop_hold_seconds = if self.uses_rolling_energy {
            NATIVE_DROP_HOLD_SECONDS
        } else {
            DROP_HOLD_SECONDS
        };
        if self.phase == MusicStructurePhase::Drop
            && now_seconds - self.phase_since < drop_hold_seconds
        {
            self.confidence = 0.75_f64.max(self.confidence * 0.96);
            return Ok(StructureUpdate {
                state: self.state_with_drop(false),
                phase_changed: self.phase != previous_phase,
            });
        }

        let (desired, rise, slope) = self.desired_phase(now_seconds);
        let confirm_seconds = match desired {
            MusicStructurePhase::Idle => 0.0,
            MusicStructurePhase::Breakdown => BREAKDOWN_CONFIRM_SECONDS,
            MusicStructurePhase::Groove => GROOVE_CONFIRM_SECONDS,
            MusicStructurePhase::Buildup if self.uses_rolling_energy => {
                NATIVE_BUILDUP_CONFIRM_SECONDS
            }
            MusicStructurePhase::Buildup => BUILDUP_CONFIRM_SECONDS,
            MusicStructurePhase::Drop => 0.0,
        };
        if self.update_candidate(desired, now_seconds, confirm_seconds) {
            self.transition(desired, now_seconds);
        }

        match self.phase {
            MusicStructurePhase::Buildup => {
                let current_progress =
                    clamp01((self.smoothed_energy - self.buildup_start_energy) / 0.45);
                self.buildup_progress = self.buildup_progress.max(current_progress);
                self.confidence = clamp01(0.55 + (rise / 0.5).min(0.25) + (slope / 0.4).min(0.2));
            }
            MusicStructurePhase::Breakdown => {
                let duration = (now_seconds - self.phase_since).max(0.0);
                let low_energy_evidence = (BREAKDOWN_ENERGY - self.smoothed_energy).max(0.0);
                self.confidence = clamp01(0.58 + (duration / 3.0).min(0.25) + low_energy_evidence);
                self.buildup_progress = 0.0;
            }
            MusicStructurePhase::Groove => {
                let duration = (now_seconds - self.phase_since).max(0.0);
                self.confidence = clamp01(0.62 + (duration / 4.0).min(0.25));
                self.buildup_progress = 0.0;
            }
            MusicStructurePhase::Idle => {
                self.confidence = 0.65;
                self.buildup_progress = 0.0;
            }
            MusicStructurePhase::Drop => {
                self.confidence = 0.8;
                self.buildup_progress = 0.0;
            }
        }
        Ok(StructureUpdate {
            state: self.state_with_drop(false),
            phase_changed: self.phase != previous_phase,
        })
    }

    fn push_history(&mut self, sample: Sample) {
        if self
            .history
            .back()
            .is_some_and(|previous| previous.timestamp == sample.timestamp)
        {
            self.history.pop_back();
        }
        if self.history.len() == HISTORY_MAX_SAMPLES {
            self.history.pop_front();
        }
        self.history.push_back(sample);
    }

    fn clear_signal_state(&mut self) {
        self.history.clear();
        self.groove_reference_energy = 0.0;
        self.smoothed_energy = 0.0;
        self.uses_rolling_energy = false;
        self.phase = MusicStructurePhase::Idle;
        self.phase_since = self.last_timestamp.unwrap_or(0.0);
        self.candidate_phase = None;
        self.candidate_since = self.phase_since;
        self.last_drop_at = None;
        self.buildup_progress = 0.0;
        self.buildup_start_energy = 0.0;
        self.confidence = 0.0;
    }

    fn drop_confirmed(&self, features: &MusicFeatures, now_seconds: f64) -> bool {
        if self.phase != MusicStructurePhase::Buildup {
            return false;
        }
        if self.uses_rolling_energy
            && now_seconds - self.phase_since < NATIVE_DROP_MIN_BUILDUP_SECONDS
        {
            return false;
        }
        if self
            .last_drop_at
            .is_some_and(|last_drop| now_seconds - last_drop < DROP_COOLDOWN_SECONDS)
        {
            return false;
        }

        if self.uses_rolling_energy {
            let mut recent = [Sample::default(); HISTORY_MAX_SAMPLES];
            let count = self.copy_recent(
                now_seconds,
                NATIVE_DROP_COMPARISON_SECONDS,
                false,
                &mut recent,
            );
            if count < 2 {
                return false;
            }
            let baseline = median_samples(&recent[..count - 1], |sample| sample.energy);
            return recent[count - 1].energy - baseline >= NATIVE_DROP_ENERGY_JUMP;
        }

        let mut recent = [Sample::default(); HISTORY_MAX_SAMPLES];
        let count = self.copy_recent(now_seconds, 0.8, false, &mut recent);
        if count < 2 {
            return false;
        }
        let baseline_energy = median_samples(&recent[..count - 1], |sample| sample.raw_energy);
        let baseline_bass = median_samples(&recent[..count - 1], |sample| sample.bass);
        let current = recent[count - 1];
        let energy_jump = current.raw_energy - baseline_energy;
        let bass_jump = current.bass - baseline_bass;
        let explicit_evidence = features.drop_detected
            && current.raw_energy >= 0.24
            && current.bass >= 0.35
            && current.onset >= 0.08;
        let transient_evidence = current.onset >= 0.16
            && current.raw_energy >= 0.3
            && (energy_jump >= 0.1 || (current.bass >= 0.45 && bass_jump >= 0.1));
        let section_evidence = features.section_change
            && current.onset >= 0.16
            && current.raw_energy >= 0.3
            && (energy_jump >= 0.12 || bass_jump >= 0.12);
        explicit_evidence || transient_evidence || section_evidence
    }

    fn desired_phase(&self, now_seconds: f64) -> (MusicStructurePhase, f64, f64) {
        let window_seconds = if self.uses_rolling_energy {
            NATIVE_BUILDUP_WINDOW_SECONDS
        } else {
            BUILDUP_WINDOW_SECONDS
        };
        let mut recent = [Sample::default(); HISTORY_MAX_SAMPLES];
        let count = self.copy_recent(
            now_seconds,
            window_seconds,
            self.uses_rolling_energy,
            &mut recent,
        );
        let mut rise = 0.0;
        let mut slope = 0.0;
        let min_span = if self.uses_rolling_energy {
            NATIVE_BUILDUP_MIN_SPAN_SECONDS
        } else {
            BUILDUP_MIN_SPAN_SECONDS
        };
        if count >= 3 && recent[count - 1].timestamp - recent[0].timestamp >= min_span {
            let span = recent[count - 1].timestamp - recent[0].timestamp;
            let earlier_end = recent[0].timestamp + span * 0.3;
            let later_start = recent[0].timestamp + span * 0.7;
            let mut earlier = [Sample::default(); HISTORY_MAX_SAMPLES];
            let mut later = [Sample::default(); HISTORY_MAX_SAMPLES];
            let mut earlier_count = 0;
            let mut later_count = 0;
            for sample in recent[..count].iter().copied() {
                if sample.timestamp <= earlier_end {
                    earlier[earlier_count] = sample;
                    earlier_count += 1;
                }
                if sample.timestamp >= later_start {
                    later[later_count] = sample;
                    later_count += 1;
                }
            }
            if earlier_count > 0 && later_count > 0 {
                let baseline = median_samples(&earlier[..earlier_count], |sample| sample.energy);
                let later_energy = median_samples(&later[..later_count], |sample| sample.energy);
                let later_at = median_samples(&later[..later_count], |sample| sample.timestamp);
                let earlier_at =
                    median_samples(&earlier[..earlier_count], |sample| sample.timestamp);
                let duration = (later_at - earlier_at).max(0.001);
                rise = (later_energy - baseline).max(0.0);
                slope = rise / duration;
            }
        }

        let mut breakdown_threshold = BREAKDOWN_ENERGY;
        if self.uses_rolling_energy && self.groove_reference_energy > 0.0 {
            breakdown_threshold =
                breakdown_threshold.max(0.16_f64.min(self.groove_reference_energy * 0.60));
        }
        if self.uses_rolling_energy
            && self.phase == MusicStructurePhase::Buildup
            && self.smoothed_energy > 0.06
        {
            return (MusicStructurePhase::Buildup, rise, slope);
        }
        if self.uses_rolling_energy
            && self.smoothed_energy > 0.06
            && rise >= NATIVE_BUILDUP_MIN_RISE
            && slope >= NATIVE_BUILDUP_MIN_SLOPE
            && (self.phase == MusicStructurePhase::Breakdown
                || (self.phase == MusicStructurePhase::Groove
                    && now_seconds - self.phase_since >= NATIVE_GROOVE_BUILDUP_ARM_SECONDS))
        {
            return (MusicStructurePhase::Buildup, rise, slope);
        }
        if self.smoothed_energy < breakdown_threshold {
            return (MusicStructurePhase::Breakdown, rise, slope);
        }
        if !self.uses_rolling_energy
            && self.smoothed_energy >= GROOVE_ENERGY
            && rise >= BUILDUP_MIN_RISE
            && slope >= BUILDUP_MIN_SLOPE
        {
            return (MusicStructurePhase::Buildup, rise, slope);
        }
        (MusicStructurePhase::Groove, rise, slope)
    }

    fn copy_recent(
        &self,
        now_seconds: f64,
        window_seconds: f64,
        after_phase_start: bool,
        output: &mut [Sample; HISTORY_MAX_SAMPLES],
    ) -> usize {
        let mut count = 0;
        for sample in self.history.iter().copied() {
            if now_seconds - sample.timestamp <= window_seconds
                && (!after_phase_start || sample.timestamp >= self.phase_since)
            {
                output[count] = sample;
                count += 1;
            }
        }
        count
    }

    fn update_candidate(
        &mut self,
        desired: MusicStructurePhase,
        now_seconds: f64,
        confirm_seconds: f64,
    ) -> bool {
        if desired == self.phase {
            self.candidate_phase = None;
            return false;
        }
        if Some(desired) != self.candidate_phase {
            self.candidate_phase = Some(desired);
            self.candidate_since = now_seconds;
            return confirm_seconds == 0.0;
        }
        if now_seconds - self.candidate_since >= confirm_seconds {
            self.candidate_phase = None;
            return true;
        }
        false
    }

    fn transition(&mut self, phase: MusicStructurePhase, now_seconds: f64) {
        if phase == self.phase {
            return;
        }
        self.phase = phase;
        self.phase_since = now_seconds;
        self.candidate_phase = None;
        self.candidate_since = now_seconds;
        self.buildup_start_energy = if phase == MusicStructurePhase::Buildup {
            self.buildup_reference_energy(now_seconds)
        } else {
            0.0
        };
    }

    fn buildup_reference_energy(&self, now_seconds: f64) -> f64 {
        let window_seconds = if self.uses_rolling_energy {
            NATIVE_BUILDUP_WINDOW_SECONDS
        } else {
            BUILDUP_WINDOW_SECONDS
        };
        let mut recent = [Sample::default(); HISTORY_MAX_SAMPLES];
        let count = self.copy_recent(now_seconds, window_seconds, false, &mut recent);
        if count < 3 {
            return self.smoothed_energy;
        }
        let span = recent[count - 1].timestamp - recent[0].timestamp;
        let early_end = recent[0].timestamp + span * 0.3;
        let mut early = [Sample::default(); HISTORY_MAX_SAMPLES];
        let mut early_count = 0;
        for sample in recent[..count].iter().copied() {
            if sample.timestamp <= early_end {
                early[early_count] = sample;
                early_count += 1;
            }
        }
        if early_count == 0 {
            self.smoothed_energy
        } else {
            median_samples(&early[..early_count], |sample| sample.energy)
        }
    }

    fn state_with_drop(&self, drop_event: bool) -> MusicStructureState {
        MusicStructureState {
            phase: self.phase,
            confidence: clamp01(self.confidence),
            buildup_progress: clamp01(self.buildup_progress),
            drop_event,
        }
    }
}

fn feature_values(features: &MusicFeatures) -> (f64, f64, f64, f64, bool) {
    let instantaneous_energy = finite_clamp(features.energy as f64)
        .max(finite_clamp(features.volume as f64))
        .max(finite_clamp(features.energy_level as f64));
    let rolling_energy = finite_clamp(features.rolling_loudness as f64);
    let energy = if rolling_energy <= 0.003 {
        instantaneous_energy.max(rolling_energy)
    } else {
        rolling_energy
    };
    let bass = finite_clamp(features.bass as f64).max(finite_clamp(features.bass_energy as f64));
    let onset = finite_clamp(features.onset_strength as f64)
        .max(finite_clamp(features.spectral_flux as f64))
        .max(if features.beat {
            finite_clamp(features.beat_strength as f64)
        } else {
            0.0
        });
    let silent = features.silence || energy <= 0.003;
    (
        if silent { 0.0 } else { energy },
        instantaneous_energy,
        bass,
        onset,
        silent,
    )
}

fn finite_clamp(value: f64) -> f64 {
    if value.is_finite() {
        clamp01(value)
    } else {
        0.0
    }
}

fn clamp01(value: f64) -> f64 {
    value.clamp(0.0, 1.0)
}

fn median_samples(samples: &[Sample], get_value: impl Fn(Sample) -> f64) -> f64 {
    let mut values = [0.0_f64; HISTORY_MAX_SAMPLES];
    let len = samples.len();
    for (target, sample) in values.iter_mut().zip(samples.iter().copied()) {
        *target = get_value(sample);
    }
    values[..len].sort_by(f64::total_cmp);
    if len % 2 == 0 {
        (values[len / 2 - 1] + values[len / 2]) / 2.0
    } else {
        values[len / 2]
    }
}
