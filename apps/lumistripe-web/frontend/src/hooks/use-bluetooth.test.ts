import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useBluetooth } from "@/hooks/use-bluetooth"
import { dashboardApi, type BluetoothStatusResponse } from "@/lib/api"

const status: BluetoothStatusResponse = {
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
  capabilities: { operations: ["scan"], max_inputs: 1, max_outputs: 1 },
  operation: null,
  operation_id: null,
  operation_state: "idle",
  error: null,
}

describe("useBluetooth", () => {
  beforeEach(() => {
    vi.spyOn(dashboardApi, "getBluetoothStatus").mockResolvedValue(status)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("updates status from an operation and prevents a second operation while busy", async () => {
    let finish: ((value: BluetoothStatusResponse) => void) | undefined
    const operation = vi.fn(() => new Promise<BluetoothStatusResponse>((resolve) => { finish = resolve }))
    const { result, unmount } = renderHook(() => useBluetooth(status))

    let pending: Promise<BluetoothStatusResponse | null> | undefined
    act(() => {
      pending = result.current.run("scan", operation)
    })

    expect(result.current.busy).toBe(true)
    expect(await result.current.run("scan", operation)).toBeNull()

    const next = { ...status, scanning: true, operation: null, operation_id: "scan-1", operation_state: "complete" as const }
    await act(async () => {
      finish?.(next)
      await pending
    })

    expect(result.current.status).toEqual(next)
    expect(operation).toHaveBeenCalledTimes(1)
    expect(result.current.busy).toBe(false)
    unmount()
  })
})
