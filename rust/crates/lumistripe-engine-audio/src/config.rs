use serde::{Deserialize, Serialize};

pub const AUDIO_WINDOW_SIZE: usize = 2048;
pub const AUDIO_HOP_SIZE: usize = 512;
pub const AUDIO_SAMPLE_RATE: f32 = 44_100.0;
pub const AUDIO_BANDS: usize = 8;

/// Flat wire-compatible form of the Python ``AudioConfig`` values.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct AudioConfig {
    pub microphone_enabled: bool,
    pub input_noise_floor: f32,
    pub high_pass_hz: f32,
    pub noise_floor: f32,
    pub rms_attack: f32,
    pub rms_release: f32,
    pub band_attack: f32,
    pub band_release: f32,
    pub beat_release: f32,
    pub smoothing_enabled: bool,
    pub target_level: f32,
    pub min_gain: f32,
    pub max_gain: f32,
    pub adapt_attack: f32,
    pub adapt_release: f32,
    pub music_threshold: f32,
    pub music_max_gain: f32,
    pub silence_floor: f32,
    pub normalization_enabled: bool,
    pub dc_block_enabled: bool,
    pub drop_bass_threshold: f32,
    pub drop_bass_delta_threshold: f32,
    pub drop_onset_threshold: f32,
    pub section_change_threshold: f32,
    pub section_onset_threshold: f32,
}

impl Default for AudioConfig {
    fn default() -> Self {
        Self {
            microphone_enabled: false,
            input_noise_floor: 0.003,
            high_pass_hz: 30.0,
            noise_floor: 0.015,
            rms_attack: 0.45,
            rms_release: 0.12,
            band_attack: 0.4,
            band_release: 0.1,
            beat_release: 0.18,
            smoothing_enabled: true,
            target_level: 0.36,
            min_gain: 0.35,
            max_gain: 5.0,
            adapt_attack: 0.18,
            adapt_release: 0.18,
            music_threshold: 0.015,
            music_max_gain: 4.5,
            silence_floor: 0.003,
            normalization_enabled: true,
            dc_block_enabled: true,
            drop_bass_threshold: 0.45,
            drop_bass_delta_threshold: 0.16,
            drop_onset_threshold: 0.12,
            section_change_threshold: 0.42,
            section_onset_threshold: 0.08,
        }
    }
}

impl AudioConfig {
    pub fn raw() -> Self {
        Self {
            smoothing_enabled: false,
            normalization_enabled: false,
            dc_block_enabled: false,
            ..Self::default()
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        let values = [
            self.input_noise_floor,
            self.high_pass_hz,
            self.noise_floor,
            self.rms_attack,
            self.rms_release,
            self.band_attack,
            self.band_release,
            self.beat_release,
            self.target_level,
            self.min_gain,
            self.max_gain,
            self.adapt_attack,
            self.adapt_release,
            self.music_threshold,
            self.music_max_gain,
            self.silence_floor,
            self.drop_bass_threshold,
            self.drop_bass_delta_threshold,
            self.drop_onset_threshold,
            self.section_change_threshold,
            self.section_onset_threshold,
        ];
        if values.iter().any(|value| !value.is_finite()) {
            return Err("audio configuration values must be finite".to_owned());
        }
        if !(0.0..=0.5).contains(&self.input_noise_floor) {
            return Err("input_noise_floor must be between 0 and 0.5".to_owned());
        }
        if !(0.0..=100.0).contains(&self.high_pass_hz) {
            return Err("high_pass_hz must be between 0 and 100".to_owned());
        }
        Ok(())
    }
}
