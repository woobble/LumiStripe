export type PlaybackMode = "solid" | "static" | "cycling" | "dynamic"
export type RuntimeKind = "simulation" | "hardware"
export const ACCESS_REVOKED_EVENT = "lumistripe:access-revoked"

export interface AccessStatus {
  required: boolean
  authenticated: boolean
}

export interface DiagnosticIssue {
  severity: "info" | "warning" | "critical"
  title: string
  message: string
  action: string
}

export type CalibrationPattern = "white" | "red" | "green" | "blue"

export interface ColorCorrectionProfile {
  output_index: number
  name: string
  device: string
  red: number
  green: number
  blue: number
}

export interface CalibrationStatus {
  active: boolean
  output_index: number | null
  pattern: CalibrationPattern | null
  expires_in_seconds: number | null
}

export type StripeLayout = "mirrored" | "continuous" | "independent"
export type StripeBackend = "spi" | "gpio"

export interface StripeOutputConfig {
  id: string
  name: string
  pixels: number
  backend: StripeBackend
  reversed: boolean
  spi_device: string
  spi_speed_hz: number
  chip: string
  data_pin: number
  clock_pin: number
  last_output_at?: string | null
  error?: string | null
}

export interface StripeTopology {
  layout: StripeLayout
  outputs: StripeOutputConfig[]
}

export interface StripePlaybackState {
  stripe_id: string
  mode: PlaybackMode
  solid_color: string
  animation: string
  brightness: number
  blackout: boolean
  music_active: boolean
  music_recognition_enabled?: boolean
}

export interface DashboardState {
  revision: number
  runtime: RuntimeKind
  output_backend: string
  output_devices: string[]
  spi_speed_hz: number | null
  running: boolean
  mode: PlaybackMode
  solid_color: string
  animation: string
  brightness: number
  blackout: boolean
  music_active: boolean
  music_recognition_enabled?: boolean
  music_gate: string
  bpm: number
  audio_status: string
  active_effects: string[]
  uptime_seconds: number
  frame_rate: number
  audio_health: string
  audio_callback_age_seconds: number | null
  audio_frame_age_seconds: number | null
  last_output_at: string | null
  last_output_age_seconds: number | null
  application_version: string
  color_corrections: ColorCorrectionProfile[]
  calibration: CalibrationStatus
  stripe_topology: StripeTopology
  stripe_playback: StripePlaybackState[]
  diagnostic_issues: DiagnosticIssue[]
  error: string | null
}

export interface AnimationOption {
  name: string
  mood: string
  dynamic_safe: boolean
}

interface AnimationList {
  items: AnimationOption[]
}

export interface CalibrationSessionResponse {
  session_id: string
  state: DashboardState
}

export interface AudioTuningValues {
  target_level: number
  hardware_gain_target?: number | null
  noise_floor?: number
  dynamic_response: number
  rms_attack: number
  rms_release: number
  band_attack: number
  band_release: number
  beat_release: number
  energy_threshold: number
  onset_threshold: number
  beat_density_threshold: number
  brightness_threshold: number
  spectral_balance_ratio: number
}

export interface AudioDeviceOption {
  selector: string
  name: string
  settings: AudioTuningValues
}

export type BluetoothDeviceRole = "input" | "output"

export interface AudioOutputDeviceInfo {
  selector: string
  name: string
  volume: number | null
  muted: boolean
  bluetooth: boolean
  connected: boolean
}

export interface BluetoothDeviceInfo {
  address: string
  name: string
  paired: boolean
  connected: boolean
  roles: BluetoothDeviceRole[]
}

export interface BluetoothStatusResponse {
  available: boolean
  powered: boolean
  adapter_alias: string | null
  scanning: boolean
  streaming: boolean
  devices: BluetoothDeviceInfo[]
  connected_inputs: BluetoothDeviceInfo[]
  connected_outputs: BluetoothDeviceInfo[]
  connected_device: BluetoothDeviceInfo | null
  input_source: string | null
  output_devices: AudioOutputDeviceInfo[]
  default_sink: string | null
  output_volume: number | null
  output_muted: boolean
  output_ready: boolean
  operation: string | null
  error: string | null
}

export interface AudioSettingsResponse {
  source: string
  active_source?: string
  monitoring: boolean
  active_device: string | null
  fallback_device?: string | null
  active_device_name: string | null
  devices: AudioDeviceOption[]
  settings: AudioTuningValues
  configured_noise_floor: number
  hardware_gain_supported?: boolean
  hardware_gain_writable?: boolean
  hardware_gain_backend?: string | null
  hardware_gain_control?: string | null
  hardware_gain_value?: number | null
  hardware_gain_error?: string | null
  bluetooth?: BluetoothStatusResponse
  error: string | null
}

export interface AudioCalibrationResult {
  duration_seconds: number
  samples: number
  measured_floor: number
  measured_peak: number
  recommended_noise_floor: number
  recommended_target_level: number
  recommended_hardware_gain?: number | null
  recommended_idle_threshold_scale: number
}

export interface AudioCalibrationSessionResponse {
  session_id: string
  status: "capturing" | "complete"
  elapsed_seconds: number
  remaining_seconds: number
  result: AudioCalibrationResult | null
  error: string | null
}

export interface StartupPlaybackState {
  mode: PlaybackMode
  solid_color: string
  animation: string
  brightness: number
  blackout: boolean
}

export interface StartupSettingsResponse {
  restore_last_state: boolean
  remembered: StartupPlaybackState
}

export interface AudioTelemetry {
  sequence: number
  fresh: boolean
  input_level: number
  processed_level: number
  bands: [number, number, number, number, number, number, number, number]
  beat: boolean
  beat_strength: number
  bpm: number
  estimated_noise_floor: number
  configured_noise_floor: number
  normalization_gain: number
  hardware_gain_value?: number | null
  program_loudness: number
  musical_impact: number
  gate: string
  gate_preview: boolean
  gate_energy: number
  gate_onset: number
  gate_beat_density: number
  gate_brightness: number
  gate_spectral_balance?: number
  gate_reason?: string
  gate_checks?: Array<{ id: string; label: string; value: number; threshold: number; passed: boolean }>
  health: string
}

const playbackModeSchema = z.enum(["solid", "static", "cycling", "dynamic"])
const stripeOutputSchema = z.object({
  id: z.string(), name: z.string(), pixels: z.number(), backend: z.enum(["spi", "gpio"]),
  reversed: z.boolean(), spi_device: z.string(), spi_speed_hz: z.number(), chip: z.string(),
  data_pin: z.number(), clock_pin: z.number(), last_output_at: z.string().nullable().optional(), error: z.string().nullable().optional(),
})
const stripeTopologySchema = z.object({ layout: z.enum(["mirrored", "continuous", "independent"]), outputs: z.array(stripeOutputSchema) }) as z.ZodType<StripeTopology>
const dashboardStateSchema = z.object({
  revision: z.number(), runtime: z.enum(["simulation", "hardware"]), running: z.boolean(),
  mode: playbackModeSchema, solid_color: z.string(), animation: z.string(), brightness: z.number(),
  stripe_topology: stripeTopologySchema, stripe_playback: z.array(z.unknown()),
}).passthrough() as unknown as z.ZodType<DashboardState>
const animationListSchema = z.object({ items: z.array(z.object({ name: z.string(), mood: z.string(), dynamic_safe: z.boolean() })) }) as unknown as z.ZodType<AnimationList>
const accessStatusSchema = z.object({ required: z.boolean(), authenticated: z.boolean() }) as unknown as z.ZodType<AccessStatus>
const bluetoothDeviceSchema = z.object({ address: z.string(), name: z.string(), paired: z.boolean(), connected: z.boolean(), roles: z.array(z.enum(["input", "output"])) })
const audioOutputDeviceSchema = z.object({ selector: z.string(), name: z.string(), volume: z.number().nullable(), muted: z.boolean(), bluetooth: z.boolean(), connected: z.boolean() })
const bluetoothStatusSchema = z.object({ available: z.boolean(), powered: z.boolean(), adapter_alias: z.string().nullable(), scanning: z.boolean(), streaming: z.boolean(), devices: z.array(bluetoothDeviceSchema), connected_inputs: z.array(bluetoothDeviceSchema), connected_outputs: z.array(bluetoothDeviceSchema), connected_device: bluetoothDeviceSchema.nullable(), input_source: z.string().nullable(), output_devices: z.array(audioOutputDeviceSchema), default_sink: z.string().nullable(), output_volume: z.number().nullable(), output_muted: z.boolean(), output_ready: z.boolean(), operation: z.string().nullable(), error: z.string().nullable() })
const audioSettingsSchema = z.object({ source: z.string(), active_source: z.string().optional(), monitoring: z.boolean(), active_device: z.string().nullable(), fallback_device: z.string().nullable().optional(), active_device_name: z.string().nullable(), devices: z.array(z.unknown()), settings: z.object({ target_level: z.number(), hardware_gain_target: z.number().nullable().optional() }).passthrough(), configured_noise_floor: z.number(), bluetooth: bluetoothStatusSchema.optional(), error: z.string().nullable() }).passthrough() as unknown as z.ZodType<AudioSettingsResponse>
const audioCalibrationSchema = z.object({ session_id: z.string(), status: z.enum(["capturing", "complete"]), elapsed_seconds: z.number(), remaining_seconds: z.number(), result: z.object({ duration_seconds: z.number(), samples: z.number(), measured_floor: z.number(), measured_peak: z.number(), recommended_noise_floor: z.number(), recommended_target_level: z.number(), recommended_hardware_gain: z.number().nullable().optional(), recommended_idle_threshold_scale: z.number() }).nullable(), error: z.string().nullable() }) as z.ZodType<AudioCalibrationSessionResponse>
const startupSettingsSchema = z.object({ restore_last_state: z.boolean(), remembered: z.object({ mode: playbackModeSchema }).passthrough() }).passthrough() as unknown as z.ZodType<StartupSettingsResponse>
const calibrationSessionSchema = z.object({ session_id: z.string(), state: dashboardStateSchema }) as unknown as z.ZodType<CalibrationSessionResponse>

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit | undefined, schema: z.ZodType<T>): Promise<T> {
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

  return schema.parse(await response.json())
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
  getBluetoothStatus: () => request("/api/audio/bluetooth", undefined, bluetoothStatusSchema),
  setBluetoothPower: (powered: boolean) => request("/api/audio/bluetooth/power", { method: "PUT", body: JSON.stringify({ powered }) }, bluetoothStatusSchema),
  setBluetoothAlias: (alias: string) => request("/api/audio/bluetooth/alias", { method: "PUT", body: JSON.stringify({ alias }) }, bluetoothStatusSchema),
  scanBluetooth: () => request("/api/audio/bluetooth/scan", { method: "POST" }, bluetoothStatusSchema),
  pairBluetooth: (address: string) => request("/api/audio/bluetooth/pair", { method: "POST", body: JSON.stringify({ address }) }, bluetoothStatusSchema),
  connectBluetooth: (address: string, role?: BluetoothDeviceRole) => request("/api/audio/bluetooth/connect", { method: "POST", body: JSON.stringify({ address, ...(role ? { role } : {}) }) }, bluetoothStatusSchema),
  forgetBluetooth: (address: string) => request("/api/audio/bluetooth/forget", { method: "POST", body: JSON.stringify({ address }) }, bluetoothStatusSchema),
  setAudioOutput: (selector: string) => request("/api/audio/output", { method: "PUT", body: JSON.stringify({ selector }) }, bluetoothStatusSchema),
  setAudioOutputVolume: (selector: string, volume: number) => request("/api/audio/output/volume", { method: "PUT", body: JSON.stringify({ selector, volume }) }, bluetoothStatusSchema),
  setAudioOutputMute: (selector: string, muted: boolean) => request("/api/audio/output/mute", { method: "PUT", body: JSON.stringify({ selector, muted }) }, bluetoothStatusSchema),
  selectAudioDevice: (device: string) =>
    request("/api/audio/device", {
      method: "PUT",
      body: JSON.stringify({ device }),
    }, audioSettingsSchema),
  updateAudioSettings: (device: string, settings: AudioTuningValues) =>
    request("/api/audio/settings", {
      method: "PUT",
      body: JSON.stringify({ device, settings }),
    }, audioSettingsSchema),
  resetAudioSettings: (device: string) =>
    request("/api/audio/settings/reset", {
      method: "POST",
      body: JSON.stringify({ device }),
    }, audioSettingsSchema),
  startAudioCalibration: (device: string, durationSeconds = 8) =>
    request("/api/audio/calibration/session", { method: "POST", body: JSON.stringify({ device, duration_seconds: durationSeconds }) }, audioCalibrationSchema),
  getAudioCalibration: (sessionId: string) => request(`/api/audio/calibration/session/${sessionId}`, undefined, audioCalibrationSchema),
  finishAudioCalibration: (sessionId: string, apply: boolean, values?: { target_level?: number; noise_floor?: number }) =>
    request(`/api/audio/calibration/session/${sessionId}/finish`, { method: "POST", body: JSON.stringify({ apply, ...values }) }, audioSettingsSchema),
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
  logout: () =>
    request("/api/auth/logout", {
      method: "POST",
    }, accessStatusSchema),
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
import { z } from "zod"
