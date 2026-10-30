import { z } from "zod"

import type {
  AudioTelemetry,
  AudioTuningValues,
  BluetoothDeviceRole,
  BluetoothStatusResponse,
  CalibrationPattern,
  DashboardState,
  NetworkStatusResponse,
  WifiConnectResponse,
  WifiScanResponse,
  PlaybackMode,
  SpotifyControlAction,
  SwitchQuantization,
  TransitionEffect,
  TransitionSettingsRequest,
  SequenceCreateRequest,
  SequenceDefinitionRequest,
  StripeTopology,
  SystemStats,
  UpdateStatus,
} from "@/lib/api/contracts"

export type {
  AccessStatus,
  AnimationList,
  AnimationOption,
  AnimationSettingOption,
  AnimationSettingsResponse,
  AnimationSettingsUpdateRequest,
  AnimationSequenceContract,
  AnimationSequenceStepContract,
  AudioCalibrationResult,
  AudioCalibrationSessionResponse,
  AudioDeviceOption,
  AudioOutputDeviceInfo,
  AudioSettingsResponse,
  AudioTelemetry,
  AudioTuningValues,
  BluetoothCapabilities,
  BluetoothDeviceInfo,
  BluetoothDeviceRole,
  BluetoothOperation,
  BluetoothStatusResponse,
  NetworkAdapterInfo,
  NetworkStatusResponse,
  WifiConnectResponse,
  WifiNetworkInfo,
  WifiScanResponse,
  CalibrationPattern,
  CalibrationSessionResponse,
  CalibrationStatus,
  ColorCorrectionProfile,
  DashboardState,
  DiagnosticIssue,
  PlaybackMode,
  PowerBudgetState,
  PowerOutputState,
  RuntimeKind,
  StartupPlaybackState,
  StartupSettingsResponse,
  SystemStats,
  SequenceCreateRequest,
  SequenceDefinitionRequest,
  SequenceLibraryResponse,
  StripeBackend,
  StripeLayout,
  StripeOutputConfig,
  StripePlaybackState,
  StripeTopology,
  SwitchQuantization,
  SequencerDiagnostics,
  SequencerSettingsRequest,
  SequencerSettingsResponse,
  TransitionEffect,
  TransitionSettingsRequest,
  TransitionSettingsResponse,
  SpotifyControlAction,
  SpotifyStatusResponse,
  SpotifyTrackInfo,
  UpdateStatus,
} from "@/lib/api/contracts"

export const ACCESS_REVOKED_EVENT = "lumistripe:access-revoked"

const playbackModeSchema = z.enum(["solid", "static", "cycling", "dynamic"])
const switchQuantizationSchema = z.enum(["off", "beat", "bar", "phrase"])
const transitionEffectSchema = z.enum(["crossfade", "fade_black", "wipe"])
const sequencerSettingsSchema = z.object({
  switch_quantization: switchQuantizationSchema,
  bars_per_phrase: z.union([z.literal(4), z.literal(8), z.literal(16)]),
}).strict()
const transitionSettingsSchema = z.object({
  enabled: z.boolean(),
  duration_ms: z.number().int().min(100).max(1500),
  effect: transitionEffectSchema.optional().default("crossfade"),
}).strict()
const sequencerDiagnosticsSchema = z.object({
  mode: switchQuantizationSchema,
  beats_per_bar: z.number().int(),
  bars_per_phrase: z.number().int(),
  beat_index: z.number().int().nullable(),
  beat_in_bar: z.number().int().nullable(),
  bar_index: z.number().int().nullable(),
  bar_in_phrase: z.number().int().nullable(),
  phrase_index: z.number().int().nullable(),
  beat_in_phrase: z.number().int().nullable(),
  bpm: z.number().nonnegative(),
  beat_clock_confidence: z.number().min(0).max(1),
  alignment_estimated: z.boolean(),
  pending_animation: z.string().nullable(),
  next_switch_in_s: z.number().nonnegative().nullable(),
  waiting_for_clock: z.boolean(),
}).strict()
const sequencePlaybackSchema = z.object({
  selected_sequence_id: z.string().nullable(),
  active: z.boolean(),
  current_step_index: z.number().int().nullable(),
  animation_name: z.string().nullable(),
  remaining_bars: z.number().int().nullable(),
  waiting_for_clock: z.boolean(),
  next_transition_in_s: z.number().nonnegative().nullable(),
}).strict()
const sequenceLibrarySummarySchema = z.object({
  sequences: z.array(z.object({ id: z.string(), name: z.string() }).strict()),
  active_sequence_id: z.string().nullable(),
}).strict()
const stripeOutputSchema = z.object({
  id: z.string(),
  name: z.string(),
  pixels: z.number(),
  backend: z.enum(["spi", "gpio"]),
  reversed: z.boolean(),
  zone_count: z.number().int().min(1).max(4096).default(1),
  zone_pattern: z.enum(["repeat", "alternate"]).default("repeat"),
  spi_device: z.string(),
  spi_speed_hz: z.number(),
  chip: z.string(),
  data_pin: z.number(),
  clock_pin: z.number(),
  voltage_v: z.number(),
  full_white_current_a: z.number(),
  power_limit_watts: z.number().nullable().optional(),
  last_output_at: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
}).strict().superRefine((output, context) => {
  if (output.zone_count > output.pixels) {
    context.addIssue({ code: "custom", path: ["zone_count"], message: "Zone count cannot exceed the stripe pixel count." })
  }
})
const stripeTopologySchema = z.object({
  layout: z.enum(["mirrored", "continuous", "independent"]),
  outputs: z.array(stripeOutputSchema),
  power_budget_enabled: z.boolean().optional().default(false),
  power_budget_watts: z.number().nullable().optional(),
  shared_supply_enabled: z.boolean().optional().default(false),
  supply_capacity_watts: z.number().positive().optional().default(200),
  pi_reserve_watts: z.number().nonnegative().optional().default(15),
}).strict()
const musicIdleSchema = z.object({
  mode: z.enum(["off", "solid", "animation"]),
  color: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  animation: z.string(),
  brightness: z.number().min(0).max(1).optional().default(1),
}).strict()
const defaultMusicIdle = { mode: "off" as const, color: "#2060FF", animation: "", brightness: 1 }
const stripePlaybackSchema = z.object({
  stripe_id: z.string(),
  mode: playbackModeSchema,
  solid_color: z.string(),
  animation: z.string(),
  brightness: z.number(),
  blackout: z.boolean(),
  music_active: z.boolean(),
  music_recognition_enabled: z.boolean(),
  music_idle: musicIdleSchema.optional().default(defaultMusicIdle),
  sequencer_diagnostics: sequencerDiagnosticsSchema.optional().default({
    mode: "off",
    beats_per_bar: 4,
    bars_per_phrase: 8,
    beat_index: null,
    beat_in_bar: null,
    bar_index: null,
    bar_in_phrase: null,
    phrase_index: null,
    beat_in_phrase: null,
    bpm: 0,
    beat_clock_confidence: 0,
    alignment_estimated: false,
    pending_animation: null,
    next_switch_in_s: null,
    waiting_for_clock: false,
  }),
  sequence_playback: sequencePlaybackSchema.optional().default({
    selected_sequence_id: null,
    active: false,
    current_step_index: null,
    animation_name: null,
    remaining_bars: null,
    waiting_for_clock: false,
    next_transition_in_s: null,
  }),
}).strict()
const powerOutputSchema = z.object({
  output_id: z.string(),
  estimated_watts: z.number(),
  limit_watts: z.number().nullable().optional(),
  applied_scale: z.number(),
}).strict()
const powerBudgetSchema = z.object({
  enabled: z.boolean(),
  budget_watts: z.number().nullable().optional(),
  estimated_watts: z.number(),
  applied_scale: z.number(),
  limiting_output_id: z.string().nullable().optional(),
  outputs: z.array(powerOutputSchema),
  shared_supply_enabled: z.boolean().optional().default(false),
  supply_capacity_watts: z.number().positive().nullable().optional(),
  pi_reserve_watts: z.number().nonnegative().nullable().optional(),
  available_led_watts: z.number().nonnegative().nullable().optional(),
}).strict()
const diagnosticIssueSchema = z.object({
  severity: z.string(),
  title: z.string(),
  message: z.string(),
  action: z.string(),
}).strict()
const calibrationStatusSchema = z.object({
  active: z.boolean(),
  output_index: z.number().int().nullable().optional(),
  pattern: z.enum(["white", "red", "green", "blue"]).nullable().optional(),
  expires_in_seconds: z.number().nullable().optional(),
}).strict()
const colorCorrectionProfileSchema = z.object({
  output_index: z.number().int(),
  name: z.string(),
  device: z.string(),
  red: z.number().int(),
  green: z.number().int(),
  blue: z.number().int(),
}).strict()

/** Strict validation for the state snapshot shared by REST and WebSocket clients. */
const dashboardStateSchema = z.object({
  revision: z.number().int(),
  runtime: z.enum(["simulation", "hardware"]),
  output_backend: z.string(),
  output_devices: z.array(z.string()),
  spi_speed_hz: z.number().int().nullable().optional(),
  running: z.boolean(),
  mode: playbackModeSchema,
  solid_color: z.string(),
  animation: z.string(),
  brightness: z.number(),
  blackout: z.boolean(),
  music_active: z.boolean(),
  music_recognition_enabled: z.boolean(),
  music_idle: musicIdleSchema.optional().default(defaultMusicIdle),
  music_gate: z.string(),
  bpm: z.number(),
  audio_status: z.string(),
  configured_audio_source: z.string().nullable().optional(),
  active_audio_source: z.string().nullable().optional(),
  audio_monitoring: z.boolean().nullable().optional(),
  active_effects: z.array(z.string()),
  uptime_seconds: z.number(),
  frame_rate: z.number(),
  render_time_ms: z.number().nonnegative(),
  worker_heartbeat_age_seconds: z.number().nullable().optional(),
  missed_frame_count: z.number().int(),
  recent_frame_miss_rate: z.number().min(0).max(1),
  command_queue_depth: z.number().int(),
  audio_health: z.string(),
  audio_callback_age_seconds: z.number().nullable().optional(),
  audio_frame_age_seconds: z.number().nullable().optional(),
  last_output_at: z.string().nullable().optional(),
  last_output_age_seconds: z.number().nullable().optional(),
  application_version: z.string(),
  application_name: z.string().default("LumiStripe"),
  color_corrections: z.array(colorCorrectionProfileSchema),
  calibration: calibrationStatusSchema,
  stripe_topology: stripeTopologySchema,
  power_budget: powerBudgetSchema.optional().default({
    enabled: false,
    budget_watts: null,
    estimated_watts: 0,
    applied_scale: 1,
    limiting_output_id: null,
    outputs: [],
    shared_supply_enabled: false,
    supply_capacity_watts: null,
    pi_reserve_watts: null,
    available_led_watts: null,
  }),
  sequencer_settings: sequencerSettingsSchema.optional().default({
    switch_quantization: "off",
    bars_per_phrase: 8,
  }),
  transition_settings: transitionSettingsSchema.optional().default({
    enabled: true,
    duration_ms: 300,
    effect: "crossfade",
  }),
  sequence_library: sequenceLibrarySummarySchema.optional().default({
    sequences: [],
    active_sequence_id: null,
  }),
  stripe_playback: z.array(stripePlaybackSchema),
  diagnostic_issues: z.array(diagnosticIssueSchema),
  error: z.string().nullable().optional(),
}).strict()

export function parseDashboardState(value: unknown): DashboardState {
  return dashboardStateSchema.parse(value)
}

const animationSettingOptionSchema = z.object({
  id: z.string(),
  label: z.string(),
  type: z.enum(["number", "boolean", "enum", "color"]),
  default: z.union([z.string(), z.number(), z.boolean()]),
  description: z.string(),
  minimum: z.number().nullable().optional(),
  maximum: z.number().nullable().optional(),
  step: z.number().nullable().optional(),
  unit: z.string().nullable().optional(),
  choices: z.array(z.string()).optional().default([]),
  visible_when_option: z.string().nullable().optional(),
  visible_when_value: z.string().nullable().optional(),
}).strict()
const animationOptionSchema = z.object({
  name: z.string(),
  display_name: z.string().optional(),
  description: z.string().optional(),
  role: z.enum(["base", "effect"]).optional(),
  mood: z.string(),
  dynamic_safe: z.boolean(),
  sequence_eligible: z.boolean().optional().default(false),
  settings: z.array(animationSettingOptionSchema).optional().default([]),
}).strict().transform((animation) => {
  const name = animation.name.replaceAll("_", " ").replaceAll("-", " ")
  return {
    ...animation,
    display_name: animation.display_name ?? name.replace(/\b\w/g, (letter) => letter.toUpperCase()),
    description: animation.description ?? `${name.replace(/\b\w/g, (letter) => letter.toUpperCase())} lighting pattern.`,
    role: animation.role ?? "base" as const,
  }
})
const animationListSchema = z.object({ items: z.array(animationOptionSchema) }).strict()
const animationSettingsResponseSchema = z.object({
  animation: z.string(),
  stripe_id: z.string().nullable().optional(),
  options: z.array(animationSettingOptionSchema),
  values: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])),
}).strict()
const animationSequenceStepSchema = z.object({
  animation_name: z.string().min(1).max(100),
  duration_bars: z.union([z.literal(1), z.literal(2), z.literal(4), z.literal(8)]),
}).strict()
const animationSequenceSchema = z.object({
  id: z.string().min(1).max(64),
  name: z.string().min(1).max(80),
  repeat: z.boolean(),
  steps: z.array(animationSequenceStepSchema).min(1).max(32),
}).strict()
const animationSequenceLibrarySchema = z.object({
  sequences: z.array(animationSequenceSchema),
  active_sequence_id: z.string().nullable().optional(),
}).strict()
const accessStatusSchema = z.object({
  required: z.boolean(),
  authenticated: z.boolean(),
}).strict()
const bluetoothDeviceSchema = z.object({
  address: z.string(),
  name: z.string(),
  paired: z.boolean(),
  connected: z.boolean(),
  roles: z.array(z.enum(["input", "output"])),
}).strict()
const audioOutputDeviceSchema = z.object({
  selector: z.string(),
  name: z.string(),
  volume: z.number().nullable().optional(),
  muted: z.boolean(),
  bluetooth: z.boolean(),
  connected: z.boolean(),
}).strict()
const bluetoothCapabilitiesSchema = z.object({
  operations: z.array(z.enum([
    "power", "rename", "scan", "pair", "connect", "disconnect", "forget",
    "output_select", "output_volume", "output_mute",
  ])),
  max_inputs: z.number().int().min(1),
  max_outputs: z.number().int().min(1),
}).strict()

/** Strict validation for the Bluetooth status payload shared by REST and UI. */
const bluetoothStatusSchema = z.object({
  available: z.boolean(),
  powered: z.boolean(),
  adapter_alias: z.string().nullable().optional(),
  scanning: z.boolean(),
  streaming: z.boolean(),
  devices: z.array(bluetoothDeviceSchema),
  connected_inputs: z.array(bluetoothDeviceSchema),
  connected_outputs: z.array(bluetoothDeviceSchema),
  connected_device: bluetoothDeviceSchema.nullable().optional(),
  input_source: z.string().nullable().optional(),
  output_devices: z.array(audioOutputDeviceSchema),
  default_sink: z.string().nullable().optional(),
  output_volume: z.number().nullable().optional(),
  output_muted: z.boolean(),
  output_ready: z.boolean(),
  capabilities: bluetoothCapabilitiesSchema.optional().default({ operations: [], max_inputs: 1, max_outputs: 1 }),
  operation: z.string().nullable().optional(),
  operation_id: z.string().nullable().optional(),
  operation_state: z.enum(["idle", "running", "complete", "failed"]).optional().default("idle"),
  error: z.string().nullable().optional(),
}).strict()

function parseBluetoothStatus(value: unknown): BluetoothStatusResponse {
  const parsed = bluetoothStatusSchema.parse(value)
  return {
    ...parsed,
    capabilities: parsed.capabilities ?? { operations: [], max_inputs: 1, max_outputs: 1 },
    operation_id: parsed.operation_id ?? null,
    operation_state: parsed.operation_state ?? "idle",
  }
}

const networkStatusSchema = z.object({
  manager_available: z.boolean(),
  error: z.string().nullable(),
  adapters: z.array(z.object({
    interface: z.string(),
    state: z.string(),
    connection: z.string().nullable(),
    current_ssid: z.string().nullable(),
    hotspot: z.boolean().nullable(),
    connectable: z.boolean(),
    reason: z.string().nullable(),
  }).strict()),
}).strict()

const wifiScanSchema = z.object({
  interface: z.string(),
  networks: z.array(z.object({
    ssid: z.string(),
    security: z.string(),
    signal: z.number().int().min(0).max(100),
    secured: z.boolean(),
  }).strict()),
}).strict()

const wifiConnectSchema = z.object({
  interface: z.string(),
  ssid: z.string(),
  connected: z.boolean(),
}).strict()

const audioTuningSchema = z.object({
  target_level: z.number(),
  hardware_gain_target: z.number().nullable().optional(),
  noise_floor: z.number(),
  input_noise_floor: z.number().min(0).max(0.5).default(0.003),
  dynamic_response: z.number(),
  rms_attack: z.number(),
  rms_release: z.number(),
  band_attack: z.number(),
  band_release: z.number(),
  beat_release: z.number(),
  energy_threshold: z.number(),
  onset_threshold: z.number(),
  beat_density_threshold: z.number(),
  brightness_threshold: z.number(),
  spectral_balance_ratio: z.number(),
}).strict()
const audioDeviceSchema = z.object({
  selector: z.string(),
  name: z.string(),
  settings: audioTuningSchema,
}).strict()
const spotifyTrackSchema = z.object({
  uri: z.string(),
  name: z.string(),
  artists: z.array(z.string()),
  album: z.string().nullable().optional(),
  cover_url: z.string().nullable().optional(),
  duration_ms: z.number().int().nullable().optional(),
}).strict()
const spotifyStatusSchema = z.object({
  configured: z.boolean(),
  connected: z.boolean(),
  logged_in: z.boolean(),
  is_active: z.boolean(),
  device_name: z.string().nullable().optional(),
  status: z.enum(["idle", "playing", "paused", "buffering"]),
  track: spotifyTrackSchema.nullable().optional(),
  position_ms: z.number().int(),
  duration_ms: z.number().int().nullable().optional(),
  volume: z.number().int().min(0).max(100),
  shuffle: z.boolean(),
  repeat: z.enum(["off", "context", "track"]),
  error: z.string().nullable().optional(),
}).strict()
const defaultSpotifyStatus = {
  configured: false,
  connected: false,
  logged_in: false,
  is_active: false,
  device_name: null,
  status: "idle" as const,
  track: null,
  position_ms: 0,
  duration_ms: null,
  volume: 0,
  shuffle: false,
  repeat: "off" as const,
  error: null,
}
const audioSettingsSchema = z.object({
  source: z.string(),
  active_source: z.string(),
  monitoring: z.boolean(),
  active_device: z.string().nullable().optional(),
  fallback_device: z.string().nullable().optional(),
  active_device_name: z.string().nullable().optional(),
  devices: z.array(audioDeviceSchema),
  settings: audioTuningSchema,
  configured_noise_floor: z.number(),
  sync_route: z.string().optional().default("mic|unknown|default"),
  sync_offset_ms: z.number().min(-500).max(500).optional().default(0),
  hardware_gain_supported: z.boolean(),
  hardware_gain_writable: z.boolean(),
  hardware_gain_backend: z.string().nullable().optional(),
  hardware_gain_control: z.string().nullable().optional(),
  hardware_gain_value: z.number().nullable().optional(),
  hardware_gain_error: z.string().nullable().optional(),
  bluetooth: bluetoothStatusSchema.optional(),
  // Older dashboard snapshots predate Spotify; keep the compatibility
  // default explicit while new responses always include the field.
  spotify: spotifyStatusSchema.optional().default(defaultSpotifyStatus),
  error: z.string().nullable().optional(),
}).strict()
const audioCalibrationResultSchema = z.object({
  duration_seconds: z.number(),
  samples: z.number(),
  measured_floor: z.number(),
  measured_peak: z.number(),
  recommended_noise_floor: z.number(),
  recommended_target_level: z.number(),
  recommended_hardware_gain: z.number().nullable().optional(),
  recommended_idle_threshold_scale: z.number(),
  recommended_input_noise_floor: z.number().min(0).max(0.5).default(0.003),
  measured_music_level: z.number().min(0).max(1).default(0),
  clipping_fraction: z.number().min(0).max(1).default(0),
}).strict()
const audioCalibrationSchema = z.object({
  session_id: z.string(),
  phase: z.enum(["quiet", "waiting_music", "music"]).default("quiet"),
  status: z.string(),
  elapsed_seconds: z.number(),
  remaining_seconds: z.number(),
  result: audioCalibrationResultSchema.nullable().optional(),
  error: z.string().nullable().optional(),
}).strict()
const startupSettingsSchema = z.object({
  restore_last_state: z.boolean(),
  remembered: z.object({
    mode: playbackModeSchema,
    solid_color: z.string(),
    animation: z.string(),
    brightness: z.number(),
    blackout: z.boolean(),
  }).strict(),
}).strict()
const identitySettingsSchema = z.object({
  application_name: z.string(),
  spotify_device_name: z.string(),
  spotify_restart_requested: z.boolean(),
}).strict()
const updateStatusSchema = z.object({
  supported: z.boolean(),
  installed_commit: z.string().nullable(),
  available_commit: z.string().nullable(),
  update_available: z.boolean(),
  working_tree_dirty: z.boolean(),
  checked_at: z.string().nullable(),
  check_error: z.string().nullable(),
  install_state: z.enum(["idle", "running", "success", "failed"]),
  install_started_at: z.string().nullable(),
  install_finished_at: z.string().nullable(),
  install_error: z.string().nullable(),
}).strict() satisfies z.ZodType<UpdateStatus>
const systemStatsSchema = z.object({
  cpu_temperature_c: z.number().nullable(),
  cpu_usage_percent: z.number().nullable(),
  memory_used_bytes: z.number().int().nullable(),
  memory_total_bytes: z.number().int().nullable(),
  memory_usage_percent: z.number().nullable(),
  disk_used_bytes: z.number().int().nullable(),
  disk_total_bytes: z.number().int().nullable(),
  disk_usage_percent: z.number().nullable(),
  uptime_seconds: z.number().nullable(),
  sampled_at: z.string(),
}).strict() satisfies z.ZodType<SystemStats>
const calibrationSessionSchema = z.object({
  session_id: z.string(),
  state: dashboardStateSchema,
}).strict()

/** Strict validation for the audio telemetry WebSocket payload. */
const audioTelemetrySchema = z.object({
  sequence: z.number().int(),
  fresh: z.boolean(),
  input_level: z.number(),
  input_peak: z.number().min(0).max(1).default(0),
  clipping_fraction: z.number().min(0).max(1).default(0),
  microphone_gate_open: z.boolean().default(false),
  configured_input_noise_floor: z.number().min(0).max(0.5).nullable().default(null),
  processed_level: z.number(),
  bands: z.tuple([
    z.number(), z.number(), z.number(), z.number(),
    z.number(), z.number(), z.number(), z.number(),
  ]),
  beat: z.boolean(),
  beat_strength: z.number(),
  bpm: z.number(),
  bpm_confidence: z.number().min(0).max(1).optional().default(0),
  beat_phase: z.number().min(0).max(1).optional().default(0),
  beat_index: z.number().int().optional().default(0),
  next_beat_in_ms: z.number().nonnegative().nullable().optional().default(null),
  beat_event_sequence: z.number().int().optional().default(0),
  dropped_beat_events: z.number().int().nonnegative().optional().default(0),
  pending_beat_events: z.number().int().nonnegative().optional().default(0),
  beat_scheduler_late_ticks_dropped: z.number().int().nonnegative().optional().default(0),
  beat_scheduler_late_ticks_coalesced: z.number().int().nonnegative().optional().default(0),
  beat_scheduler_lateness_p95_ms: z.number().nonnegative().optional().default(0),
  calibration_output_callback_count: z.number().int().nonnegative().optional().default(0),
  calibration_output_sample_rate: z.number().nonnegative().optional().default(0),
  calibration_output_block_duration_ms: z.number().nonnegative().optional().default(0),
  calibration_output_callback_p50_ms: z.number().nonnegative().optional().default(0),
  calibration_output_callback_p95_ms: z.number().nonnegative().optional().default(0),
  calibration_output_callback_p99_ms: z.number().nonnegative().optional().default(0),
  calibration_output_interval_jitter_p95_ms: z.number().nonnegative().optional().default(0),
  calibration_output_underflow_count: z.number().int().nonnegative().optional().default(0),
  calibration_output_overflow_count: z.number().int().nonnegative().optional().default(0),
  calibration_output_callback_error_count: z.number().int().nonnegative().optional().default(0),
  sync_offset_ms: z.number().min(-500).max(500).optional().default(0),
  sync_route: z.string().optional().default("mic|unknown|default"),
  estimated_noise_floor: z.number(),
  configured_noise_floor: z.number(),
  normalization_gain: z.number(),
  hardware_gain_value: z.number().nullable().optional(),
  program_loudness: z.number(),
  musical_impact: z.number(),
  gate: z.string(),
  gate_preview: z.boolean(),
  gate_energy: z.number(),
  gate_onset: z.number(),
  gate_beat_density: z.number(),
  gate_brightness: z.number(),
  gate_spectral_balance: z.number().optional(),
  gate_reason: z.string().optional(),
  gate_checks: z.array(z.object({
    id: z.string(),
    label: z.string(),
    value: z.number(),
    threshold: z.number(),
    passed: z.boolean(),
  }).strict()).optional(),
  music_structure_phase: z.enum(["idle", "breakdown", "groove", "buildup", "drop"]).optional().default("idle"),
  music_structure_confidence: z.number().min(0).max(1).optional().default(0),
  music_structure_buildup_progress: z.number().min(0).max(1).optional().default(0),
  music_structure_drop_count: z.number().int().nonnegative().optional().default(0),
  music_structure_active: z.boolean().optional().default(false),
  music_structure_fresh: z.boolean().optional().default(false),
  drop_accent_status: z.enum(["idle", "pending", "activated", "skipped"]).optional().default("idle"),
  drop_accent_reason: z.string().nullable().optional().default(null),
  health: z.string(),
}).strict()

export function parseAudioTelemetry(value: unknown): AudioTelemetry {
  return audioTelemetrySchema.parse(value)
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

type ResponseParser<T> = z.ZodType<T> | ((value: unknown) => T)

async function request<T>(
  path: string,
  init: RequestInit | undefined,
  parser: ResponseParser<T>,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: string; error?: string }
      message = body.detail ?? body.error ?? message
    } catch {
      // Keep the status-based fallback when the server does not return JSON.
    }
    if (response.status === 401) {
      window.dispatchEvent(new Event(ACCESS_REVOKED_EVENT))
    }
    throw new ApiError(message, response.status)
  }

  const body = await response.json()
  return typeof parser === "function" ? parser(body) : parser.parse(body)
}

export const dashboardApi = {
  getState: () => request("/api/state", undefined, dashboardStateSchema),
  getSystemStats: () => request("/api/system/stats", undefined, systemStatsSchema),
  getUpdateStatus: () => request("/api/system/update", undefined, updateStatusSchema),
  checkForUpdates: () => request("/api/system/update/check", { method: "POST" }, updateStatusSchema),
  installUpdate: () => request("/api/system/update/install", { method: "POST" }, updateStatusSchema),
  getAnimations: async () => (await request("/api/animations", undefined, animationListSchema)).items,
  getAnimationSettings: (animation: string, stripeId?: string) => {
    const suffix = stripeId ? `?stripe_id=${encodeURIComponent(stripeId)}` : ""
    return request(`/api/animations/${encodeURIComponent(animation)}/settings${suffix}`, undefined, animationSettingsResponseSchema)
  },
  setAnimationSettings: (
    animation: string,
    values: Record<string, string | number | boolean>,
    stripeId?: string,
  ) => request(`/api/animations/${encodeURIComponent(animation)}/settings`, {
    method: "PUT",
    body: JSON.stringify({ values, ...(stripeId ? { stripe_id: stripeId } : {}) }),
  }, animationSettingsResponseSchema),
  resetAnimationSettings: (animation: string, stripeId?: string) => {
    const suffix = stripeId ? `?stripe_id=${encodeURIComponent(stripeId)}` : ""
    return request(`/api/animations/${encodeURIComponent(animation)}/settings/reset${suffix}`, {
      method: "POST",
    }, animationSettingsResponseSchema)
  },
  getAnimationSequences: () => request("/api/sequencer/sequences", undefined, animationSequenceLibrarySchema),
  createAnimationSequence: (sequence: SequenceCreateRequest) => request("/api/sequencer/sequences", {
    method: "POST",
    body: JSON.stringify(sequence),
  }, dashboardStateSchema),
  updateAnimationSequence: (sequenceId: string, sequence: SequenceDefinitionRequest) => request(`/api/sequencer/sequences/${encodeURIComponent(sequenceId)}`, {
    method: "PUT",
    body: JSON.stringify(sequence),
  }, dashboardStateSchema),
  deleteAnimationSequence: (sequenceId: string) => request(`/api/sequencer/sequences/${encodeURIComponent(sequenceId)}`, {
    method: "DELETE",
  }, dashboardStateSchema),
  setActiveAnimationSequence: (sequenceId: string | null) => request("/api/sequencer/active", {
    method: "PUT",
    body: JSON.stringify({ sequence_id: sequenceId }),
  }, dashboardStateSchema),
  setMode: (mode: PlaybackMode, color?: string, stripeId?: string, musicRecognitionEnabled?: boolean) =>
    request("/api/mode", {
      method: "PUT",
      body: JSON.stringify({
        mode,
        ...(color === undefined ? {} : { color }),
        ...(stripeId ? { stripe_id: stripeId } : {}),
        ...(musicRecognitionEnabled === undefined ? {} : { music_recognition_enabled: musicRecognitionEnabled }),
      }),
    }, dashboardStateSchema),
  setMusicIdle: (idle: { mode: "off" | "solid" | "animation"; color: string; animation: string; brightness: number }, stripeId?: string) =>
    request("/api/music-idle", { method: "PUT", body: JSON.stringify({ ...idle, ...(stripeId ? { stripe_id: stripeId } : {}) }) }, dashboardStateSchema),
  setBrightness: (brightness: number, stripeId?: string) =>
    request("/api/brightness", {
      method: "PUT",
      body: JSON.stringify({ brightness, ...(stripeId ? { stripe_id: stripeId } : {}) }),
    }, dashboardStateSchema),
  selectAnimation: (name: string, stripeId?: string) =>
    request("/api/animation", {
      method: "PUT",
      body: JSON.stringify({ name, ...(stripeId ? { stripe_id: stripeId } : {}) }),
    }, dashboardStateSchema),
  setBlackout: (enabled: boolean, stripeId?: string) =>
    request("/api/blackout", {
      method: "POST",
      body: JSON.stringify({ enabled, ...(stripeId ? { stripe_id: stripeId } : {}) }),
    }, dashboardStateSchema),
  getStripes: () => request("/api/stripes", undefined, stripeTopologySchema),
  updateStripes: (topology: StripeTopology) =>
    request("/api/stripes", {
      method: "PUT",
      body: JSON.stringify(topology),
    }, dashboardStateSchema),
  testStripe: (stripeId: string, pattern: "identify" | "red" | "green" | "blue" | "white", topology?: StripeTopology) =>
    request("/api/stripes/test", {
      method: "POST",
      body: JSON.stringify({ stripe_id: stripeId, pattern, ...(topology ? { topology } : {}) }),
    }, dashboardStateSchema),
  startCalibration: (outputIndex: number) =>
    request("/api/calibration/session", {
      method: "POST",
      body: JSON.stringify({ output_index: outputIndex }),
    }, calibrationSessionSchema),
  updateCalibration: (
    sessionId: string,
    correction: { red: number; green: number; blue: number },
    pattern: CalibrationPattern,
  ) => request(`/api/calibration/session/${sessionId}`, {
    method: "PUT",
    body: JSON.stringify({ ...correction, pattern }),
  }, dashboardStateSchema),
  finishCalibration: (sessionId: string, save: boolean) =>
    request(`/api/calibration/session/${sessionId}/finish`, {
      method: "POST",
      body: JSON.stringify({ save }),
    }, dashboardStateSchema),
  getAudioSettings: (signal?: AbortSignal) => request("/api/audio/settings", signal ? { signal } : undefined, audioSettingsSchema),
  getSequencerSettings: () => request("/api/sequencer/settings", undefined, sequencerSettingsSchema),
  setSequencerSettings: (switchQuantization: SwitchQuantization, barsPerPhrase: 4 | 8 | 16) =>
    request("/api/sequencer/settings", {
      method: "PUT",
      body: JSON.stringify({
        switch_quantization: switchQuantization,
        bars_per_phrase: barsPerPhrase,
      }),
    }, dashboardStateSchema),
  getTransitionSettings: () => request("/api/transitions/settings", undefined, transitionSettingsSchema),
  setTransitionSettings: (enabled: boolean, durationMs: number, effect: TransitionEffect) => {
    const body: TransitionSettingsRequest = { enabled, duration_ms: durationMs, effect }
    return request("/api/transitions/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }, dashboardStateSchema)
  },
  setAudioSyncOffset: (offsetMs: number) =>
    request("/api/audio/sync-offset", {
      method: "PUT",
      body: JSON.stringify({ offset_ms: offsetMs }),
    }, audioSettingsSchema),
  setAudioSyncCalibration: (active: boolean) =>
    request("/api/audio/sync-calibration", {
      method: "POST",
      body: JSON.stringify({ active }),
    }, z.object({
      start_at_ms: z.number().int().nullable().optional().default(null),
      sync_route: z.string(),
      sync_offset_ms: z.number().min(-500).max(500),
    }).strict()),
  setAudioSource: (source: "auto" | "off" | "demo" | "mic" | "bluetooth" | "spotify") =>
    request("/api/audio/source", {
      method: "PUT",
      body: JSON.stringify({ source }),
    }, audioSettingsSchema),
  getSpotifyStatus: (signal?: AbortSignal) => request("/api/audio/spotify", signal ? { signal } : undefined, spotifyStatusSchema),
  controlSpotify: (action: SpotifyControlAction, value?: number | boolean | string) =>
    request("/api/audio/spotify/control", {
      method: "POST",
      body: JSON.stringify({ action, ...(value === undefined ? {} : { value }) }),
    }, spotifyStatusSchema),
  getBluetoothStatus: (signal?: AbortSignal) => request("/api/audio/bluetooth", signal ? { signal } : undefined, parseBluetoothStatus),
  setBluetoothPower: (powered: boolean) => request("/api/audio/bluetooth/power", { method: "PUT", body: JSON.stringify({ powered }) }, parseBluetoothStatus),
  setBluetoothAlias: (alias: string) => request("/api/audio/bluetooth/alias", { method: "PUT", body: JSON.stringify({ alias }) }, parseBluetoothStatus),
  scanBluetooth: () => request("/api/audio/bluetooth/scan", { method: "POST" }, parseBluetoothStatus),
  pairBluetooth: (address: string) => request("/api/audio/bluetooth/pair", { method: "POST", body: JSON.stringify({ address }) }, parseBluetoothStatus),
  connectBluetooth: (address: string, role?: BluetoothDeviceRole) => request("/api/audio/bluetooth/connect", { method: "POST", body: JSON.stringify({ address, ...(role ? { role } : {}) }) }, parseBluetoothStatus),
  disconnectBluetooth: (address: string) => request("/api/audio/bluetooth/disconnect", { method: "POST", body: JSON.stringify({ address }) }, parseBluetoothStatus),
  forgetBluetooth: (address: string) => request("/api/audio/bluetooth/forget", { method: "POST", body: JSON.stringify({ address }) }, parseBluetoothStatus),
  getNetworkStatus: (signal?: AbortSignal) => request("/api/network/status", signal ? { signal } : undefined, networkStatusSchema) as Promise<NetworkStatusResponse>,
  scanWifi: (interfaceName: string) => request("/api/network/scan", {
    method: "POST",
    body: JSON.stringify({ interface: interfaceName }),
  }, wifiScanSchema) as Promise<WifiScanResponse>,
  connectWifi: (interfaceName: string, ssid: string, password: string) => request("/api/network/connect", {
    method: "POST",
    body: JSON.stringify({ interface: interfaceName, ssid, password }),
  }, wifiConnectSchema) as Promise<WifiConnectResponse>,
  setAudioOutput: (selector: string) => request("/api/audio/output", { method: "PUT", body: JSON.stringify({ selector }) }, parseBluetoothStatus),
  setAudioOutputVolume: (selector: string, volume: number) => request("/api/audio/output/volume", { method: "PUT", body: JSON.stringify({ selector, volume }) }, parseBluetoothStatus),
  setAudioOutputMute: (selector: string, muted: boolean) => request("/api/audio/output/mute", { method: "PUT", body: JSON.stringify({ selector, muted }) }, parseBluetoothStatus),
  selectAudioDevice: (device: string) =>
    request("/api/audio/device", {
      method: "PUT",
      body: JSON.stringify({ device }),
    }, audioSettingsSchema),
  updateAudioSettings: (device: string, settings: Omit<AudioTuningValues, "noise_floor"> & { noise_floor?: number }) =>
    request("/api/audio/settings", {
      method: "PUT",
      body: JSON.stringify({
        device,
        settings: { ...settings, noise_floor: settings.noise_floor ?? 0.015 },
      }),
    }, audioSettingsSchema),
  resetAudioSettings: (device: string) =>
    request("/api/audio/settings/reset", {
      method: "POST",
      body: JSON.stringify({ device }),
    }, audioSettingsSchema),
  startAudioCalibration: (device: string, durationSeconds = 8, twoStep = false) =>
    request("/api/audio/calibration/session", {
      method: "POST",
      body: JSON.stringify({ device, duration_seconds: durationSeconds, two_step: twoStep }),
    }, audioCalibrationSchema),
  beginAudioCalibrationMusic: (sessionId: string) => request(`/api/audio/calibration/session/${sessionId}/music`, { method: "POST" }, audioCalibrationSchema),
  getAudioCalibration: (sessionId: string) => request(`/api/audio/calibration/session/${sessionId}`, undefined, audioCalibrationSchema),
  finishAudioCalibration: (sessionId: string, apply: boolean, values?: { target_level?: number; noise_floor?: number }) =>
    request(`/api/audio/calibration/session/${sessionId}/finish`, {
      method: "POST",
      body: JSON.stringify({ apply, ...values }),
    }, audioSettingsSchema),
  getStartupSettings: () => request("/api/startup", undefined, startupSettingsSchema),
  updateStartupSettings: (restoreLastState: boolean) =>
    request("/api/startup", {
      method: "PUT",
      body: JSON.stringify({ restore_last_state: restoreLastState }),
    }, startupSettingsSchema),
  getIdentitySettings: () => request("/api/identity", undefined, identitySettingsSchema),
  updateIdentitySettings: (applicationName: string, spotifyDeviceName: string) =>
    request("/api/identity", {
      method: "PUT",
      body: JSON.stringify({ application_name: applicationName, spotify_device_name: spotifyDeviceName }),
    }, identitySettingsSchema),
}

export const accessApi = {
  status: () => request("/api/auth/status", undefined, accessStatusSchema),
  pair: (code: string) =>
    request("/api/auth/pair", {
      method: "POST",
      body: JSON.stringify({ code }),
    }, accessStatusSchema),
  logout: () => request("/api/auth/logout", { method: "POST" }, accessStatusSchema),
}

export function websocketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}/ws/state`
}

export function audioWebsocketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}/ws/audio`
}

export function previewWebsocketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}/ws/preview`
}
