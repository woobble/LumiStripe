import { z } from "zod"

import type {
  AudioTelemetry,
  AudioTuningValues,
  BluetoothDeviceRole,
  BluetoothStatusResponse,
  CalibrationPattern,
  DashboardState,
  PlaybackMode,
  StripeTopology,
} from "@/lib/api/contracts"

export type {
  AccessStatus,
  AnimationList,
  AnimationOption,
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
  StripeBackend,
  StripeLayout,
  StripeOutputConfig,
  StripePlaybackState,
  StripeTopology,
} from "@/lib/api/contracts"

export const ACCESS_REVOKED_EVENT = "lumistripe:access-revoked"

const playbackModeSchema = z.enum(["solid", "static", "cycling", "dynamic"])
const stripeOutputSchema = z.object({
  id: z.string(),
  name: z.string(),
  pixels: z.number(),
  backend: z.enum(["spi", "gpio"]),
  reversed: z.boolean(),
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
}).strict()
const stripeTopologySchema = z.object({
  layout: z.enum(["mirrored", "continuous", "independent"]),
  outputs: z.array(stripeOutputSchema),
  power_budget_enabled: z.boolean().optional().default(false),
  power_budget_watts: z.number().nullable().optional(),
}).strict()
const stripePlaybackSchema = z.object({
  stripe_id: z.string(),
  mode: playbackModeSchema,
  solid_color: z.string(),
  animation: z.string(),
  brightness: z.number(),
  blackout: z.boolean(),
  music_active: z.boolean(),
  music_recognition_enabled: z.boolean(),
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
  music_gate: z.string(),
  bpm: z.number(),
  audio_status: z.string(),
  active_effects: z.array(z.string()),
  uptime_seconds: z.number(),
  frame_rate: z.number(),
  worker_heartbeat_age_seconds: z.number().nullable().optional(),
  missed_frame_count: z.number().int(),
  command_queue_depth: z.number().int(),
  audio_health: z.string(),
  audio_callback_age_seconds: z.number().nullable().optional(),
  audio_frame_age_seconds: z.number().nullable().optional(),
  last_output_at: z.string().nullable().optional(),
  last_output_age_seconds: z.number().nullable().optional(),
  application_version: z.string(),
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
  }),
  stripe_playback: z.array(stripePlaybackSchema),
  diagnostic_issues: z.array(diagnosticIssueSchema),
  error: z.string().nullable().optional(),
}).strict()

export function parseDashboardState(value: unknown): DashboardState {
  return dashboardStateSchema.parse(value)
}

const animationOptionSchema = z.object({
  name: z.string(),
  mood: z.string(),
  dynamic_safe: z.boolean(),
}).strict()
const animationListSchema = z.object({ items: z.array(animationOptionSchema) }).strict()
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
const audioTuningSchema = z.object({
  target_level: z.number(),
  hardware_gain_target: z.number().nullable().optional(),
  noise_floor: z.number(),
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
  hardware_gain_supported: z.boolean(),
  hardware_gain_writable: z.boolean(),
  hardware_gain_backend: z.string().nullable().optional(),
  hardware_gain_control: z.string().nullable().optional(),
  hardware_gain_value: z.number().nullable().optional(),
  hardware_gain_error: z.string().nullable().optional(),
  bluetooth: bluetoothStatusSchema.optional(),
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
}).strict()
const audioCalibrationSchema = z.object({
  session_id: z.string(),
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
const calibrationSessionSchema = z.object({
  session_id: z.string(),
  state: dashboardStateSchema,
}).strict()

/** Strict validation for the audio telemetry WebSocket payload. */
const audioTelemetrySchema = z.object({
  sequence: z.number().int(),
  fresh: z.boolean(),
  input_level: z.number(),
  processed_level: z.number(),
  bands: z.tuple([
    z.number(), z.number(), z.number(), z.number(),
    z.number(), z.number(), z.number(), z.number(),
  ]),
  beat: z.boolean(),
  beat_strength: z.number(),
  bpm: z.number(),
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
  getAnimations: async () => (await request("/api/animations", undefined, animationListSchema)).items,
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
  getAudioSettings: () => request("/api/audio/settings", undefined, audioSettingsSchema),
  setAudioSource: (source: "auto" | "off" | "demo" | "mic" | "bluetooth") =>
    request("/api/audio/source", {
      method: "PUT",
      body: JSON.stringify({ source }),
    }, audioSettingsSchema),
  getBluetoothStatus: () => request("/api/audio/bluetooth", undefined, parseBluetoothStatus),
  setBluetoothPower: (powered: boolean) => request("/api/audio/bluetooth/power", { method: "PUT", body: JSON.stringify({ powered }) }, parseBluetoothStatus),
  setBluetoothAlias: (alias: string) => request("/api/audio/bluetooth/alias", { method: "PUT", body: JSON.stringify({ alias }) }, parseBluetoothStatus),
  scanBluetooth: () => request("/api/audio/bluetooth/scan", { method: "POST" }, parseBluetoothStatus),
  pairBluetooth: (address: string) => request("/api/audio/bluetooth/pair", { method: "POST", body: JSON.stringify({ address }) }, parseBluetoothStatus),
  connectBluetooth: (address: string, role?: BluetoothDeviceRole) => request("/api/audio/bluetooth/connect", { method: "POST", body: JSON.stringify({ address, ...(role ? { role } : {}) }) }, parseBluetoothStatus),
  disconnectBluetooth: (address: string) => request("/api/audio/bluetooth/disconnect", { method: "POST", body: JSON.stringify({ address }) }, parseBluetoothStatus),
  forgetBluetooth: (address: string) => request("/api/audio/bluetooth/forget", { method: "POST", body: JSON.stringify({ address }) }, parseBluetoothStatus),
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
  startAudioCalibration: (device: string, durationSeconds = 8) =>
    request("/api/audio/calibration/session", {
      method: "POST",
      body: JSON.stringify({ device, duration_seconds: durationSeconds }),
    }, audioCalibrationSchema),
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
