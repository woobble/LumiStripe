import { beforeEach, describe, expect, it, vi } from "vitest"

import { dashboardApi, websocketUrl } from "@/lib/api"
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
})
