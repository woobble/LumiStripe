import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

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
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal("WebSocket", MockWebSocket)
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) => {
        const path = String(input)
        if (path === "/api/animations") return jsonResponse({ items: animations })
        return jsonResponse(initialState)
      })
    )
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("loads initial data and applies only current websocket revisions", async () => {
    const { result } = renderHook(() => useDashboard())
    await waitFor(() => expect(result.current.state?.revision).toBe(1))
    await waitFor(() => expect(result.current.connection).toBe("connected"))

    act(() => MockWebSocket.instances[0].emit({ ...initialState, revision: 4, brightness: 0.25 }))
    expect(result.current.state?.brightness).toBe(0.25)

    act(() => MockWebSocket.instances[0].emit({ ...initialState, revision: 2, brightness: 0.9 }))
    expect(result.current.state?.revision).toBe(4)
    expect(result.current.state?.brightness).toBe(0.25)
  })

  it("marks a dropped socket as reconnecting and opens a replacement", async () => {
    const { result } = renderHook(() => useDashboard())
    await waitFor(() => expect(result.current.connection).toBe("connected"))

    vi.useFakeTimers()
    act(() => MockWebSocket.instances[0].disconnect())
    expect(result.current.connection).toBe("reconnecting")

    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(MockWebSocket.instances).toHaveLength(2)
  })
})
