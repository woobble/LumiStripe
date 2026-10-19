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
