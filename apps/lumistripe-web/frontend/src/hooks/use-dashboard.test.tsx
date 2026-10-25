import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ReactNode } from "react"
import { toast } from "sonner"

import { useDashboard } from "@/hooks/use-dashboard"
import { animations, initialState, jsonResponse } from "@/test/fixtures"

class MockWebSocket {
  static instances: MockWebSocket[] = []
  readonly url: string
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    queueMicrotask(() => this.onopen?.())
  }

  close() {}

  emit(state: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(state) }))
  }

  disconnect() {
    this.onclose?.(new CloseEvent("close", { code: 1006 }))
  }
}

describe("useDashboard", () => {
  function wrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }

  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal("WebSocket", MockWebSocket)
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) => {
        const path = String(input)
        if (path === "/api/animations") return jsonResponse({ items: animations })
        if (path === "/api/sequencer/sequences") return jsonResponse({ sequences: [], active_sequence_id: null })
        return jsonResponse(initialState)
      })
    )
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("loads initial data and applies only current websocket revisions", async () => {
    const { result } = renderHook(() => useDashboard(), { wrapper })
    await waitFor(() => expect(result.current.state?.revision).toBe(1))
    await waitFor(() => expect(result.current.connection).toBe("connected"))

    act(() => MockWebSocket.instances[0].emit({ ...initialState, revision: 4, brightness: 0.25 }))
    await waitFor(() => expect(result.current.state?.brightness).toBe(0.25))

    act(() => MockWebSocket.instances[0].emit({ ...initialState, revision: 2, brightness: 0.9 }))
    expect(result.current.state?.revision).toBe(4)
    expect(result.current.state?.brightness).toBe(0.25)
  })

  it("normalizes websocket states from a backend without power telemetry", async () => {
    const { result } = renderHook(() => useDashboard(), { wrapper })
    await waitFor(() => expect(result.current.state?.revision).toBe(1))

    const { power_budget: _powerBudget, ...legacyState } = initialState
    const {
      shared_supply_enabled: _sharedSupplyEnabled,
      supply_capacity_watts: _supplyCapacityWatts,
      pi_reserve_watts: _piReserveWatts,
      ...legacyTopology
    } = legacyState.stripe_topology
    act(() => MockWebSocket.instances[0].emit({
      ...legacyState,
      stripe_topology: legacyTopology,
      revision: 2,
    }))

    await waitFor(() => expect(result.current.state?.power_budget).toEqual({
      enabled: false,
      budget_watts: null,
      estimated_watts: 0,
      applied_scale: 1,
      limiting_output_id: null,
      outputs: [],
      shared_supply_enabled: false,
      supply_capacity_watts: null,
      pi_reserve_watts: null,
      available_led_watts: null,
    }))
    expect(result.current.state?.stripe_topology.shared_supply_enabled).toBe(false)
    expect(result.current.state?.stripe_topology.supply_capacity_watts).toBe(200)
    expect(result.current.state?.stripe_topology.pi_reserve_watts).toBe(15)
  })

  it("marks a dropped socket as reconnecting and opens a replacement", async () => {
    const { result } = renderHook(() => useDashboard(), { wrapper })
    await waitFor(() => expect(result.current.connection).toBe("connected"))

    vi.useFakeTimers()
    act(() => MockWebSocket.instances[0].disconnect())
    expect(result.current.connection).toBe("reconnecting")

    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it("reports a rejected sequencer update and releases the command state", async () => {
    const toastError = vi.spyOn(toast, "error")
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input)
      if (path === "/api/animations") return jsonResponse({ items: animations })
      if (path === "/api/sequencer/sequences") return jsonResponse({ sequences: [], active_sequence_id: null })
      if (path === "/api/sequencer/settings") {
        return jsonResponse({ detail: "Sequencer settings could not be saved." }, 422)
      }
      return jsonResponse(initialState)
    }))

    const { result } = renderHook(() => useDashboard(), { wrapper })
    await waitFor(() => expect(result.current.state?.revision).toBe(1))

    let applied: boolean | undefined
    await act(async () => {
      applied = await result.current.setSequencerSettings("phrase", 16)
    })

    expect(applied).toBe(false)
    expect(toastError).toHaveBeenCalledWith("Sequencer settings could not be saved.")
    expect(result.current.pendingCommand).toBeNull()
    toastError.mockRestore()
  })

  it("applies transition settings through the dashboard command state", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input)
      if (path === "/api/animations") return jsonResponse({ items: animations })
      if (path === "/api/sequencer/sequences") return jsonResponse({ sequences: [], active_sequence_id: null })
      if (path === "/api/transitions/settings") {
        expect(init?.method).toBe("PUT")
        expect(init?.body).toBe(JSON.stringify({ enabled: false, duration_ms: 900, effect: "wipe" }))
        return jsonResponse({
          ...initialState,
          revision: 2,
          transition_settings: { enabled: false, duration_ms: 900, effect: "wipe" },
        })
      }
      return jsonResponse(initialState)
    })
    vi.stubGlobal("fetch", fetchMock)
    const { result } = renderHook(() => useDashboard(), { wrapper })
    await waitFor(() => expect(result.current.state?.revision).toBe(1))

    let applied: boolean | undefined
    await act(async () => {
      applied = await result.current.setTransitionSettings(false, 900, "wipe")
    })

    expect(applied).toBe(true)
    expect(result.current.pendingCommand).toBeNull()
    expect(result.current.state?.transition_settings).toEqual({
      enabled: false,
      duration_ms: 900,
      effect: "wipe",
    })
  })

  it("runs sequence API mutations through the dashboard command state and refreshes the library", async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const path = String(input)
      if (path === "/api/animations") return jsonResponse({ items: animations })
      if (path === "/api/sequencer/sequences" && init?.method === "POST") return jsonResponse(initialState)
      if (path === "/api/sequencer/sequences") return jsonResponse({ sequences: [], active_sequence_id: null })
      return jsonResponse(initialState)
    })
    vi.stubGlobal("fetch", fetchMock)
    const { result } = renderHook(() => useDashboard(), { wrapper })
    await waitFor(() => expect(result.current.sequenceLibraryLoading).toBe(false))

    let created: boolean | undefined
    await act(async () => {
      created = await result.current.createSequence({
        id: "demo-sequence",
        name: "Demo",
        repeat: true,
        steps: [{ animation_name: "aurora_wave", duration_bars: 1 }],
      })
    })

    expect(created).toBe(true)
    expect(result.current.pendingCommand).toBeNull()
    expect(fetchMock).toHaveBeenCalledWith("/api/sequencer/sequences", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ id: "demo-sequence", name: "Demo", repeat: true, steps: [{ animation_name: "aurora_wave", duration_bars: 1 }] }),
    }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path, init]) => String(path) === "/api/sequencer/sequences" && init?.method !== "POST").length).toBeGreaterThan(1))
  })

  it("keeps sequence mutations explicit on API conflicts", async () => {
    const toastError = vi.spyOn(toast, "error")
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = String(input)
      if (path === "/api/animations") return jsonResponse({ items: animations })
      if (path === "/api/sequencer/sequences") return jsonResponse({ sequences: [], active_sequence_id: null })
      if (path === "/api/sequencer/active") return jsonResponse({ detail: "Sequence library is full." }, 409)
      return jsonResponse(initialState)
    }))
    const { result } = renderHook(() => useDashboard(), { wrapper })
    await waitFor(() => expect(result.current.sequenceLibraryLoading).toBe(false))

    let activated: boolean | undefined
    await act(async () => { activated = await result.current.setActiveSequence("show-a") })
    expect(activated).toBe(false)
    expect(result.current.sequenceActionError).toBe("Sequence library is full.")
    expect(toastError).toHaveBeenCalledWith("Sequence library is full.")
    expect(result.current.pendingCommand).toBeNull()
    toastError.mockRestore()
  })
})
