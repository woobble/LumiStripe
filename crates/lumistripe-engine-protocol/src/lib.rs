//! Versioned control messages and compact binary frame-set packets.

use lumistripe_engine_audio::{
    AudioCaptureStats, AudioConfig, AudioFrame, AudioProcessorStats, InputAudioDevice,
    MusicFeatures, MusicStructureState, PcmIngressStats,
};
use lumistripe_engine_config::EngineConfig;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::fmt;

pub const PROTOCOL_VERSION: u16 = 4;
pub const PREVIOUS_PROTOCOL_VERSION: u16 = 3;
pub const LEGACY_PROTOCOL_VERSION: u16 = 1;
pub const MAX_CONTROL_MESSAGE_BYTES: usize = 1_048_576;
pub const MAX_RENDER_STRUCTURE_EVENTS: usize = 256;
pub const FRAME_MAGIC: [u8; 4] = *b"LSF2";
pub const FRAME_ACK_MAGIC: [u8; 4] = *b"LSA2";
pub const FRAME_HEADER_BYTES: usize = 20;
pub const FRAME_ACK_BYTES: usize = 24;
pub const FRAME_VERSION: u16 = 2;
pub const MAX_FRAME_BYTES: usize = 2 * 16_384 * 4;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Request {
    pub protocol_version: u16,
    pub request_id: String,
    pub command: RequestCommand,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "type", content = "data", rename_all = "snake_case")]
pub enum RequestCommand {
    Health,
    RenderCapabilities,
    Configure {
        config: EngineConfig,
    },
    Render {
        frame: RenderFrameRequest,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        scheduler: Option<RenderSchedulerConfig>,
    },
    SceneUpdate {
        update: RenderSceneUpdate,
    },
    AudioUpdate {
        update: RenderAudioUpdate,
    },
    AudioStart {
        selector: Option<String>,
        config: AudioConfig,
    },
    AudioStartDirect {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        fallback_selector: Option<String>,
        config: AudioConfig,
    },
    AudioConfigure {
        config: AudioConfig,
    },
    AudioStop,
    AudioDevices,
    AudioStatus,
    Shutdown,
}

/// Enables Rust-owned monotonic frame scheduling for an active render scene.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RenderSchedulerConfig {
    pub max_fps: u16,
}

impl RenderSchedulerConfig {
    pub const MIN_FPS: u16 = 1;
    pub const MAX_FPS: u16 = 240;

    pub fn is_valid(self) -> bool {
        (Self::MIN_FPS..=Self::MAX_FPS).contains(&self.max_fps)
    }
}

/// Reliable, ordered, pixel-free update of the active scheduled scene.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderSceneUpdate {
    pub revision: u64,
    pub timestamp_ns: u64,
    pub brightness: f64,
    #[serde(default, skip_serializing_if = "RenderPowerConfig::is_default")]
    pub power: RenderPowerConfig,
    pub outputs: Vec<RenderOutput>,
    pub scheduler: RenderSchedulerConfig,
}

/// Replaceable audio snapshot; one-shot events remain in the snapshot until acknowledged.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderAudioUpdate {
    pub sequence: u64,
    pub timestamp_ns: u64,
    pub audio: Option<RenderAudioInput>,
}

/// Pixel-free scene input for one direct Rust render/output iteration.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderFrameRequest {
    pub sequence: u64,
    pub timestamp_ns: u64,
    pub delta_ns: u64,
    pub brightness: f64,
    pub audio: Option<RenderAudioInput>,
    #[serde(default, skip_serializing_if = "RenderPowerConfig::is_default")]
    pub power: RenderPowerConfig,
    pub outputs: Vec<RenderOutput>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct RenderPowerConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub budget_watts: Option<f64>,
}

impl RenderPowerConfig {
    fn is_default(&self) -> bool {
        self == &Self::default()
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderAudioInput {
    pub rms: f64,
    pub bands: [f64; 8],
    pub beat: bool,
    pub beat_strength: f64,
    /// Monotonic capture timestamp for the feature snapshot, when available.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub feature_timestamp_ns: Option<u64>,
    /// Monotonic producer sequence for selector evaluation. Older clients may omit it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub feature_sequence: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fallback_bpm: Option<f64>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub beat_events: Vec<RenderBeatEvent>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub structure_events: Vec<RenderStructureEvent>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selector_features: Option<RenderSelectorFeatures>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub effect_features: Option<RenderEffectFeatures>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub beat_event_stream: bool,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct RenderEffectFeatures {
    #[serde(default)]
    pub beat_strength: f64,
    #[serde(default)]
    pub musical_impact: f64,
}

impl RenderEffectFeatures {
    pub fn is_valid(self) -> bool {
        self.beat_strength.is_finite()
            && (0.0..=1.0).contains(&self.beat_strength)
            && self.musical_impact.is_finite()
            && (0.0..=1.0).contains(&self.musical_impact)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderSelectorFeatures {
    pub energy_level: f64,
    pub energy: f64,
    pub volume: f64,
    pub brightness: f64,
    pub onset_strength: f64,
    pub bass_energy: f64,
    pub bass: f64,
    pub mid_energy: f64,
    pub treble_energy: f64,
    pub beat_confidence: f64,
    pub bpm: f64,
    pub beat: bool,
    pub silence: bool,
    pub drop_detected: bool,
    pub section_change: bool,
}

impl RenderSelectorFeatures {
    pub fn is_valid(self) -> bool {
        [
            self.energy_level,
            self.energy,
            self.volume,
            self.brightness,
            self.onset_strength,
            self.bass_energy,
            self.bass,
            self.mid_energy,
            self.treble_energy,
            self.beat_confidence,
            self.bpm,
        ]
        .into_iter()
        .all(f64::is_finite)
    }
}

impl Default for RenderSelectorFeatures {
    fn default() -> Self {
        Self {
            energy_level: 0.0,
            energy: 0.0,
            volume: 0.0,
            brightness: 0.0,
            onset_strength: 0.0,
            bass_energy: 0.0,
            bass: 0.0,
            mid_energy: 0.0,
            treble_energy: 0.0,
            beat_confidence: 0.0,
            bpm: 120.0,
            beat: false,
            silence: true,
            drop_detected: false,
            section_change: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderBeatEvent {
    pub sequence: u64,
    pub timestamp_ns: u64,
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    pub strength: f64,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderBeatClockState {
    pub sampled_at_ns: u64,
    pub bpm: f64,
    pub phase: f64,
    pub confidence: f64,
    pub beat_index: u64,
    pub next_beat_at_ns: Option<u64>,
    pub last_beat_at_ns: Option<u64>,
    #[serde(default, skip_serializing_if = "is_zero_u64")]
    pub last_event_sequence: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderOutput {
    pub animation: String,
    #[serde(default)]
    pub settings: Map<String, Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub effects: Vec<RenderEffect>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub music_structure: Option<RenderMusicStructure>,
    #[serde(default = "default_render_intensity", skip_serializing_if = "is_one")]
    pub base_intensity: f64,
    #[serde(
        default = "default_overlay_strength",
        skip_serializing_if = "is_default_overlay_strength"
    )]
    pub max_overlay_strength: f64,
    #[serde(default, skip_serializing_if = "is_zero_u64")]
    pub scene_revision: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scene_pixel_count: Option<usize>,
    #[serde(default, skip_serializing_if = "is_zero_usize")]
    pub scene_offset: usize,
    #[serde(default, skip_serializing_if = "is_false")]
    pub scene_resample: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub brightness: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub solid_color: Option<[u8; 3]>,
    #[serde(default = "default_true", skip_serializing_if = "is_true")]
    pub audio_enabled: bool,
    #[serde(default = "default_zone_count", skip_serializing_if = "is_one_zone")]
    pub zone_count: usize,
    #[serde(
        default = "default_zone_pattern",
        skip_serializing_if = "is_repeat_zone_pattern"
    )]
    pub zone_pattern: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transition: Option<RenderTransition>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cycling: Option<RenderCycling>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub blackout: bool,
    #[serde(default, skip_serializing_if = "is_false")]
    pub reversed: bool,
    #[serde(
        default = "default_color_correction",
        skip_serializing_if = "is_identity_correction"
    )]
    pub color_correction: [u8; 3],
    #[serde(
        default = "default_voltage_v",
        skip_serializing_if = "is_default_voltage_v"
    )]
    pub voltage_v: f64,
    #[serde(
        default = "default_full_white_current_a",
        skip_serializing_if = "is_default_full_white_current_a"
    )]
    pub full_white_current_a: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub power_limit_watts: Option<f64>,
}

impl Default for RenderOutput {
    fn default() -> Self {
        Self {
            animation: String::new(),
            settings: Map::new(),
            effects: Vec::new(),
            music_structure: None,
            base_intensity: default_render_intensity(),
            max_overlay_strength: default_overlay_strength(),
            scene_revision: 0,
            scene_pixel_count: None,
            scene_offset: 0,
            scene_resample: false,
            brightness: None,
            solid_color: None,
            audio_enabled: true,
            zone_count: default_zone_count(),
            zone_pattern: default_zone_pattern(),
            transition: None,
            cycling: None,
            blackout: false,
            reversed: false,
            color_correction: default_color_correction(),
            voltage_v: default_voltage_v(),
            full_white_current_a: default_full_white_current_a(),
            power_limit_watts: None,
        }
    }
}

/// Animation choices and timing for one independently scheduled output.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderCycling {
    pub order: RenderCycleOrder,
    pub timing: RenderCycleTiming,
    pub interval_ns: u64,
    pub seed: Option<u64>,
    pub choices: Vec<RenderAnimationChoice>,
    pub transition_duration_ns: u64,
    pub transition_mode: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selector: Option<RenderDynamicSelector>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dynamic_idle: Option<RenderDynamicIdle>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sequence: Option<RenderAnimationSequence>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub effect_scheduler: Option<RenderEffectSchedulerConfig>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderMusicStructurePhase {
    Idle,
    Breakdown,
    Groove,
    Buildup,
    Drop,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderMusicStructure {
    pub phase: RenderMusicStructurePhase,
    pub confidence: f64,
    pub buildup_progress: f64,
    #[serde(default)]
    pub drop_event: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderStructureEvent {
    pub output_index: usize,
    pub sequence: u64,
    pub timestamp_ns: u64,
    pub phase: RenderMusicStructurePhase,
    pub confidence: f64,
    pub buildup_progress: f64,
    #[serde(default)]
    pub drop_event: bool,
    #[serde(default)]
    pub section_change: bool,
    #[serde(default, skip_serializing_if = "is_false")]
    pub phase_change: bool,
}

impl RenderStructureEvent {
    pub fn is_valid(self) -> bool {
        self.sequence > 0
            && (self.drop_event || self.section_change || self.phase_change)
            && self.confidence.is_finite()
            && (0.0..=1.0).contains(&self.confidence)
            && self.buildup_progress.is_finite()
            && (0.0..=1.0).contains(&self.buildup_progress)
    }
}

impl RenderMusicStructure {
    pub fn is_valid(self) -> bool {
        self.confidence.is_finite()
            && (0.0..=1.0).contains(&self.confidence)
            && self.buildup_progress.is_finite()
            && (0.0..=1.0).contains(&self.buildup_progress)
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderEffectChoice {
    pub name: String,
    #[serde(default)]
    pub settings: Map<String, Value>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderEffectSchedulerConfig {
    pub beat_confidence_threshold: f64,
    pub onset_threshold: f64,
    pub rhythmic_cooldown_ns: u64,
    pub accent_cooldown_ns: u64,
    pub max_active_effects: usize,
    pub max_overlay_strength: f64,
    pub subdivisions_per_beat: usize,
    pub clock_confidence_threshold: f64,
    pub seed: Option<u64>,
    pub sync_offset_ns: i64,
    pub dynamic_response: f64,
    pub choices: Vec<RenderEffectChoice>,
}

impl RenderEffectSchedulerConfig {
    pub fn is_valid(&self) -> bool {
        self.beat_confidence_threshold.is_finite()
            && (0.0..=1.0).contains(&self.beat_confidence_threshold)
            && self.onset_threshold.is_finite()
            && (0.0..=1.0).contains(&self.onset_threshold)
            && matches!(self.max_active_effects, 1 | 2)
            && self.max_overlay_strength.is_finite()
            && (0.0 < self.max_overlay_strength && self.max_overlay_strength <= 1.0)
            && matches!(self.subdivisions_per_beat, 1 | 2 | 4)
            && self.clock_confidence_threshold.is_finite()
            && (0.0..=1.0).contains(&self.clock_confidence_threshold)
            && self.dynamic_response.is_finite()
            && (0.0..=1.0).contains(&self.dynamic_response)
            && !self.choices.is_empty()
            && self.choices.len() <= 19
            && self
                .choices
                .iter()
                .all(|choice| !choice.name.trim().is_empty())
            && {
                let mut names = std::collections::BTreeSet::new();
                self.choices
                    .iter()
                    .all(|choice| names.insert(choice.name.as_str()))
            }
    }
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct RenderEffectPlaybackState {
    pub name: String,
    pub category: String,
    pub strength: f64,
    pub elapsed_ns: u64,
    pub remaining_ns: u64,
    pub progress: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderEffectSchedulerState {
    #[serde(default)]
    pub active: Vec<RenderEffectPlaybackState>,
    pub rhythmic_cooldown_remaining_ns: u64,
    pub accent_cooldown_remaining_ns: u64,
    pub rhythmic_result: String,
    pub accent_result: String,
    pub drop_accent_status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub drop_accent_reason: Option<String>,
    pub late_ticks_dropped: u64,
    pub late_ticks_coalesced: u64,
    pub beat_lateness_p95_ns: u64,
}

/// One animation's current Dynamic selector score and contributing factors.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderSelectorScore {
    pub animation: String,
    pub score: f64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub reasons: Vec<String>,
}

/// Latest Dynamic selector decision and timing state for one output.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderSelectorDiagnostics {
    pub output_index: usize,
    pub current_animation: String,
    pub candidate_animation: Option<String>,
    pub selected_animation: String,
    pub should_switch: bool,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub scores: Vec<RenderSelectorScore>,
    pub elapsed_ns: u64,
    pub min_duration_remaining_ns: u64,
    pub max_duration_remaining_ns: u64,
    pub switch_cooldown_remaining_ns: u64,
    pub drop_cooldown_remaining_ns: u64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub recent_names: Vec<String>,
}

impl Default for RenderEffectSchedulerState {
    fn default() -> Self {
        Self {
            active: Vec::new(),
            rhythmic_cooldown_remaining_ns: 0,
            accent_cooldown_remaining_ns: 0,
            rhythmic_result: "not-evaluated".to_owned(),
            accent_result: "not-evaluated".to_owned(),
            drop_accent_status: "idle".to_owned(),
            drop_accent_reason: None,
            late_ticks_dropped: 0,
            late_ticks_coalesced: 0,
            beat_lateness_p95_ns: 0,
        }
    }
}

/// One Dynamic animation and the number of estimated 4/4 bars it occupies.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RenderAnimationSequenceStep {
    pub animation: String,
    pub duration_bars: u32,
}

/// Saved Dynamic sequence scheduled by Rust against the per-output beat clock.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RenderAnimationSequence {
    pub id: String,
    pub repeat: bool,
    pub steps: Vec<RenderAnimationSequenceStep>,
}

impl RenderAnimationSequence {
    pub fn is_valid(&self) -> bool {
        !self.id.trim().is_empty()
            && !self.steps.is_empty()
            && self.steps.len() <= 32
            && self.steps.iter().all(|step| {
                !step.animation.trim().is_empty() && [1, 2, 4, 8].contains(&step.duration_bars)
            })
    }
}

/// Sequence state mirrored into the versioned health response.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct RenderSequencePlaybackState {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sequence_id: Option<String>,
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub waiting_for_clock: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current_step_index: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub animation: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub remaining_bars: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub next_transition_at_ns: Option<u64>,
}

/// Scoring and switch timing for Rust-owned Dynamic playback selection.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderDynamicSelector {
    pub min_duration_ns: u64,
    pub max_duration_ns: u64,
    pub switch_cooldown_ns: u64,
    pub drop_cooldown_ns: u64,
    pub randomness: f64,
    pub history_size: usize,
    pub seed: Option<u64>,
    pub switch_margin: f64,
    #[serde(default)]
    pub switch_quantization: RenderSwitchQuantization,
    #[serde(default = "default_bars_per_phrase")]
    pub bars_per_phrase: usize,
    #[serde(default = "default_clock_confidence_threshold")]
    pub clock_confidence_threshold: f64,
    #[serde(default = "default_subdivisions_per_beat")]
    pub subdivisions_per_beat: usize,
    #[serde(default)]
    pub sync_offset_ns: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub activity: Option<RenderMusicActivityConfig>,
}

impl RenderDynamicSelector {
    pub fn is_valid(self) -> bool {
        self.randomness.is_finite()
            && self.randomness >= 0.0
            && self.switch_margin.is_finite()
            && self.switch_margin >= 0.0
            && self.history_size <= 256
            && [4, 8, 16].contains(&self.bars_per_phrase)
            && self.clock_confidence_threshold.is_finite()
            && (0.0..=1.0).contains(&self.clock_confidence_threshold)
            && [1, 2, 4].contains(&self.subdivisions_per_beat)
            && (-500_000_000..=500_000_000).contains(&self.sync_offset_ns)
            && self
                .activity
                .is_none_or(RenderMusicActivityConfig::is_valid)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderDynamicIdleMode {
    Off,
    Solid,
    Animation,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderDynamicIdle {
    pub mode: RenderDynamicIdleMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub animation: Option<String>,
    #[serde(default)]
    pub settings: Map<String, Value>,
    pub color: [u8; 3],
    pub brightness: f64,
}

impl RenderDynamicIdle {
    pub fn is_valid(&self) -> bool {
        self.brightness.is_finite()
            && (0.0..=1.0).contains(&self.brightness)
            && match self.mode {
                RenderDynamicIdleMode::Off | RenderDynamicIdleMode::Solid => {
                    self.animation.is_none()
                }
                RenderDynamicIdleMode::Animation => self
                    .animation
                    .as_ref()
                    .is_some_and(|animation| !animation.is_empty()),
            }
    }
}

/// Parameters for the music activity gate used by Rust-owned Dynamic playback.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderMusicActivityConfig {
    pub idle_enter_frames: u32,
    pub activation_delay_ns: u64,
    pub feature_attack: f64,
    pub feature_release: f64,
    pub energy_threshold: f64,
    pub onset_threshold: f64,
    pub beat_density_threshold: f64,
    pub brightness_threshold: f64,
    pub spectral_balance_ratio: f64,
    pub activation_hysteresis_ratio: f64,
    pub beat_density_window_ns: u64,
}

impl RenderMusicActivityConfig {
    pub fn is_valid(self) -> bool {
        let unit_values = [
            self.feature_attack,
            self.feature_release,
            self.energy_threshold,
            self.onset_threshold,
            self.beat_density_threshold,
            self.brightness_threshold,
            self.spectral_balance_ratio,
        ];
        self.idle_enter_frames > 0
            && self.beat_density_window_ns > 0
            && unit_values
                .into_iter()
                .all(|value| value.is_finite() && (0.0..=1.0).contains(&value))
            && self.activation_hysteresis_ratio.is_finite()
            && self.activation_hysteresis_ratio >= 1.0
    }
}

impl Default for RenderMusicActivityConfig {
    fn default() -> Self {
        Self {
            idle_enter_frames: 60,
            activation_delay_ns: 750_000_000,
            feature_attack: 0.28,
            feature_release: 0.08,
            energy_threshold: 0.03,
            onset_threshold: 0.025,
            beat_density_threshold: 0.05,
            brightness_threshold: 0.08,
            spectral_balance_ratio: 0.35,
            activation_hysteresis_ratio: 1.35,
            beat_density_window_ns: 2_000_000_000,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderCycleOrder {
    Sequential,
    Shuffle,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderCycleTiming {
    PerAnimation,
    Fixed,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderSwitchQuantization {
    #[default]
    Off,
    Beat,
    Bar,
    Phrase,
}

fn default_bars_per_phrase() -> usize {
    8
}

fn default_clock_confidence_threshold() -> f64 {
    0.65
}

fn default_subdivisions_per_beat() -> usize {
    1
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderAnimationChoice {
    pub animation: String,
    #[serde(default)]
    pub settings: Map<String, Value>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderTransition {
    pub started_at_ns: u64,
    pub duration_ns: u64,
    pub mode: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderEffect {
    pub name: String,
    #[serde(default)]
    pub settings: Map<String, Value>,
    pub intensity: f64,
    #[serde(default, skip_serializing_if = "is_false")]
    pub trigger_beat: bool,
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    pub trigger_beat_strength: f64,
}

fn default_render_intensity() -> f64 {
    1.0
}

fn default_true() -> bool {
    true
}

fn is_true(value: &bool) -> bool {
    *value
}

fn default_overlay_strength() -> f64 {
    0.65
}

fn default_zone_count() -> usize {
    1
}

fn default_zone_pattern() -> String {
    "repeat".to_owned()
}

fn is_one(value: &f64) -> bool {
    *value == 1.0
}

fn is_zero_f64(value: &f64) -> bool {
    *value == 0.0
}

fn is_zero_u64(value: &u64) -> bool {
    *value == 0
}

fn is_zero_usize(value: &usize) -> bool {
    *value == 0
}

fn is_default_overlay_strength(value: &f64) -> bool {
    *value == 0.65
}

fn is_one_zone(value: &usize) -> bool {
    *value == 1
}

fn is_repeat_zone_pattern(value: &str) -> bool {
    value == "repeat"
}

fn is_false(value: &bool) -> bool {
    !*value
}

fn default_color_correction() -> [u8; 3] {
    [255; 3]
}

fn is_identity_correction(value: &[u8; 3]) -> bool {
    value == &[255; 3]
}

fn default_voltage_v() -> f64 {
    5.0
}

fn is_default_voltage_v(value: &f64) -> bool {
    *value == 5.0
}

fn default_full_white_current_a() -> f64 {
    0.06
}

fn is_default_full_white_current_a(value: &f64) -> bool {
    *value == 0.06
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Response {
    pub protocol_version: u16,
    pub request_id: String,
    pub result: ResponseResult,
}

/// Version 1 remains available for existing single-output simulated clients.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LegacyRequest {
    pub protocol_version: u16,
    pub request_id: String,
    pub command: LegacyRequestCommand,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "data", rename_all = "snake_case")]
pub enum LegacyRequestCommand {
    Health,
    Configure { config: LegacyConfig },
    Render { pixels: Vec<[u8; 3]> },
    Shutdown,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LegacyConfig {
    pub pixel_count: usize,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LegacyResponse {
    pub protocol_version: u16,
    pub request_id: String,
    pub result: LegacyResponseResult,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum LegacyResponseResult {
    Health {
        status: EngineStatus,
        pixel_count: Option<usize>,
        frames_rendered: u64,
    },
    Configured {
        pixel_count: usize,
    },
    Rendered {
        frame_index: u64,
        pixel_count: usize,
    },
    Shutdown,
    Error {
        code: LegacyErrorCode,
        message: String,
    },
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LegacyErrorCode {
    UnsupportedVersion,
    InvalidConfig,
    InvalidFrame,
    NotConfigured,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ResponseResult {
    Health {
        status: EngineStatus,
        configured: bool,
        output_count: usize,
        pixel_counts: Vec<usize>,
        frames_rendered: u64,
        last_sequence: Option<u64>,
        dropped_frames: u64,
        failed_frames: u64,
        #[serde(default, skip_serializing_if = "is_zero_u64")]
        scheduler_missed_deadlines: u64,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        selected_animations: Vec<String>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        scene_revisions: Vec<u64>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        structure_event_sequences: Vec<u64>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        cycling_active: Vec<bool>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        dynamic_gate_states: Vec<String>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        selector_diagnostics: Vec<RenderSelectorDiagnostics>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        beat_clocks: Vec<RenderBeatClockState>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        sequence_playback: Vec<RenderSequencePlaybackState>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        effect_schedulers: Vec<RenderEffectSchedulerState>,
        last_error: Option<String>,
        output_rate: f64,
        frame_latency_ms: Option<f64>,
        queue_latency_ms: Option<f64>,
        transmission_ms: Option<f64>,
        frame_latency_p50_ms: Option<f64>,
        frame_latency_p95_ms: Option<f64>,
        frame_latency_p99_ms: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        audio_feature_to_led_latency_ms: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        audio_feature_to_led_p50_ms: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        audio_feature_to_led_p95_ms: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        audio_feature_to_led_p99_ms: Option<f64>,
        sequential_skew_ms: Option<f64>,
        last_successful_update_epoch_ms: Option<u64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        render_time_ms: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        scene_control_revision: Option<u64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        audio_update_sequence: Option<u64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        estimated_power_watts: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        applied_power_scale: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        output_power_watts: Option<Vec<f64>>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        limiting_output_index: Option<usize>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        preview_outputs: Option<Vec<Vec<u8>>>,
    },
    Configured {
        output_count: usize,
        total_pixels: usize,
    },
    RenderCapabilities {
        animations: Vec<String>,
    },
    RenderQueued {
        sequence: u64,
        replaced: bool,
    },
    SceneUpdated {
        revision: u64,
        replaced: bool,
    },
    AudioUpdated {
        sequence: u64,
        accepted: bool,
    },
    AudioConfigured {
        active: bool,
    },
    AudioStopped,
    AudioDevices {
        devices: Vec<InputAudioDevice>,
    },
    AudioStatus {
        status: EngineAudioStatus,
    },
    Shutdown,
    Error {
        code: EngineErrorCode,
        message: String,
    },
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct EngineAudioStatus {
    pub active: bool,
    #[serde(default)]
    pub input_transport: AudioInputTransport,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub device: Option<InputAudioDevice>,
    #[serde(default)]
    pub frame: AudioFrame,
    #[serde(default)]
    pub features: MusicFeatures,
    #[serde(default)]
    pub structure: MusicStructureState,
    #[serde(default)]
    pub processor: AudioProcessorStats,
    #[serde(default)]
    pub capture: AudioCaptureStats,
    #[serde(default)]
    pub pcm_ingress: PcmIngressStats,
    #[serde(default)]
    pub dsp_processing_p50_ns: u64,
    #[serde(default)]
    pub dsp_processing_p95_ns: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_frame_ns: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
    #[serde(default)]
    pub audio_update_sequence: u64,
    #[serde(default)]
    pub dropped_audio_updates: u64,
    #[serde(default)]
    pub dropped_structure_events: u64,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AudioInputTransport {
    #[default]
    Off,
    CaptureDevice,
    DirectPcm,
    MonitorFallback,
    Demo,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineStatus {
    Ready,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineErrorCode {
    UnsupportedVersion,
    InvalidConfig,
    InvalidFrame,
    InvalidRender,
    AudioFailure,
    NotConfigured,
    OutputFailure,
    ShuttingDown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FrameHeader {
    pub output_count: u16,
    pub sequence: u64,
    pub payload_bytes: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FrameAck {
    pub status: FrameAckStatus,
    pub sequence: u64,
    pub dropped_frames: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u16)]
pub enum FrameAckStatus {
    Accepted = 0,
    NotConfigured = 1,
    ShuttingDown = 2,
    InvalidFrame = 3,
}

impl Request {
    pub fn new(request_id: impl Into<String>, command: RequestCommand) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            request_id: request_id.into(),
            command,
        }
    }
}

impl Response {
    pub fn new(request_id: impl Into<String>, result: ResponseResult) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            request_id: request_id.into(),
            result,
        }
    }
}

impl LegacyRequest {
    pub fn new(request_id: impl Into<String>, command: LegacyRequestCommand) -> Self {
        Self {
            protocol_version: LEGACY_PROTOCOL_VERSION,
            request_id: request_id.into(),
            command,
        }
    }
}

impl LegacyResponse {
    pub fn new(request_id: impl Into<String>, result: LegacyResponseResult) -> Self {
        Self {
            protocol_version: LEGACY_PROTOCOL_VERSION,
            request_id: request_id.into(),
            result,
        }
    }
}

pub fn encode_request(request: &Request) -> Result<Vec<u8>, serde_json::Error> {
    encode_line(request)
}

pub fn encode_response(response: &Response) -> Result<Vec<u8>, serde_json::Error> {
    encode_line(response)
}

pub fn decode_request(bytes: &[u8]) -> Result<Request, serde_json::Error> {
    serde_json::from_slice(bytes)
}

pub fn decode_response(bytes: &[u8]) -> Result<Response, serde_json::Error> {
    serde_json::from_slice(bytes)
}

pub fn encode_legacy_request(request: &LegacyRequest) -> Result<Vec<u8>, serde_json::Error> {
    encode_line(request)
}

pub fn decode_legacy_response(bytes: &[u8]) -> Result<LegacyResponse, serde_json::Error> {
    serde_json::from_slice(bytes)
}

pub fn encode_legacy_response(response: &LegacyResponse) -> Result<Vec<u8>, serde_json::Error> {
    encode_line(response)
}

pub fn decode_legacy_request(bytes: &[u8]) -> Result<LegacyRequest, serde_json::Error> {
    serde_json::from_slice(bytes)
}

pub fn encode_frame_header(header: FrameHeader) -> Result<[u8; FRAME_HEADER_BYTES], ProtocolError> {
    if header.output_count == 0 || header.payload_bytes as usize > MAX_FRAME_BYTES {
        return Err(ProtocolError::InvalidFrameHeader);
    }
    let mut bytes = [0u8; FRAME_HEADER_BYTES];
    bytes[..4].copy_from_slice(&FRAME_MAGIC);
    bytes[4..6].copy_from_slice(&FRAME_VERSION.to_le_bytes());
    bytes[6..8].copy_from_slice(&header.output_count.to_le_bytes());
    bytes[8..16].copy_from_slice(&header.sequence.to_le_bytes());
    bytes[16..20].copy_from_slice(&header.payload_bytes.to_le_bytes());
    Ok(bytes)
}

pub fn decode_frame_header(bytes: &[u8; FRAME_HEADER_BYTES]) -> Result<FrameHeader, ProtocolError> {
    if bytes[..4] != FRAME_MAGIC {
        return Err(ProtocolError::InvalidFrameHeader);
    }
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    let output_count = u16::from_le_bytes([bytes[6], bytes[7]]);
    let sequence = u64::from_le_bytes(bytes[8..16].try_into().unwrap());
    let payload_bytes = u32::from_le_bytes(bytes[16..20].try_into().unwrap());
    if version != FRAME_VERSION || output_count == 0 || payload_bytes as usize > MAX_FRAME_BYTES {
        return Err(ProtocolError::InvalidFrameHeader);
    }
    Ok(FrameHeader {
        output_count,
        sequence,
        payload_bytes,
    })
}

pub fn encode_frame_ack(ack: FrameAck) -> [u8; FRAME_ACK_BYTES] {
    let mut bytes = [0u8; FRAME_ACK_BYTES];
    bytes[..4].copy_from_slice(&FRAME_ACK_MAGIC);
    bytes[4..6].copy_from_slice(&FRAME_VERSION.to_le_bytes());
    bytes[6..8].copy_from_slice(&(ack.status as u16).to_le_bytes());
    bytes[8..16].copy_from_slice(&ack.sequence.to_le_bytes());
    bytes[16..24].copy_from_slice(&ack.dropped_frames.to_le_bytes());
    bytes
}

pub fn decode_frame_ack(bytes: &[u8; FRAME_ACK_BYTES]) -> Result<FrameAck, ProtocolError> {
    if bytes[..4] != FRAME_ACK_MAGIC || u16::from_le_bytes([bytes[4], bytes[5]]) != FRAME_VERSION {
        return Err(ProtocolError::InvalidFrameAcknowledgement);
    }
    let status = match u16::from_le_bytes([bytes[6], bytes[7]]) {
        0 => FrameAckStatus::Accepted,
        1 => FrameAckStatus::NotConfigured,
        2 => FrameAckStatus::ShuttingDown,
        3 => FrameAckStatus::InvalidFrame,
        _ => return Err(ProtocolError::InvalidFrameAcknowledgement),
    };
    Ok(FrameAck {
        status,
        sequence: u64::from_le_bytes(bytes[8..16].try_into().unwrap()),
        dropped_frames: u64::from_le_bytes(bytes[16..24].try_into().unwrap()),
    })
}

fn encode_line<T: Serialize>(value: &T) -> Result<Vec<u8>, serde_json::Error> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    Ok(bytes)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProtocolError {
    InvalidFrameHeader,
    InvalidFrameAcknowledgement,
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidFrameHeader => f.write_str("invalid binary frame header"),
            Self::InvalidFrameAcknowledgement => {
                f.write_str("invalid binary frame acknowledgement")
            }
        }
    }
}

impl std::error::Error for ProtocolError {}

#[cfg(test)]
mod tests {
    use super::{
        AudioConfig, FRAME_ACK_BYTES, FRAME_HEADER_BYTES, FrameAck, FrameAckStatus, FrameHeader,
        LEGACY_PROTOCOL_VERSION, LegacyConfig, LegacyRequest, LegacyRequestCommand, LegacyResponse,
        LegacyResponseResult, PREVIOUS_PROTOCOL_VERSION, PROTOCOL_VERSION, RenderAnimationChoice,
        RenderAudioInput, RenderBeatEvent, RenderCycleOrder, RenderCycleTiming, RenderCycling,
        RenderDynamicSelector, RenderEffect, RenderFrameRequest, RenderOutput, RenderPowerConfig,
        RenderSchedulerConfig, RenderSwitchQuantization, RenderTransition, Request, RequestCommand,
        Response, ResponseResult, decode_frame_ack, decode_frame_header, encode_frame_ack,
        encode_frame_header,
    };
    use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
    use serde_json::{Map, Value, json};

    fn health_response(request_id: &str) -> Response {
        Response::new(
            request_id,
            ResponseResult::Health {
                status: super::EngineStatus::Ready,
                configured: false,
                output_count: 0,
                pixel_counts: vec![],
                frames_rendered: 0,
                last_sequence: None,
                dropped_frames: 0,
                failed_frames: 0,
                scheduler_missed_deadlines: 0,
                selected_animations: Vec::new(),
                scene_revisions: Vec::new(),
                structure_event_sequences: Vec::new(),
                cycling_active: Vec::new(),
                dynamic_gate_states: Vec::new(),
                selector_diagnostics: Vec::new(),
                beat_clocks: Vec::new(),
                sequence_playback: Vec::new(),
                effect_schedulers: Vec::new(),
                scene_control_revision: None,
                audio_update_sequence: None,
                last_error: None,
                output_rate: 0.0,
                frame_latency_ms: None,
                queue_latency_ms: None,
                transmission_ms: None,
                frame_latency_p50_ms: None,
                frame_latency_p95_ms: None,
                frame_latency_p99_ms: None,
                audio_feature_to_led_latency_ms: None,
                audio_feature_to_led_p50_ms: None,
                audio_feature_to_led_p95_ms: None,
                audio_feature_to_led_p99_ms: None,
                sequential_skew_ms: None,
                last_successful_update_epoch_ms: None,
                render_time_ms: None,
                estimated_power_watts: None,
                applied_power_scale: None,
                output_power_watts: None,
                limiting_output_index: None,
                preview_outputs: None,
            },
        )
    }

    #[test]
    fn dynamic_selector_defaults_new_quantization_fields_for_v2_clients() {
        let selector: RenderDynamicSelector = serde_json::from_value(json!({
            "min_duration_ns": 0,
            "max_duration_ns": 1_000_000_000,
            "switch_cooldown_ns": 0,
            "drop_cooldown_ns": 0,
            "randomness": 0.0,
            "history_size": 0,
            "seed": 7,
            "switch_margin": 0.0
        }))
        .unwrap();
        assert_eq!(selector.switch_quantization, RenderSwitchQuantization::Off);
        assert_eq!(selector.bars_per_phrase, 8);
        assert_eq!(selector.clock_confidence_threshold, 0.65);
        assert_eq!(selector.subdivisions_per_beat, 1);
        assert_eq!(selector.sync_offset_ns, 0);
        assert!(selector.is_valid());
    }

    #[test]
    fn health_request_matches_shared_v2_fixture() {
        let mut request = Request::new("fixture-health-2", RequestCommand::Health);
        request.protocol_version = 2;
        let actual: Value =
            serde_json::from_slice(&super::encode_request(&request).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol-v2/health-request.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
        assert_eq!(request.protocol_version, 2);
    }

    #[test]
    fn health_response_matches_shared_v2_fixture() {
        let mut response = health_response("fixture-health-2");
        response.protocol_version = 2;
        let actual: Value =
            serde_json::from_slice(&super::encode_response(&response).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol-v2/health-response.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
    }

    #[test]
    fn health_request_matches_shared_v3_fixture() {
        let mut request = Request::new("fixture-health-3", RequestCommand::Health);
        request.protocol_version = PREVIOUS_PROTOCOL_VERSION;
        let actual: Value =
            serde_json::from_slice(&super::encode_request(&request).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol-v3/health-request.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
        assert_eq!(request.protocol_version, PREVIOUS_PROTOCOL_VERSION);
    }

    #[test]
    fn health_response_matches_shared_v3_fixture() {
        let mut response = health_response("fixture-health-3");
        response.protocol_version = PREVIOUS_PROTOCOL_VERSION;
        let actual: Value =
            serde_json::from_slice(&super::encode_response(&response).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol-v3/health-response.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
        assert_eq!(response.protocol_version, PREVIOUS_PROTOCOL_VERSION);
    }

    #[test]
    fn health_request_matches_shared_v4_fixture() {
        let request = Request::new("fixture-health-4", RequestCommand::Health);
        let actual: Value =
            serde_json::from_slice(&super::encode_request(&request).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol-v4/health-request.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
        assert_eq!(request.protocol_version, PROTOCOL_VERSION);
    }

    #[test]
    fn health_response_matches_shared_v4_fixture() {
        let response = health_response("fixture-health-4");
        let actual: Value =
            serde_json::from_slice(&super::encode_response(&response).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol-v4/health-response.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
        assert_eq!(response.protocol_version, PROTOCOL_VERSION);
    }

    #[test]
    fn legacy_health_messages_match_the_shared_v1_fixtures() {
        let request = LegacyRequest::new("fixture-health-1", LegacyRequestCommand::Health);
        let actual: Value = serde_json::from_slice(&serde_json::to_vec(&request).unwrap()).unwrap();
        let request_fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol-v1/health-request.json"
        ))
        .unwrap();
        assert_eq!(actual, request_fixture);
        assert_eq!(request.protocol_version, LEGACY_PROTOCOL_VERSION);

        let response = LegacyResponse::new(
            "fixture-health-1",
            LegacyResponseResult::Health {
                status: super::EngineStatus::Ready,
                pixel_count: Some(3),
                frames_rendered: 7,
            },
        );
        let actual: Value =
            serde_json::from_slice(&super::encode_legacy_response(&response).unwrap()).unwrap();
        let response_fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/protocol-v1/health-response.json"
        ))
        .unwrap();
        assert_eq!(actual, response_fixture);
    }

    #[test]
    fn legacy_configuration_and_rgb_frames_remain_typed() {
        let request = LegacyRequest::new(
            "configure-1",
            LegacyRequestCommand::Configure {
                config: LegacyConfig { pixel_count: 3 },
            },
        );
        assert_eq!(request.protocol_version, LEGACY_PROTOCOL_VERSION);
        let encoded = serde_json::to_vec(&request).unwrap();
        assert_eq!(super::decode_legacy_request(&encoded).unwrap(), request);

        let render = LegacyRequest::new(
            "render-1",
            LegacyRequestCommand::Render {
                pixels: vec![[1, 2, 3], [4, 5, 6]],
            },
        );
        let encoded = serde_json::to_vec(&render).unwrap();
        assert_eq!(super::decode_legacy_request(&encoded).unwrap(), render);
    }

    #[test]
    fn configure_control_message_is_typed() {
        let request = Request::new(
            "configure-1",
            RequestCommand::Configure {
                config: EngineConfig {
                    outputs: vec![OutputConfig {
                        id: "main".into(),
                        pixel_count: 2,
                        reversed: true,
                        backend: OutputBackend::Mock,
                    }],
                },
            },
        );
        let decoded = super::decode_request(&super::encode_request(&request).unwrap()).unwrap();
        assert_eq!(decoded, request);
    }

    #[test]
    fn direct_audio_start_control_message_is_typed() {
        let request = Request::new(
            "audio-direct-start-1",
            RequestCommand::AudioStartDirect {
                fallback_selector: Some("pipewire:Spotify monitor".to_owned()),
                config: AudioConfig::default(),
            },
        );

        let decoded = super::decode_request(&super::encode_request(&request).unwrap()).unwrap();

        assert_eq!(decoded, request);
    }

    #[test]
    fn scheduled_scene_and_audio_updates_are_separate_typed_commands() {
        let scene = Request::new(
            "scene-update-1",
            RequestCommand::SceneUpdate {
                update: super::RenderSceneUpdate {
                    revision: 9,
                    timestamp_ns: 1_250_000_000,
                    brightness: 0.8,
                    power: RenderPowerConfig::default(),
                    outputs: vec![RenderOutput {
                        animation: "rainbow".into(),
                        ..RenderOutput::default()
                    }],
                    scheduler: RenderSchedulerConfig { max_fps: 60 },
                },
            },
        );
        let audio = Request::new(
            "audio-update-1",
            RequestCommand::AudioUpdate {
                update: super::RenderAudioUpdate {
                    sequence: 23,
                    timestamp_ns: 1_266_000_000,
                    audio: None,
                },
            },
        );

        for request in [scene, audio] {
            let encoded = super::encode_request(&request).unwrap();
            assert_eq!(super::decode_request(&encoded).unwrap(), request);
        }
    }

    #[test]
    fn render_control_message_carries_audio_and_settings_without_pixels() {
        let request = Request::new(
            "render-1",
            RequestCommand::Render {
                frame: RenderFrameRequest {
                    sequence: 8,
                    timestamp_ns: 1_000_000_000,
                    delta_ns: 16_000_000,
                    brightness: 0.8,
                    audio: Some(RenderAudioInput {
                        rms: 0.4,
                        bands: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
                        beat: true,
                        beat_strength: 0.9,
                        feature_timestamp_ns: Some(990_000_000),
                        feature_sequence: Some(17),
                        fallback_bpm: Some(128.0),
                        beat_events: vec![RenderBeatEvent {
                            sequence: 13,
                            timestamp_ns: 1_250_000_000,
                            strength: 0.7,
                        }],
                        structure_events: Vec::new(),
                        selector_features: None,
                        effect_features: None,
                        beat_event_stream: false,
                    }),
                    power: RenderPowerConfig {
                        enabled: true,
                        budget_watts: Some(17.5),
                    },
                    outputs: vec![
                        RenderOutput {
                            animation: "rainbow".into(),
                            settings: Map::new(),
                            effects: vec![RenderEffect {
                                name: "hard_beat".into(),
                                settings: Map::new(),
                                intensity: 0.75,
                                trigger_beat: true,
                                trigger_beat_strength: 0.9,
                            }],
                            base_intensity: 0.8,
                            max_overlay_strength: 0.5,
                            scene_revision: 9,
                            brightness: Some(0.4),
                            zone_count: 3,
                            zone_pattern: "alternate".into(),
                            scene_pixel_count: Some(8),
                            scene_offset: 3,
                            transition: Some(RenderTransition {
                                started_at_ns: 900_000_000,
                                duration_ns: 200_000_000,
                                mode: "wipe".into(),
                            }),
                            cycling: Some(RenderCycling {
                                order: RenderCycleOrder::Sequential,
                                timing: RenderCycleTiming::PerAnimation,
                                interval_ns: 30_000_000_000,
                                seed: Some(7),
                                choices: vec![RenderAnimationChoice {
                                    animation: "rainbow".into(),
                                    settings: Map::new(),
                                }],
                                transition_duration_ns: 300_000_000,
                                transition_mode: "crossfade".into(),
                                selector: None,
                                dynamic_idle: None,
                                sequence: None,
                                effect_scheduler: None,
                            }),
                            blackout: false,
                            color_correction: [128, 240, 255],
                            voltage_v: 12.0,
                            full_white_current_a: 0.08,
                            power_limit_watts: Some(6.0),
                            ..RenderOutput::default()
                        },
                        RenderOutput {
                            animation: "rainbow".into(),
                            scene_pixel_count: Some(8),
                            scene_resample: true,
                            ..RenderOutput::default()
                        },
                    ],
                },
                scheduler: Some(RenderSchedulerConfig { max_fps: 60 }),
            },
        );
        let encoded = super::encode_request(&request).unwrap();
        assert!(
            encoded.len() < 2048,
            "render request should contain no pixel frame"
        );
        let decoded = super::decode_request(&encoded).unwrap();
        assert_eq!(decoded, request);
        let encoded_text = std::str::from_utf8(&encoded).unwrap();
        assert!(encoded_text.contains("\"zone_count\":3"));
        assert!(encoded_text.contains("\"zone_pattern\":\"alternate\""));
        assert!(encoded_text.contains("\"scene_pixel_count\":8"));
        assert!(encoded_text.contains("\"scene_offset\":3"));
        assert!(encoded_text.contains("\"scene_resample\":true"));
        assert!(encoded_text.contains("\"budget_watts\":17.5"));
        assert!(encoded_text.contains("\"color_correction\":[128,240,255]"));
        assert!(encoded_text.contains("\"max_fps\":60"));
        assert!(encoded_text.contains("\"timing\":\"per_animation\""));
        assert!(encoded_text.contains("\"feature_sequence\":17"));
        assert!(encoded_text.contains("\"feature_timestamp_ns\":990000000"));
        assert!(encoded_text.contains("\"fallback_bpm\":128.0"));
        assert!(encoded_text.contains("\"timestamp_ns\":1250000000"));
    }

    #[test]
    fn render_control_message_defaults_zone_mapping_for_older_clients() {
        let request = br#"{"protocol_version":2,"request_id":"render-old","command":{"type":"render","data":{"frame":{"sequence":1,"timestamp_ns":0,"delta_ns":16000000,"brightness":1.0,"audio":null,"outputs":[{"animation":"rainbow"}]}}}}"#;
        let decoded = super::decode_request(request).unwrap();
        let super::RequestCommand::Render { frame, scheduler } = decoded.command else {
            panic!("expected render request");
        };
        assert_eq!(scheduler, None);
        assert_eq!(frame.outputs[0].zone_count, 1);
        assert_eq!(frame.outputs[0].zone_pattern, "repeat");
        assert!(frame.outputs[0].transition.is_none());
        assert_eq!(frame.outputs[0].color_correction, [255; 3]);
        assert_eq!(frame.outputs[0].voltage_v, 5.0);
        assert_eq!(frame.outputs[0].full_white_current_a, 0.06);
        assert!(frame.outputs[0].power_limit_watts.is_none());
        assert!(!frame.power.enabled);
    }

    #[test]
    fn older_audio_render_messages_default_beat_clock_extensions() {
        let request = br#"{"protocol_version":2,"request_id":"render-audio-old","command":{"type":"render","data":{"frame":{"sequence":1,"timestamp_ns":1000000000,"delta_ns":16000000,"brightness":1.0,"audio":{"rms":0.4,"bands":[0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8],"beat":true,"beat_strength":0.9},"outputs":[{"animation":"rainbow"}]}}}}"#;
        let decoded = super::decode_request(request).unwrap();
        let super::RequestCommand::Render { frame, .. } = decoded.command else {
            panic!("expected render request");
        };
        let audio = frame.audio.unwrap();
        assert_eq!(audio.fallback_bpm, None);
        assert!(audio.beat_events.is_empty());
    }

    #[test]
    fn render_capability_query_is_a_typed_control_command() {
        let request = Request::new("render-capabilities", RequestCommand::RenderCapabilities);
        let encoded = super::encode_request(&request).unwrap();
        assert_eq!(super::decode_request(&encoded).unwrap(), request);
    }

    #[test]
    fn binary_frame_header_and_ack_are_fixed_size_and_little_endian() {
        let header = FrameHeader {
            output_count: 2,
            sequence: 0x0102_0304_0506_0708,
            payload_bytes: 24,
        };
        let encoded = encode_frame_header(header).unwrap();
        assert_eq!(encoded.len(), FRAME_HEADER_BYTES);
        assert_eq!(&encoded[8..16], &[8, 7, 6, 5, 4, 3, 2, 1]);
        assert_eq!(decode_frame_header(&encoded), Ok(header));

        let ack = FrameAck {
            status: FrameAckStatus::Accepted,
            sequence: header.sequence,
            dropped_frames: 9,
        };
        let encoded_ack = encode_frame_ack(ack);
        assert_eq!(encoded_ack.len(), FRAME_ACK_BYTES);
        assert_eq!(decode_frame_ack(&encoded_ack), Ok(ack));
    }

    #[test]
    fn rejects_bad_frame_headers_and_acknowledgements() {
        let mut header = [0u8; FRAME_HEADER_BYTES];
        assert!(decode_frame_header(&header).is_err());
        header[..4].copy_from_slice(b"LSF2");
        header[4..6].copy_from_slice(&99u16.to_le_bytes());
        assert!(decode_frame_header(&header).is_err());
        assert!(decode_frame_ack(&[0u8; FRAME_ACK_BYTES]).is_err());
    }
}
