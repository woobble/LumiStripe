// Generated REST types are re-exported through this feature-facing module.

import type { components, paths } from "@/generated/api"

export type RestPaths = paths
export type RestSchema<Name extends keyof components["schemas"]> = components["schemas"][Name]

export type AccessStatus = RestSchema<"AccessStatus">
export type AnimationList = RestSchema<"AnimationList">
export type AnimationOption = RestSchema<"AnimationOption">
export type AudioCalibrationResult = RestSchema<"AudioCalibrationResult">
export type AudioCalibrationSessionResponse = RestSchema<"AudioCalibrationSessionResponse">
export type AudioDeviceOption = RestSchema<"AudioDeviceOption">
export type AudioOutputDeviceInfo = RestSchema<"AudioOutputDeviceInfo">
export type AudioSettingsResponse = RestSchema<"AudioSettingsResponse">
// WebSocket payloads are not represented by OpenAPI components.
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
export type AudioTuningValues = RestSchema<"AudioTuningValues">
export type BluetoothCapabilities = RestSchema<"BluetoothCapabilities">
export type BluetoothDeviceInfo = RestSchema<"BluetoothDeviceInfo">
export type BluetoothStatusResponse = RestSchema<"BluetoothStatusResponse">
export type CalibrationSessionResponse = RestSchema<"CalibrationSessionResponse">
export type CalibrationStatus = RestSchema<"CalibrationStatus">
export type ColorCorrectionProfile = RestSchema<"ColorCorrectionProfile">
export type DashboardState = RestSchema<"DashboardState">
export type DiagnosticIssue = RestSchema<"DiagnosticIssue">
export type PowerBudgetState = RestSchema<"PowerBudgetState">
export type PowerOutputState = RestSchema<"PowerOutputState">
export type StartupPlaybackState = RestSchema<"StartupPlaybackState">
export type StartupSettingsResponse = RestSchema<"StartupSettingsResponse">
export type StripeOutputConfig = RestSchema<"StripeOutputConfig">
export type StripePlaybackState = RestSchema<"StripePlaybackState">
export type StripeTopology = RestSchema<"StripeTopology">

export type PlaybackMode = RestSchema<"ModeRequest">["mode"]
export type CalibrationPattern = RestSchema<"CalibrationUpdateRequest">["pattern"]
export type StripeLayout = RestSchema<"StripeTopology">["layout"]
export type StripeBackend = RestSchema<"StripeOutputConfig">["backend"]
export type BluetoothDeviceRole = RestSchema<"BluetoothDeviceRequest">["role"]
export type BluetoothOperation = RestSchema<"BluetoothCapabilities">["operations"][number]
export type AudioSource = RestSchema<"AudioSourceRequest">["source"]
