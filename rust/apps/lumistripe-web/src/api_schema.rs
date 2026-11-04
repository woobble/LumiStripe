//! Rust-owned OpenAPI models for the REST contract.
//!
//! Request models used by handlers live beside those handlers. These response
//! models describe the JSON values returned by the runtime control plane.

use crate::bluetooth_models::{
    BluetoothCapabilities, BluetoothDeviceRole, BluetoothOperationState,
};
use crate::settings::{
    MusicIdleMode, MusicIdleSettings, SavedAnimationSequence, StripeBackend, StripeLayout,
    SwitchQuantization, TransitionEffect, TransitionSettings, ZonePattern,
};
use crate::spotify::{SpotifyPlaybackStatus, SpotifyRepeat, SpotifyStatus};
use crate::state::PlaybackMode;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use utoipa::ToSchema;
use utoipa::openapi::RefOr;
use utoipa::openapi::schema::{
    AdditionalProperties, AnyOfBuilder, Object, ObjectBuilder, Schema, Type,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq, ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum RuntimeKind {
    Simulation,
    Hardware,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AccessStatus {
    pub required: bool,
    pub authenticated: bool,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct DiagnosticIssue {
    pub severity: String,
    pub title: String,
    pub message: String,
    pub action: String,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct ColorCorrectionProfile {
    #[schema(minimum = 0)]
    pub output_index: usize,
    pub name: String,
    pub device: String,
    #[schema(maximum = 255)]
    pub red: u8,
    #[schema(maximum = 255)]
    pub green: u8,
    #[schema(maximum = 255)]
    pub blue: u8,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct CalibrationStatus {
    #[schema(required = false, default = false)]
    pub active: bool,
    #[serde(default)]
    pub output_index: Option<usize>,
    #[serde(default)]
    pub pattern: Option<CalibrationPattern>,
    #[serde(default)]
    pub expires_in_seconds: Option<f64>,
}

#[derive(Clone, Copy, Debug, ToSchema)]
#[serde(rename_all = "lowercase")]
pub(crate) enum CalibrationPattern {
    White,
    Red,
    Green,
    Blue,
}

#[derive(Clone, Copy, Debug, ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum AudioSource {
    Auto,
    Off,
    Demo,
    Mic,
    Bluetooth,
    Spotify,
}

#[derive(Clone, Copy, Debug, ToSchema)]
#[serde(rename_all = "lowercase")]
pub(crate) enum StripeTestPattern {
    Identify,
    Red,
    Green,
    Blue,
    White,
}

pub(crate) fn enum_schema_with_default<T: ToSchema>(default: Value) -> RefOr<Schema> {
    match <T as utoipa::PartialSchema>::schema() {
        RefOr::T(Schema::Object(mut schema)) => {
            schema.default = Some(default);
            RefOr::T(Schema::Object(schema))
        }
        schema => schema,
    }
}

pub(crate) fn bluetooth_operation_state_schema() -> RefOr<Schema> {
    enum_schema_with_default::<BluetoothOperationState>(json!("idle"))
}

pub(crate) fn music_idle_mode_schema() -> RefOr<Schema> {
    enum_schema_with_default::<MusicIdleMode>(json!("off"))
}

pub(crate) fn stripe_backend_schema() -> RefOr<Schema> {
    enum_schema_with_default::<StripeBackend>(json!("spi"))
}

pub(crate) fn stripe_layout_schema() -> RefOr<Schema> {
    enum_schema_with_default::<StripeLayout>(json!("mirrored"))
}

pub(crate) fn stripe_test_pattern_schema() -> RefOr<Schema> {
    enum_schema_with_default::<StripeTestPattern>(json!("identify"))
}

pub(crate) fn transition_effect_schema() -> RefOr<Schema> {
    enum_schema_with_default::<TransitionEffect>(json!("crossfade"))
}

pub(crate) fn zone_pattern_schema() -> RefOr<Schema> {
    enum_schema_with_default::<ZonePattern>(json!("repeat"))
}

pub(crate) fn spotify_playback_status_schema() -> RefOr<Schema> {
    enum_schema_with_default::<SpotifyPlaybackStatus>(json!("idle"))
}

pub(crate) fn spotify_repeat_schema() -> RefOr<Schema> {
    enum_schema_with_default::<SpotifyRepeat>(json!("off"))
}

#[derive(Clone, Debug, ToSchema)]
#[schema(as = AudioOutputDeviceInfo)]
pub(crate) struct AudioOutputDeviceInfoSchema {
    pub selector: String,
    pub name: String,
    #[schema(required = false, minimum = 0.0, maximum = 1.0)]
    pub volume: Option<f64>,
    #[schema(required = false, default = false)]
    pub muted: bool,
    #[schema(required = false, default = false)]
    pub bluetooth: bool,
    #[schema(required = false, default = true)]
    pub connected: bool,
}

#[derive(Clone, Debug, ToSchema)]
#[schema(as = BluetoothDeviceInfo)]
pub(crate) struct BluetoothDeviceInfoSchema {
    #[schema(required = true)]
    pub address: String,
    #[schema(required = false, default = false)]
    pub connected: bool,
    #[schema(required = true)]
    pub name: String,
    #[schema(required = false, default = false)]
    pub paired: bool,
    #[schema(required = false, default = json!([]))]
    pub roles: Vec<BluetoothDeviceRole>,
}

#[derive(Clone, Debug, ToSchema)]
#[schema(as = BluetoothStatusResponse)]
pub(crate) struct BluetoothStatusResponseSchema {
    #[schema(required = false, default = false)]
    pub available: bool,
    #[schema(required = false, default = false)]
    pub powered: bool,
    #[schema(required = false)]
    pub adapter_alias: Option<String>,
    #[schema(required = false, default = false)]
    pub scanning: bool,
    #[schema(required = false, default = false)]
    pub streaming: bool,
    #[schema(required = false, default = json!([]))]
    pub devices: Vec<BluetoothDeviceInfoSchema>,
    #[schema(required = false, default = json!([]))]
    pub connected_inputs: Vec<BluetoothDeviceInfoSchema>,
    #[schema(required = false, default = json!([]))]
    pub connected_outputs: Vec<BluetoothDeviceInfoSchema>,
    #[schema(required = false)]
    pub connected_device: Option<BluetoothDeviceInfoSchema>,
    #[schema(required = false)]
    pub input_source: Option<String>,
    #[schema(required = false, default = json!([]))]
    pub output_devices: Vec<AudioOutputDeviceInfoSchema>,
    #[schema(required = false)]
    pub default_sink: Option<String>,
    #[schema(required = false, minimum = 0.0, maximum = 1.0)]
    pub output_volume: Option<f64>,
    #[schema(required = false, default = false)]
    pub output_muted: bool,
    #[schema(required = false, default = false)]
    pub output_ready: bool,
    #[schema(required = false)]
    pub capabilities: BluetoothCapabilities,
    #[schema(required = false)]
    pub operation: Option<String>,
    #[schema(required = false)]
    pub operation_id: Option<String>,
    #[schema(required = false, schema_with = bluetooth_operation_state_schema)]
    pub operation_state: BluetoothOperationState,
    #[schema(required = false)]
    pub error: Option<String>,
}

#[derive(Clone, Debug, ToSchema)]
#[schema(as = AudioTuningValues)]
pub(crate) struct AudioTuningValuesSchema {
    #[schema(required = false, minimum = 0.1, maximum = 0.8, default = 0.36)]
    pub target_level: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0)]
    pub hardware_gain_target: Option<f64>,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.015)]
    pub noise_floor: f64,
    #[schema(required = false, minimum = 0.0, maximum = 0.5, default = 0.003)]
    pub input_noise_floor: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.65)]
    pub dynamic_response: f64,
    #[schema(required = false, minimum = 0.01, maximum = 1.0, default = 0.45)]
    pub rms_attack: f64,
    #[schema(required = false, minimum = 0.01, maximum = 1.0, default = 0.12)]
    pub rms_release: f64,
    #[schema(required = false, minimum = 0.01, maximum = 1.0, default = 0.4)]
    pub band_attack: f64,
    #[schema(required = false, minimum = 0.01, maximum = 1.0, default = 0.1)]
    pub band_release: f64,
    #[schema(required = false, minimum = 0.01, maximum = 1.0, default = 0.18)]
    pub beat_release: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.03)]
    pub energy_threshold: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.025)]
    pub onset_threshold: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.05)]
    pub beat_density_threshold: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.08)]
    pub brightness_threshold: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.35)]
    pub spectral_balance_ratio: f64,
}

#[derive(Clone, Debug, ToSchema)]
#[serde(untagged)]
pub(crate) enum ValidationLocation {
    Integer(i64),
    String(String),
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct PowerOutputState {
    pub output_id: String,
    #[schema(minimum = 0.0)]
    pub estimated_watts: f64,
    #[serde(default)]
    #[schema(exclusive_minimum = 0.0)]
    pub limit_watts: Option<f64>,
    #[schema(minimum = 0.0, maximum = 1.0)]
    pub applied_scale: f64,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct PowerBudgetState {
    #[schema(required = false, default = false)]
    pub enabled: bool,
    #[serde(default)]
    #[schema(minimum = 0.0)]
    pub budget_watts: Option<f64>,
    #[schema(required = false, minimum = 0.0, default = 0.0)]
    pub estimated_watts: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 1.0)]
    pub applied_scale: f64,
    #[serde(default)]
    pub limiting_output_id: Option<String>,
    #[schema(required = false, default = json!([]))]
    pub outputs: Vec<PowerOutputState>,
    #[schema(required = false, default = false)]
    pub shared_supply_enabled: bool,
    #[serde(default)]
    #[schema(exclusive_minimum = 0.0)]
    pub supply_capacity_watts: Option<f64>,
    #[serde(default)]
    pub pi_reserve_watts: Option<f64>,
    #[serde(default)]
    pub available_led_watts: Option<f64>,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct SequencerDiagnostics {
    #[schema(required = false, default = "off")]
    pub mode: SwitchQuantization,
    #[schema(required = false, default = 4)]
    pub beats_per_bar: u32,
    #[schema(required = false, default = 8)]
    pub bars_per_phrase: u32,
    #[serde(default)]
    pub beat_index: Option<i64>,
    #[serde(default)]
    pub beat_in_bar: Option<i64>,
    #[serde(default)]
    pub bar_index: Option<i64>,
    #[serde(default)]
    pub bar_in_phrase: Option<i64>,
    #[serde(default)]
    pub phrase_index: Option<i64>,
    #[serde(default)]
    pub beat_in_phrase: Option<i64>,
    #[schema(required = false, minimum = 0.0, default = 0.0)]
    pub bpm: f64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.0)]
    pub beat_clock_confidence: f64,
    #[schema(required = false, default = false)]
    pub alignment_estimated: bool,
    #[serde(default)]
    pub pending_animation: Option<String>,
    #[serde(default)]
    #[schema(minimum = 0.0)]
    pub next_switch_in_s: Option<f64>,
    #[schema(required = false, default = false)]
    pub waiting_for_clock: bool,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct SequenceLibraryEntry {
    pub id: String,
    pub name: String,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct SequenceLibrarySummary {
    #[schema(required = false, default = json!([]))]
    pub sequences: Vec<SequenceLibraryEntry>,
    #[serde(default)]
    pub active_sequence_id: Option<String>,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct SequencePlaybackDiagnostics {
    #[serde(default)]
    pub selected_sequence_id: Option<String>,
    #[schema(required = false, default = false)]
    pub active: bool,
    #[serde(default)]
    pub current_step_index: Option<usize>,
    #[serde(default)]
    pub animation_name: Option<String>,
    #[serde(default)]
    pub remaining_bars: Option<u32>,
    #[schema(required = false, default = false)]
    pub waiting_for_clock: bool,
    #[serde(default)]
    #[schema(minimum = 0.0)]
    pub next_transition_in_s: Option<f64>,
}

#[derive(Clone, Debug, ToSchema)]
#[schema(as = StripePlaybackState)]
pub(crate) struct StripePlaybackStateSchema {
    #[schema(required = true)]
    pub stripe_id: String,
    #[schema(required = false, default = "static")]
    pub mode: PlaybackMode,
    #[schema(required = false, default = "#7C3AED")]
    pub solid_color: String,
    #[schema(required = false, default = "")]
    pub animation: String,
    #[schema(required = false, default = 1.0)]
    pub brightness: f64,
    #[schema(required = false, default = false)]
    pub blackout: bool,
    #[schema(required = false, default = false)]
    pub music_active: bool,
    #[schema(required = false, default = true)]
    pub music_recognition_enabled: bool,
    #[schema(required = false)]
    pub music_idle: MusicIdleSettings,
    #[schema(required = false)]
    pub sequencer_diagnostics: SequencerDiagnostics,
    #[schema(required = false)]
    pub sequence_playback: SequencePlaybackDiagnostics,
}

#[derive(Clone, Debug, ToSchema)]
#[schema(as = DashboardState)]
pub(crate) struct DashboardStateSchema {
    #[schema(required = false, default = 0)]
    pub revision: u64,
    #[schema(required = true)]
    pub runtime: RuntimeKind,
    #[schema(required = false)]
    pub spotify: SpotifyStatus,
    #[schema(required = false)]
    pub bluetooth: BluetoothStatusResponseSchema,
    #[schema(required = false, default = "simulation")]
    pub output_backend: String,
    #[schema(required = false, default = json!([]))]
    pub output_devices: Vec<String>,
    #[serde(default)]
    pub spi_speed_hz: Option<u32>,
    #[schema(required = false, default = false)]
    pub running: bool,
    #[schema(required = false, default = "static")]
    pub mode: PlaybackMode,
    #[schema(required = false, default = "#7C3AED")]
    pub solid_color: String,
    #[schema(required = false, default = "")]
    pub animation: String,
    #[schema(required = false, default = 1.0)]
    pub brightness: f64,
    #[schema(required = false, default = false)]
    pub blackout: bool,
    #[schema(required = false, default = false)]
    pub music_active: bool,
    #[schema(required = false, default = true)]
    pub music_recognition_enabled: bool,
    #[schema(required = false)]
    pub music_idle: MusicIdleSettings,
    #[schema(required = false, default = "calm")]
    pub music_gate: String,
    #[schema(required = false, default = 0.0)]
    pub bpm: f64,
    #[schema(required = false, default = "No audio source active.")]
    pub audio_status: String,
    #[serde(default)]
    pub configured_audio_source: Option<String>,
    #[serde(default)]
    pub active_audio_source: Option<String>,
    #[serde(default)]
    pub audio_monitoring: Option<bool>,
    #[schema(required = false, default = json!([]))]
    pub active_effects: Vec<String>,
    #[schema(required = false, default = 0.0)]
    pub uptime_seconds: f64,
    #[schema(required = false, default = 0.0)]
    pub frame_rate: f64,
    #[schema(required = false, minimum = 0.0, default = 0.0)]
    pub render_time_ms: f64,
    #[serde(default)]
    pub scheduler_time_ms: Option<f64>,
    #[serde(default)]
    pub worker_heartbeat_age_seconds: Option<f64>,
    #[schema(required = false, default = 0)]
    pub missed_frame_count: u64,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 0.0)]
    pub recent_frame_miss_rate: f64,
    #[schema(required = false, minimum = 0.0, default = 0.0)]
    pub output_frame_rate: f64,
    #[serde(default)]
    pub output_frame_latency_ms: Option<f64>,
    #[serde(default)]
    pub output_queue_latency_ms: Option<f64>,
    #[serde(default)]
    pub output_transmission_ms: Option<f64>,
    #[serde(default)]
    pub output_frame_latency_p50_ms: Option<f64>,
    #[serde(default)]
    pub output_frame_latency_p95_ms: Option<f64>,
    #[serde(default)]
    pub output_frame_latency_p99_ms: Option<f64>,
    #[serde(default)]
    pub output_ipc_latency_ms: Option<f64>,
    #[serde(default)]
    pub output_sequential_skew_ms: Option<f64>,
    #[serde(default)]
    pub output_heartbeat_age_seconds: Option<f64>,
    #[schema(required = false, default = 0)]
    pub output_dropped_frame_count: u64,
    #[schema(required = false, default = 0)]
    pub output_failure_count: u64,
    #[serde(default)]
    pub output_error: Option<String>,
    #[schema(required = false, default = 0)]
    pub command_queue_depth: usize,
    #[schema(required = false, default = "inactive")]
    pub audio_health: String,
    #[serde(default)]
    pub audio_callback_age_seconds: Option<f64>,
    #[serde(default)]
    pub audio_frame_age_seconds: Option<f64>,
    #[serde(default)]
    #[schema(format = DateTime)]
    pub last_output_at: Option<String>,
    #[serde(default)]
    pub last_output_age_seconds: Option<f64>,
    #[schema(required = false, default = "development")]
    pub application_version: String,
    #[schema(required = false, default = "LumiStripe")]
    pub application_name: String,
    #[schema(required = false, default = json!([]))]
    pub color_corrections: Vec<ColorCorrectionProfile>,
    #[schema(required = false, default = json!({"active": false}))]
    pub calibration: CalibrationStatus,
    #[schema(required = false, default = json!({
        "layout": "mirrored",
        "outputs": [],
        "pi_reserve_watts": 15.0,
        "power_budget_enabled": false,
        "shared_supply_enabled": false,
        "supply_capacity_watts": 200.0
    }))]
    pub stripe_topology: StripeTopologyResponse,
    #[schema(required = false, default = json!({
        "applied_scale": 1.0,
        "enabled": false,
        "estimated_watts": 0.0,
        "outputs": [],
        "shared_supply_enabled": false
    }))]
    pub power_budget: PowerBudgetState,
    #[schema(required = false)]
    pub sequencer_settings: SequencerSettingsResponse,
    #[schema(required = false)]
    pub transition_settings: TransitionSettings,
    #[schema(required = false)]
    pub sequence_library: SequenceLibrarySummary,
    #[schema(required = false, default = json!([]))]
    pub stripe_playback: Vec<StripePlaybackStateSchema>,
    #[schema(required = false, default = json!([]))]
    pub diagnostic_issues: Vec<DiagnosticIssue>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct SequencerSettingsResponse {
    #[schema(required = false, default = "off")]
    pub switch_quantization: SwitchQuantization,
    #[schema(required = false, schema_with = duration_phrase_bars_default_schema)]
    pub bars_per_phrase: u8,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AnimationSettingOption {
    #[schema(required = true)]
    pub id: String,
    #[schema(required = true)]
    pub label: String,
    #[schema(required = true)]
    pub r#type: AnimationSettingType,
    #[schema(required = true, schema_with = animation_setting_value_schema)]
    pub default: Value,
    #[schema(required = true)]
    pub description: String,
    #[serde(default)]
    pub minimum: Option<f64>,
    #[serde(default)]
    pub maximum: Option<f64>,
    #[serde(default)]
    pub step: Option<f64>,
    #[serde(default)]
    pub unit: Option<String>,
    #[schema(required = false, default = json!([]))]
    pub choices: Vec<String>,
    #[serde(default)]
    pub visible_when_option: Option<String>,
    #[serde(default)]
    pub visible_when_value: Option<String>,
}

#[derive(Clone, Copy, Debug, ToSchema)]
#[serde(rename_all = "lowercase")]
pub(crate) enum AnimationSettingType {
    Number,
    Boolean,
    Enum,
    Color,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AnimationOption {
    #[schema(required = true)]
    pub name: String,
    #[schema(required = true)]
    pub display_name: String,
    #[schema(required = true)]
    pub description: String,
    #[schema(required = true)]
    pub role: AnimationRole,
    #[schema(required = true)]
    pub mood: String,
    #[schema(required = true)]
    pub dynamic_safe: bool,
    #[schema(required = true)]
    pub sequence_eligible: bool,
    #[schema(required = false, default = json!([]))]
    pub settings: Vec<AnimationSettingOption>,
}

#[derive(Clone, Copy, Debug, ToSchema)]
#[serde(rename_all = "lowercase")]
pub(crate) enum AnimationRole {
    Base,
    Effect,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AnimationList {
    pub items: Vec<AnimationOption>,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AnimationSettingsResponse {
    pub animation: String,
    #[serde(default)]
    pub stripe_id: Option<String>,
    pub options: Vec<AnimationSettingOption>,
    #[schema(schema_with = animation_setting_map_schema)]
    pub values: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AudioCalibrationResult {
    pub duration_seconds: f64,
    #[schema(minimum = 0)]
    pub samples: usize,
    pub measured_floor: f64,
    pub measured_peak: f64,
    pub recommended_noise_floor: f64,
    pub recommended_target_level: f64,
    #[serde(default)]
    #[schema(minimum = 0.0, maximum = 1.0)]
    pub recommended_hardware_gain: Option<f64>,
    pub recommended_idle_threshold_scale: f64,
    #[serde(default)]
    #[schema(default = 0.003)]
    pub recommended_input_noise_floor: f64,
    #[serde(default)]
    #[schema(default = 0.0)]
    pub measured_music_level: f64,
    #[serde(default)]
    #[schema(default = 0.0)]
    pub clipping_fraction: f64,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AudioCalibrationSessionResponse {
    #[schema(required = true)]
    pub session_id: String,
    #[schema(required = true)]
    pub status: String,
    #[schema(required = false, default = 0.0)]
    pub elapsed_seconds: f64,
    #[schema(required = false, default = 0.0)]
    pub remaining_seconds: f64,
    #[serde(default)]
    pub result: Option<AudioCalibrationResult>,
    #[serde(default)]
    pub error: Option<String>,
    #[schema(required = false, default = "quiet")]
    pub phase: String,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct CalibrationSessionResponse {
    pub session_id: String,
    pub state: DashboardStateSchema,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AudioResetRequest {
    #[schema(min_length = 1)]
    pub device: String,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AudioSettingsResponse {
    #[schema(required = true)]
    pub source: String,
    #[schema(required = false, default = "off")]
    pub active_source: String,
    #[schema(required = true)]
    pub monitoring: bool,
    #[serde(default)]
    pub active_device: Option<String>,
    #[serde(default)]
    pub fallback_device: Option<String>,
    #[serde(default)]
    pub active_device_name: Option<String>,
    #[schema(required = false, default = json!([]))]
    pub devices: Vec<AudioDeviceOption>,
    #[schema(required = false, default = json!({}))]
    pub settings: AudioTuningValuesSchema,
    #[schema(required = false, default = 0.015)]
    pub configured_noise_floor: f64,
    #[schema(required = false, default = "mic|unknown|default")]
    pub sync_route: String,
    #[schema(required = false, minimum = -500.0, maximum = 500.0, default = 0.0)]
    pub sync_offset_ms: f64,
    #[schema(required = false, default = false)]
    pub hardware_gain_supported: bool,
    #[schema(required = false, default = false)]
    pub hardware_gain_writable: bool,
    #[serde(default)]
    pub hardware_gain_backend: Option<String>,
    #[serde(default)]
    pub hardware_gain_control: Option<String>,
    #[serde(default)]
    pub hardware_gain_value: Option<f64>,
    #[serde(default)]
    pub hardware_gain_error: Option<String>,
    #[schema(required = false)]
    pub bluetooth: BluetoothStatusResponseSchema,
    #[schema(required = true)]
    pub spotify: SpotifyStatus,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AudioDeviceOption {
    pub selector: String,
    pub name: String,
    pub settings: AudioTuningValuesSchema,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct AudioSyncCalibrationResponse {
    #[serde(default)]
    pub start_at_ms: Option<i64>,
    pub sync_route: String,
    #[schema(minimum = -500.0, maximum = 500.0)]
    pub sync_offset_ms: f64,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct IdentitySettingsResponse {
    pub application_name: String,
    pub spotify_device_name: String,
    pub spotify_restart_requested: bool,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct StartupPlaybackState {
    #[schema(required = false, default = "static")]
    pub mode: PlaybackMode,
    #[schema(required = false, default = "#7C3AED", pattern = "^#[0-9A-Fa-f]{6}$")]
    pub solid_color: String,
    #[schema(required = false, default = "")]
    pub animation: String,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 1.0)]
    pub brightness: f64,
    #[schema(required = false, default = false)]
    pub blackout: bool,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct StartupSettingsResponse {
    pub restore_last_state: bool,
    pub remembered: StartupPlaybackState,
}

#[derive(Clone, Debug, ToSchema)]
pub(crate) struct SequenceLibraryResponse {
    #[schema(required = false, default = json!([]))]
    pub sequences: Vec<SavedAnimationSequence>,
    #[serde(default)]
    pub active_sequence_id: Option<String>,
}

#[derive(Clone, Debug, ToSchema)]
#[schema(as = StripeTopology)]
pub(crate) struct StripeTopologyResponse {
    #[schema(required = false, schema_with = stripe_layout_schema)]
    pub layout: StripeLayout,
    #[schema(required = false, default = json!([]), max_items = 2)]
    pub outputs: Vec<crate::app::StripeOutputRequest>,
    #[schema(required = false, default = false)]
    pub power_budget_enabled: bool,
    #[serde(default)]
    #[schema(exclusive_minimum = 0.0)]
    pub power_budget_watts: Option<f64>,
    #[schema(required = false, default = false)]
    pub shared_supply_enabled: bool,
    #[schema(required = false, exclusive_minimum = 0.0, default = 200.0)]
    pub supply_capacity_watts: f64,
    #[schema(required = false, minimum = 0.0, default = 15.0)]
    pub pi_reserve_watts: f64,
}

pub(crate) fn duration_phrase_bars_schema() -> Object {
    ObjectBuilder::new()
        .schema_type(Type::Integer)
        .enum_values(Some([json!(4), json!(8), json!(16)]))
        .build()
}

pub(crate) fn duration_phrase_bars_default_schema() -> Object {
    ObjectBuilder::new()
        .schema_type(Type::Integer)
        .default(Some(json!(8)))
        .enum_values(Some([json!(4), json!(8), json!(16)]))
        .build()
}

pub(crate) fn duration_sequence_bars_schema() -> Object {
    ObjectBuilder::new()
        .schema_type(Type::Integer)
        .enum_values(Some([json!(1), json!(2), json!(4), json!(8)]))
        .build()
}

pub(crate) fn arbitrary_object_schema() -> Object {
    ObjectBuilder::new()
        .schema_type(Type::Object)
        .additional_properties(Some(AdditionalProperties::FreeForm(true)))
        .build()
}

pub(crate) fn animation_setting_value_schema() -> RefOr<Schema> {
    Schema::AnyOf(
        AnyOfBuilder::new()
            .item(ObjectBuilder::new().schema_type(Type::String))
            .item(ObjectBuilder::new().schema_type(Type::Integer))
            .item(ObjectBuilder::new().schema_type(Type::Number))
            .item(ObjectBuilder::new().schema_type(Type::Boolean))
            .build(),
    )
    .into()
}

fn animation_setting_map_schema() -> Object {
    let value_schema: RefOr<Schema> = animation_setting_value_schema();
    ObjectBuilder::new()
        .schema_type(Type::Object)
        .additional_properties(Some(value_schema))
        .build()
}

pub(crate) fn spotify_control_value_schema() -> RefOr<Schema> {
    Schema::AnyOf(
        AnyOfBuilder::new()
            .item(ObjectBuilder::new().schema_type(Type::Integer))
            .item(ObjectBuilder::new().schema_type(Type::Number))
            .item(ObjectBuilder::new().schema_type(Type::Boolean))
            .item(ObjectBuilder::new().schema_type(Type::String))
            .item(ObjectBuilder::new().schema_type(Type::Null))
            .build(),
    )
    .into()
}
