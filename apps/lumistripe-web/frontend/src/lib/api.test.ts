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
})
