import type { AnimationOption, DashboardState } from "@/lib/api"

export const initialState: DashboardState = {
  revision: 1,
  runtime: "simulation",
  output_backend: "simulation",
  output_devices: [],
  spi_speed_hz: null,
  running: true,
  mode: "static",
  solid_color: "#7C3AED",
  animation: "aurora_wave",
  brightness: 0.72,
  blackout: false,
  music_active: false,
  music_gate: "calm",
  bpm: 0,
  audio_status: "No audio source active.",
  active_effects: [],
  uptime_seconds: 65,
  frame_rate: 20,
  audio_health: "inactive",
  audio_callback_age_seconds: null,
  audio_frame_age_seconds: null,
  last_output_at: "2026-08-07T10:00:00Z",
  last_output_age_seconds: 0.1,
  application_version: "0.1.0",
  color_corrections: [
    { output_index: 0, name: "Primary", device: "Simulation", red: 255, green: 255, blue: 255 },
  ],
  calibration: {
    active: false,
    output_index: null,
    pattern: null,
    expires_in_seconds: null,
  },
  diagnostic_issues: [],
  error: null,
}

export const animations: AnimationOption[] = [
  { name: "aurora_wave", mood: "ambient", dynamic_safe: true },
  { name: "bass_pulse", mood: "bass_heavy", dynamic_safe: true },
  { name: "quiet_stars", mood: "calm", dynamic_safe: false },
]

export function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  )
}
