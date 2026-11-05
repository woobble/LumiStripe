use lumistripe_engine_protocol::{RenderBeatClockState, RenderBeatEvent};
use std::collections::VecDeque;

const MIN_BPM: f64 = 60.0;
const MAX_BPM: f64 = 200.0;
const TEMPO_GAIN: f64 = 0.12;
const PHASE_GAIN: f64 = 0.35;
const MIN_BEAT_INTERVAL_SECONDS: f64 = 0.18;
const MAX_CONTIGUOUS_BEAT_GAP_SECONDS: f64 = 2.0;
const NS_PER_SECOND: f64 = 1_000_000_000.0;

#[derive(Clone, Debug)]
pub(crate) struct BeatClockTracker {
    period: f64,
    last_beat_at: Option<f64>,
    last_event_at: Option<f64>,
    last_sequence: u64,
    beat_index: u64,
    interval_errors: VecDeque<f64>,
    candidate_period: Option<f64>,
    candidate_count: u8,
}

impl Default for BeatClockTracker {
    fn default() -> Self {
        Self {
            period: 0.5,
            last_beat_at: None,
            last_event_at: None,
            last_sequence: 0,
            beat_index: 0,
            interval_errors: VecDeque::with_capacity(12),
            candidate_period: None,
            candidate_count: 0,
        }
    }
}

impl BeatClockTracker {
    pub(crate) fn update(
        &mut self,
        events: &[RenderBeatEvent],
        now_ns: u64,
        fallback_bpm: Option<f64>,
    ) -> RenderBeatClockState {
        let mut next_sequence = self.last_sequence;
        for event in events {
            if event.sequence <= next_sequence {
                continue;
            }
            next_sequence = event.sequence;
        }
        for event in events {
            if event.sequence <= self.last_sequence {
                continue;
            }
            self.last_sequence = event.sequence;
            self.observe(event.timestamp_ns as f64 / NS_PER_SECOND, fallback_bpm);
        }
        self.state(now_ns)
    }

    pub(crate) fn state(&self, now_ns: u64) -> RenderBeatClockState {
        let now = now_ns as f64 / NS_PER_SECOND;
        let (phase, beat_index, next_beat_at, last_beat_at, confidence) =
            if let Some(last) = self.last_beat_at {
                let elapsed = (now - last).max(0.0);
                let beats_since = (elapsed / self.period).floor() as u64;
                let phase = (elapsed / self.period) % 1.0;
                let current_beat_at = last + beats_since as f64 * self.period;
                let next_beat = current_beat_at + self.period;
                let stability = if self.interval_errors.is_empty() {
                    1.0
                } else {
                    (-4.0 * self.interval_errors.iter().sum::<f64>()
                        / self.interval_errors.len() as f64)
                        .exp()
                };
                let lock = (self.interval_errors.len() as f64 / 5.0).min(1.0);
                let stale_seconds = self
                    .last_event_at
                    .map_or(0.0, |event| (now - event - 2.0 * self.period).max(0.0));
                let freshness = (-stale_seconds / (3.0 * self.period)).exp();
                let confidence = (lock * stability * freshness).clamp(0.0, 1.0);
                (
                    phase,
                    self.beat_index.saturating_add(beats_since),
                    Some(next_beat),
                    Some(current_beat_at),
                    confidence,
                )
            } else {
                (0.0, 0, None, None, 0.0)
            };
        RenderBeatClockState {
            sampled_at_ns: now_ns,
            bpm: 60.0 / self.period,
            phase,
            confidence,
            beat_index,
            next_beat_at_ns: next_beat_at.map(seconds_to_ns),
            last_beat_at_ns: last_beat_at.map(seconds_to_ns),
            last_event_sequence: self.last_sequence,
        }
    }

    fn observe(&mut self, timestamp: f64, fallback_bpm: Option<f64>) {
        let Some(last_event) = self.last_event_at else {
            if let Some(bpm) = normalize_bpm(fallback_bpm) {
                self.period = 60.0 / bpm;
            }
            self.last_event_at = Some(timestamp);
            self.last_beat_at = Some(timestamp);
            self.beat_index = self.beat_index.saturating_add(1);
            return;
        };
        if timestamp <= last_event {
            return;
        }
        let interval = timestamp - last_event;
        self.last_event_at = Some(timestamp);
        if interval > MAX_CONTIGUOUS_BEAT_GAP_SECONDS {
            if let Some(previous_beat_at) = self.last_beat_at {
                let elapsed_beats = ((timestamp - previous_beat_at) / self.period) as u64;
                self.beat_index = self.beat_index.saturating_add(elapsed_beats.max(1));
            }
            self.last_beat_at = Some(timestamp);
            self.interval_errors.clear();
            self.candidate_period = None;
            self.candidate_count = 0;
            return;
        }
        if interval < MIN_BEAT_INTERVAL_SECONDS {
            return;
        }

        let current_period = self.period;
        let candidate = choose_candidate(interval, fallback_bpm, self.period, self.beat_index);
        if self.interval_errors.is_empty() {
            if let Some(candidate) = candidate {
                self.period = candidate;
                if candidate > interval * 1.25 {
                    self.push_error(0.0);
                    return;
                }
            }
            self.last_beat_at = Some(timestamp);
            let elapsed_beats = round_positive(interval / self.period).max(1);
            self.beat_index = self.beat_index.saturating_add(elapsed_beats);
            self.push_error(0.0);
            return;
        }

        let ratio = interval / current_period;
        if ratio < 0.68 {
            let residual = (ratio - ratio.round())
                .abs()
                .min((ratio * 2.0 - (ratio * 2.0).round()).abs());
            self.push_error(residual.min(1.0));
            return;
        }

        let beat_multiple = round_positive(ratio).clamp(1, 4);
        let measured_period = interval / beat_multiple as f64;
        if candidate.is_some_and(|period| (period - current_period).abs() / current_period > 0.15) {
            let candidate = candidate.expect("checked above");
            if self
                .candidate_period
                .is_some_and(|previous| (candidate - previous).abs() / candidate < 0.06)
            {
                self.candidate_count = self.candidate_count.saturating_add(1);
            } else {
                self.candidate_period = Some(candidate);
                self.candidate_count = 1;
            }
            if self.candidate_count >= 3 {
                self.period = candidate;
                self.candidate_period = None;
                self.candidate_count = 0;
            }
        } else {
            self.candidate_period = None;
            self.candidate_count = 0;
            let target_period = candidate.unwrap_or(measured_period);
            self.period += (target_period - self.period) * TEMPO_GAIN;
        }

        let predicted = self.last_beat_at.unwrap_or(timestamp) + beat_multiple as f64 * self.period;
        let phase_error = timestamp - predicted;
        self.push_error((phase_error.abs() / self.period).min(1.0));
        self.last_beat_at = Some(predicted + phase_error * PHASE_GAIN);
        self.beat_index = self.beat_index.saturating_add(beat_multiple);
    }

    fn push_error(&mut self, value: f64) {
        if self.interval_errors.len() == 12 {
            self.interval_errors.pop_front();
        }
        self.interval_errors.push_back(value);
    }
}

fn choose_candidate(
    interval: f64,
    fallback_bpm: Option<f64>,
    current_period: f64,
    beat_index: u64,
) -> Option<f64> {
    let low_period = 60.0 / MAX_BPM;
    let high_period = 60.0 / MIN_BPM;
    let mut candidates = Vec::with_capacity(8);
    for multiple in [0.5, 1.0, 2.0, 3.0, 4.0] {
        let candidate = interval * multiple;
        if (low_period..=high_period).contains(&candidate) {
            candidates.push(candidate);
        }
    }
    for multiple in [2.0, 3.0, 4.0] {
        let candidate = interval / multiple;
        if (low_period..=high_period).contains(&candidate) {
            candidates.push(candidate);
        }
    }
    if candidates.is_empty() {
        return None;
    }
    let reference = if beat_index < 4 {
        normalize_bpm(fallback_bpm).map_or(current_period, |bpm| 60.0 / bpm)
    } else {
        current_period
    };
    candidates.into_iter().min_by(|left, right| {
        (left / reference)
            .ln()
            .abs()
            .total_cmp(&(right / reference).ln().abs())
    })
}

fn normalize_bpm(value: Option<f64>) -> Option<f64> {
    let mut bpm = value?;
    if !bpm.is_finite() || bpm <= 0.0 {
        return None;
    }
    while bpm < MIN_BPM {
        bpm *= 2.0;
    }
    while bpm > MAX_BPM {
        bpm *= 0.5;
    }
    Some(bpm)
}

fn round_positive(value: f64) -> u64 {
    let floor = value.floor();
    let fraction = value - floor;
    let rounded = if fraction < 0.5 {
        floor
    } else if fraction > 0.5 || (floor as u64) % 2 == 1 {
        floor + 1.0
    } else {
        floor
    };
    rounded.min(u64::MAX as f64) as u64
}

fn seconds_to_ns(seconds: f64) -> u64 {
    (seconds.max(0.0) * NS_PER_SECOND)
        .round()
        .min(u64::MAX as f64) as u64
}

#[cfg(test)]
mod tests {
    use super::BeatClockTracker;
    use lumistripe_engine_protocol::RenderBeatEvent;

    fn event(sequence: u64, timestamp: f64) -> RenderBeatEvent {
        RenderBeatEvent {
            sequence,
            timestamp_ns: (timestamp * 1_000_000_000.0).round() as u64,
            strength: 0.0,
        }
    }

    #[test]
    fn locks_to_synthetic_128_bpm_onsets() {
        let mut tracker = BeatClockTracker::default();
        let period = 60.0 / 128.0;
        let mut state = tracker.state(0);
        for index in 0..12 {
            state = tracker.update(
                &[event(index + 1, index as f64 * period)],
                ((index as f64 * period + 0.02) * 1_000_000_000.0) as u64,
                Some(120.0),
            );
        }
        assert!((state.bpm - 128.0).abs() < 1.0);
        assert!(state.confidence > 0.8);
        assert!((state.phase - 0.04).abs() < 0.04);
        assert!((state.next_beat_at_ns.unwrap() as f64 / 1e9 - 12.0 * period).abs() < 0.02);
    }

    #[test]
    fn ignores_duplicate_sequences_and_out_of_order_timestamps() {
        let mut tracker = BeatClockTracker::default();
        tracker.update(&[event(1, 1.0)], 1_000_000_000, None);
        tracker.update(&[event(2, 0.5)], 1_000_000_000, None);
        let state = tracker.update(&[event(3, 1.5), event(3, 1.75)], 1_500_000_000, None);
        assert_eq!(state.beat_index, 2);
        assert_eq!(state.last_event_sequence, 3);
        assert_eq!(state.next_beat_at_ns, Some(2_000_000_000));
    }

    #[test]
    fn ignores_repeated_half_beat_subdivisions_for_phase() {
        let mut tracker = BeatClockTracker::default();
        let period = 60.0 / 128.0;
        let events = (0..20)
            .map(|index| event(index + 1, index as f64 * period / 2.0))
            .collect::<Vec<_>>();
        let now_ns = ((events[19].timestamp_ns as f64 / 1e9 + 0.01) * 1e9) as u64;
        let state = tracker.update(&events, now_ns, Some(128.0));
        assert!((state.bpm - 128.0).abs() < 1.0);
        assert!(state.confidence > 0.7);
        assert!((state.phase - 0.52).abs() < 0.04);
    }

    #[test]
    fn confidence_fades_and_clock_relocks_after_a_long_gap() {
        let mut tracker = BeatClockTracker::default();
        let period = 60.0 / 128.0;
        let mut state = tracker.state(0);
        for index in 0..12 {
            state = tracker.update(
                &[event(index + 1, index as f64 * period)],
                (index as f64 * period * 1e9) as u64,
                Some(128.0),
            );
        }
        let stale = tracker.state(((11.0 * period + 5.0 * period) * 1e9) as u64);
        assert!(state.confidence > 0.8);
        assert!(stale.confidence < 0.65);

        let recovery_at = 11.0 * period + 6.0;
        let recovered = tracker.update(
            &[event(13, recovery_at)],
            ((recovery_at + 0.01) * 1e9) as u64,
            Some(128.0),
        );
        assert!(recovered.beat_index >= state.beat_index + 12);
        assert_eq!(recovered.confidence, 0.0);
    }
}
