import { beforeEach, describe, expect, it, vi } from "vitest"

import { dashboardApi, parseAudioTelemetry, websocketUrl } from "@/lib/api"
import { emptyTelemetry } from "@/components/dashboard/audio-telemetry"
import { initialState, jsonResponse } from "@/test/fixtures"

describe("dashboardApi", () => {
  const fetchMock = vi.fn(() => jsonResponse({ ...initialState, revision: 2 }))

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock)
    fetchMock.mockClear()
  })

  it.each([
    ["mode", () => dashboardApi.setMode("dynamic"), "/api/mode", "PUT", { mode: "dynamic" }],
    ["brightness", () => dashboardApi.setBrightness(0.4), "/api/brightness", "PUT", { brightness: 0.4 }],
    ["animation", () => dashboardApi.selectAnimation("bass_pulse"), "/api/animation", "PUT", { name: "bass_pulse" }],
    ["blackout", () => dashboardApi.setBlackout(true), "/api/blackout", "POST", { enabled: true }],
  ])("sends the %s command", async (_name, command, path, method, body) => {
    await command()

    expect(fetchMock).toHaveBeenCalledWith(
      path,
      expect.objectContaining({ method, body: JSON.stringify(body) })
    )
  })

  it("builds the websocket URL from the current origin", () => {
    expect(websocketUrl()).toBe("ws://localhost:3000/ws/state")
  })

  it("reads strictly validated host system stats", async () => {
    const systemStats = {
      cpu_temperature_c: 53.5,
      cpu_usage_percent: 60,
      memory_used_bytes: 768 * 1024,
      memory_total_bytes: 1024 * 1024,
      memory_usage_percent: 75,
      disk_used_bytes: 4 * 1024 ** 3,
      disk_total_bytes: 8 * 1024 ** 3,
      disk_usage_percent: 50,
      uptime_seconds: 1234.5,
      sampled_at: "2026-01-01T00:00:00Z",
    }
    fetchMock.mockReturnValueOnce(jsonResponse(systemStats))

    expect(await dashboardApi.getSystemStats()).toEqual(systemStats)
    expect(fetchMock).toHaveBeenLastCalledWith("/api/system/stats", expect.any(Object))

    fetchMock.mockReturnValueOnce(jsonResponse({ ...systemStats, extra_metric: 1 }))
    await expect(dashboardApi.getSystemStats()).rejects.toThrow()
  })

  it("reads update status and exposes fixed check and install requests", async () => {
    const updateStatus = {
      supported: true,
      installed_commit: "a".repeat(40),
      available_commit: "b".repeat(40),
      update_available: true,
      working_tree_dirty: false,
      checked_at: "2026-01-01T00:00:00Z",
      check_error: null,
      install_state: "idle",
      install_started_at: null,
      install_finished_at: null,
      install_error: null,
    }
    fetchMock.mockReturnValueOnce(jsonResponse(updateStatus))
    expect(await dashboardApi.getUpdateStatus()).toEqual(updateStatus)
    expect(fetchMock).toHaveBeenLastCalledWith("/api/system/update", expect.any(Object))

    fetchMock.mockReturnValueOnce(jsonResponse(updateStatus))
    await dashboardApi.checkForUpdates()
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/system/update/check",
      expect.objectContaining({ method: "POST" }),
    )

    fetchMock.mockReturnValueOnce(jsonResponse({ ...updateStatus, install_state: "running" }))
    await dashboardApi.installUpdate()
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/system/update/install",
      expect.objectContaining({ method: "POST" }),
    )
  })

  it("saves a route sync offset and starts the sync calibration cue", async () => {
    const settings = {
      source: "mic",
      active_source: "mic",
      monitoring: true,
      active_device: "2",
      fallback_device: "2",
      active_device_name: "USB Mic",
      devices: [],
      settings: {
        target_level: 0.36,
        hardware_gain_target: null,
        noise_floor: 0.015,
        input_noise_floor: 0.003,
        dynamic_response: 0.65,
        rms_attack: 0.45,
        rms_release: 0.15,
        band_attack: 0.55,
        band_release: 0.12,
        beat_release: 0.18,
        energy_threshold: 0.1,
        onset_threshold: 0.1,
        beat_density_threshold: 0.15,
        brightness_threshold: 0.2,
        spectral_balance_ratio: 0.1,
      },
      configured_noise_floor: 0.015,
      sync_route: "mic|USB Mic|analog-output",
      sync_offset_ms: 125,
      hardware_gain_supported: false,
      hardware_gain_writable: false,
      hardware_gain_backend: null,
      hardware_gain_control: null,
      hardware_gain_value: null,
      hardware_gain_error: null,
    }
    fetchMock.mockReturnValueOnce(jsonResponse(settings))

    expect((await dashboardApi.setAudioSyncOffset(125)).sync_offset_ms).toBe(125)
    expect(fetchMock).toHaveBeenLastCalledWith("/api/audio/sync-offset", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ offset_ms: 125 }),
    }))

    fetchMock.mockReturnValueOnce(jsonResponse({
      start_at_ms: null,
      sync_route: settings.sync_route,
      sync_offset_ms: 125,
    }))
    expect((await dashboardApi.setAudioSyncCalibration(true)).start_at_ms).toBeNull()
    expect(fetchMock).toHaveBeenLastCalledWith("/api/audio/sync-calibration", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ active: true }),
    }))
  })

  it("normalizes legacy Bluetooth responses without capability metadata", async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      available: true,
      powered: true,
      adapter_alias: "LumiStripe",
      scanning: false,
      streaming: false,
      devices: [],
      connected_inputs: [],
      connected_outputs: [],
      connected_device: null,
      input_source: null,
      output_devices: [],
      default_sink: null,
      output_volume: null,
      output_muted: false,
      output_ready: false,
      operation: null,
      error: null,
    }))

    const status = await dashboardApi.getBluetoothStatus()

    expect(status.capabilities).toEqual({ operations: [], max_inputs: 1, max_outputs: 1 })
    expect(status.operation_id).toBeNull()
    expect(status.operation_state).toBe("idle")
  })

  it("uses explicit defaults for microphone fields from older telemetry", () => {
    const legacy = { ...emptyTelemetry } as Record<string, unknown>
    for (const key of ["input_peak", "clipping_fraction", "microphone_gate_open", "configured_input_noise_floor"]) delete legacy[key]
    const parsed = parseAudioTelemetry(legacy)
    expect(parsed.input_peak).toBe(0)
    expect(parsed.clipping_fraction).toBe(0)
    expect(parsed.microphone_gate_open).toBe(false)
    expect(parsed.configured_input_noise_floor).toBeNull()
    expect(() => parseAudioTelemetry({ ...legacy, clipping_fraction: 2 })).toThrow()
    expect(() => parseAudioTelemetry({ ...legacy, microphone_gate_open: "yes" })).toThrow()
    expect(() => parseAudioTelemetry({ ...legacy, extra_field: 1 })).toThrow()
  })

  it("opts into two-step calibration and advances using the music endpoint", async () => {
    const session = { session_id: "mic", status: "waiting", phase: "waiting_music", elapsed_seconds: 8, remaining_seconds: 0, result: null, error: null }
    fetchMock.mockReturnValueOnce(jsonResponse(session))
    expect((await dashboardApi.startAudioCalibration("PCM2902", 8, true)).phase).toBe("waiting_music")
    expect(fetchMock).toHaveBeenLastCalledWith("/api/audio/calibration/session", expect.objectContaining({
      method: "POST", body: JSON.stringify({ device: "PCM2902", duration_seconds: 8, two_step: true }),
    }))
    fetchMock.mockReturnValueOnce(jsonResponse({ ...session, status: "capturing", phase: "music" }))
    expect((await dashboardApi.beginAudioCalibrationMusic("mic")).phase).toBe("music")
    expect(fetchMock).toHaveBeenLastCalledWith("/api/audio/calibration/session/mic/music", expect.objectContaining({ method: "POST" }))
  })
})
