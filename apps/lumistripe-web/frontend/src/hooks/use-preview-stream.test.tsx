import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { usePreviewStream } from "@/hooks/use-preview-stream"

class MockWebSocket {
  static instances: MockWebSocket[] = []
  readonly url: string
  binaryType: BinaryType = "blob"
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  close() {
    this.closed = true
  }
}

describe("usePreviewStream", () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal("WebSocket", MockWebSocket)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("pauses the preview socket while the page is hidden", async () => {
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
    const { result } = renderHook(() => usePreviewStream(true))
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))

    act(() => {
      visibility.mockReturnValue("hidden")
      document.dispatchEvent(new Event("visibilitychange"))
    })
    await waitFor(() => expect(MockWebSocket.instances[0]?.closed).toBe(true))
    expect(result.current.frame).toBeNull()

    act(() => {
      visibility.mockReturnValue("visible")
      document.dispatchEvent(new Event("visibilitychange"))
    })
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2))
  })
})
