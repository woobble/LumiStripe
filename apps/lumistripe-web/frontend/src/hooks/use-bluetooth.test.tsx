import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return { client, wrapper }
}

afterEach(() => vi.restoreAllMocks())

describe("useBluetooth shared status", () => {
  it("deduplicates status requests across simultaneous consumers", async () => {
    const { client, wrapper } = setup()
    const getStatus = vi.spyOn(dashboardApi, "getBluetoothStatus").mockResolvedValue(status)
    const { result } = renderHook(() => [useBluetooth(), useBluetooth()] as const, { wrapper })

    await waitFor(() => expect(result.current[0].status).toEqual(status))
    expect(getStatus).toHaveBeenCalledTimes(1)
    expect(result.current[1].status).toEqual(status)
    client.clear()
  })

  it("publishes successful operations and prevents another operation while busy", async () => {
    const { client, wrapper } = setup()
    vi.spyOn(dashboardApi, "getBluetoothStatus").mockResolvedValue(status)
    let finish: ((value: BluetoothStatusResponse) => void) | undefined
    const operation = vi.fn(() => new Promise<BluetoothStatusResponse>((resolve) => { finish = resolve }))
    const { result, unmount } = renderHook(() => useBluetooth(status), { wrapper })
    await waitFor(() => expect(result.current.status).toEqual(status))

    let pending: Promise<BluetoothStatusResponse | null> | undefined
    act(() => { pending = result.current.run("scan", operation) })
    expect(result.current.busy).toBe(true)
    expect(await result.current.run("scan", operation)).toBeNull()

    const next = { ...status, scanning: true, operation_id: "scan-1", operation_state: "complete" as const }
    await act(async () => { finish?.(next); await pending })
    expect(result.current.status).toEqual(next)
    expect(operation).toHaveBeenCalledTimes(1)
    expect(result.current.busy).toBe(false)
    unmount()
    client.clear()
  })

  it("does not let a pending poll overwrite the command response", async () => {
    const { client, wrapper } = setup()
    const pendingPoll = deferred<BluetoothStatusResponse>()
    const getStatus = vi.spyOn(dashboardApi, "getBluetoothStatus").mockReturnValue(pendingPoll.promise)
    const { result, unmount } = renderHook(() => useBluetooth(status), { wrapper })
    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(1))

    const confirmed = { ...status, powered: false }
    await act(async () => { await result.current.run("power:off", async () => confirmed) })
    await act(async () => { pendingPoll.resolve({ ...status, powered: true }); await pendingPoll.promise })

    expect(result.current.status).toEqual(confirmed)
    unmount()
    client.clear()
  })

  it("retains the last confirmed status and exposes an operation failure", async () => {
    const { client, wrapper } = setup()
    vi.spyOn(dashboardApi, "getBluetoothStatus").mockResolvedValue(status)
    const { result } = renderHook(() => useBluetooth(status), { wrapper })
    await waitFor(() => expect(result.current.status).toEqual(status))

    await act(async () => {
      await expect(result.current.run("scan", async () => { throw new Error("Adapter is unavailable") })).rejects.toThrow("Adapter is unavailable")
    })
    expect(result.current.status).toEqual(status)
    expect(result.current.error).toBe("Adapter is unavailable")
    client.clear()
  })

  it("keeps cached status through a transient refresh failure and recovers on retry", async () => {
    const { client, wrapper } = setup()
    vi.spyOn(dashboardApi, "getBluetoothStatus")
      .mockResolvedValueOnce(status)
      .mockRejectedValueOnce(new Error("Temporary connection failure"))
      .mockResolvedValueOnce({ ...status, powered: false })
    const { result } = renderHook(() => useBluetooth(status), { wrapper })
    await waitFor(() => expect(result.current.status).toEqual(status))

    await act(async () => { await expect(result.current.refresh()).rejects.toThrow("Temporary connection failure") })
    expect(result.current.status).toEqual(status)
    expect(result.current.error).toBe("Temporary connection failure")

    await act(async () => { await result.current.refresh() })
    expect(result.current.status?.powered).toBe(false)
    expect(result.current.error).toBeNull()
    client.clear()
  })
})
