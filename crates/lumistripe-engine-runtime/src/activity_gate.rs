//! Music activity gating for Rust-owned Dynamic playback.

use lumistripe_engine_protocol::{RenderMusicActivityConfig, RenderSelectorFeatures};
use std::collections::VecDeque;
use std::time::{Duration, Instant};

const MAX_ACTIVITY_SAMPLES: usize = 131_072;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MusicGateState {
    Idle,
    Candidate,
    Music,
}

impl MusicGateState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Candidate => "candidate",
            Self::Music => "music",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ActivityUpdate {
    pub state: MusicGateState,
    pub activated: bool,
    pub deactivated: bool,
}

#[derive(Clone, Debug)]
pub struct MusicActivityTracker {
    config: RenderMusicActivityConfig,
    active: bool,
    inactive_frames: u32,
    energy: f64,
    onset: f64,
    beat_density: f64,
    brightness: f64,
    candidate_since: Option<Instant>,
    beat_samples: VecDeque<(Instant, bool)>,
    beat_count: usize,
}

impl MusicActivityTracker {
    pub fn new(config: RenderMusicActivityConfig) -> Self {
        Self {
            config,
            active: false,
            inactive_frames: 0,
            energy: 0.0,
            onset: 0.0,
            beat_density: 0.0,
            brightness: 0.0,
            candidate_since: None,
            beat_samples: VecDeque::with_capacity(128),
            beat_count: 0,
        }
    }

    pub fn state(&self) -> MusicGateState {
        if self.active {
            MusicGateState::Music
        } else if self.candidate_since.is_some() {
            MusicGateState::Candidate
        } else {
            MusicGateState::Idle
        }
    }

    pub fn update(&mut self, features: RenderSelectorFeatures, now: Instant) -> ActivityUpdate {
        let was_active = self.active;
        let config = self.config;
        self.energy = smooth(
            self.energy,
            features.energy,
            config.feature_attack,
            config.feature_release,
        );
        self.onset = smooth(
            self.onset,
            features.onset_strength,
            config.feature_attack,
            config.feature_release,
        );
        if self
            .beat_samples
            .back()
            .is_some_and(|(last_time, _)| now < *last_time)
        {
            self.beat_samples.clear();
            self.beat_count = 0;
        }
        self.beat_samples.push_back((now, features.beat));
        if features.beat {
            self.beat_count = self.beat_count.saturating_add(1);
        }
        let cutoff = now
            .checked_sub(Duration::from_nanos(config.beat_density_window_ns))
            .unwrap_or(now);
        while self
            .beat_samples
            .front()
            .is_some_and(|(sample_time, _)| *sample_time < cutoff)
        {
            if let Some((_, beat)) = self.beat_samples.pop_front()
                && beat
            {
                self.beat_count = self.beat_count.saturating_sub(1);
            }
        }
        while self.beat_samples.len() > MAX_ACTIVITY_SAMPLES {
            if let Some((_, beat)) = self.beat_samples.pop_front()
                && beat
            {
                self.beat_count = self.beat_count.saturating_sub(1);
            }
        }
        self.beat_density = if self.beat_samples.is_empty() {
            0.0
        } else {
            self.beat_count as f64 / self.beat_samples.len() as f64
        };
        self.brightness = smooth(self.brightness, features.brightness, 0.2, 0.1);

        let threshold_scale = if self.active {
            1.0
        } else {
            config.activation_hysteresis_ratio
        };
        let signal = self.energy >= config.energy_threshold * threshold_scale;
        let rhythmic = self.beat_density >= config.beat_density_threshold * threshold_scale
            && self.onset >= config.onset_threshold * threshold_scale;
        let spectral_floor = features.mid_energy * config.spectral_balance_ratio;
        let broadband = features.bass_energy >= config.energy_threshold * threshold_scale
            && features.treble_energy >= config.brightness_threshold * threshold_scale
            && features.bass_energy.min(features.treble_energy) >= spectral_floor;
        let detected = !features.silence && signal && (rhythmic || broadband);

        if self.active {
            self.candidate_since = None;
            if detected {
                self.inactive_frames = 0;
            } else {
                self.inactive_frames = self.inactive_frames.saturating_add(1);
                if self.inactive_frames >= config.idle_enter_frames {
                    self.active = false;
                }
            }
        } else {
            self.inactive_frames = 0;
            if !detected {
                self.candidate_since = None;
            } else {
                let since = *self.candidate_since.get_or_insert(now);
                if now.saturating_duration_since(since)
                    >= Duration::from_nanos(config.activation_delay_ns)
                {
                    self.active = true;
                    self.candidate_since = None;
                }
            }
        }

        ActivityUpdate {
            state: self.state(),
            activated: !was_active && self.active,
            deactivated: was_active && !self.active,
        }
    }
}

fn smooth(current: f64, target: f64, attack: f64, release: f64) -> f64 {
    let rate = if target > current { attack } else { release };
    current + (target - current) * rate
}

#[cfg(test)]
mod tests {
    use super::{MusicActivityTracker, MusicGateState};
    use lumistripe_engine_protocol::{RenderMusicActivityConfig, RenderSelectorFeatures};
    use std::time::{Duration, Instant};

    fn music_features() -> RenderSelectorFeatures {
        RenderSelectorFeatures {
            energy: 0.9,
            onset_strength: 0.5,
            beat: true,
            bass_energy: 0.8,
            mid_energy: 0.4,
            treble_energy: 0.8,
            silence: false,
            ..RenderSelectorFeatures::default()
        }
    }

    #[test]
    fn activation_waits_for_candidate_delay_and_releases_after_idle_frames() {
        let config = RenderMusicActivityConfig {
            idle_enter_frames: 3,
            activation_delay_ns: 100_000_000,
            ..RenderMusicActivityConfig::default()
        };
        let mut tracker = MusicActivityTracker::new(config);
        let start = Instant::now();
        assert_eq!(
            tracker.update(music_features(), start).state,
            MusicGateState::Candidate
        );
        assert_eq!(
            tracker
                .update(music_features(), start + Duration::from_millis(50))
                .state,
            MusicGateState::Candidate
        );
        let activated = tracker.update(music_features(), start + Duration::from_millis(100));
        assert_eq!(activated.state, MusicGateState::Music);
        assert!(activated.activated);
        assert_eq!(
            tracker
                .update(
                    RenderSelectorFeatures::default(),
                    start + Duration::from_millis(120)
                )
                .state,
            MusicGateState::Music
        );
        assert_eq!(
            tracker
                .update(
                    RenderSelectorFeatures::default(),
                    start + Duration::from_millis(140)
                )
                .state,
            MusicGateState::Music
        );
        let released = tracker.update(
            RenderSelectorFeatures::default(),
            start + Duration::from_millis(160),
        );
        assert_eq!(released.state, MusicGateState::Idle);
        assert!(released.deactivated);
    }

    #[test]
    fn activation_hysteresis_is_stronger_than_the_active_hold_threshold() {
        let config = RenderMusicActivityConfig {
            activation_delay_ns: 0,
            energy_threshold: 0.2,
            onset_threshold: 0.0,
            beat_density_threshold: 0.0,
            activation_hysteresis_ratio: 1.5,
            ..RenderMusicActivityConfig::default()
        };
        let mut tracker = MusicActivityTracker::new(config);
        let start = Instant::now();
        let features = RenderSelectorFeatures {
            energy: 1.0,
            onset_strength: 1.0,
            bass_energy: 0.0,
            treble_energy: 0.0,
            silence: false,
            ..RenderSelectorFeatures::default()
        };
        for index in 0..8 {
            let update = tracker.update(features, start + Duration::from_millis(index * 10));
            if update.state == MusicGateState::Music {
                break;
            }
        }
        assert_eq!(tracker.state(), MusicGateState::Music);
        tracker = MusicActivityTracker::new(config);
        let below_activation_threshold = RenderSelectorFeatures {
            energy: 0.2,
            onset_strength: 1.0,
            bass_energy: 0.0,
            treble_energy: 0.0,
            silence: false,
            ..RenderSelectorFeatures::default()
        };
        assert_eq!(
            tracker
                .update(
                    below_activation_threshold,
                    start + Duration::from_millis(90)
                )
                .state,
            MusicGateState::Idle
        );
    }
}
