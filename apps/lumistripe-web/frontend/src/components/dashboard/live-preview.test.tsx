import { render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { LivePreview } from "@/components/dashboard/live-preview"
import { initialState } from "@/test/fixtures"

class MockPreviewWebSocket {
  static instances: MockPreviewWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  binaryType = ""

  constructor() {
    MockPreviewWebSocket.instances.push(this)
    queueMicrotask(() => this.onopen?.())
  }

  close() {}

  emit(data: ArrayBuffer) {
    this.onmessage?.(new MessageEvent("message", { data }))
  }
}

function frame(sequence = 1) {
  const output = new Uint8Array([255, 0, 100, 255])
  const buffer = new ArrayBuffer(16 + output.byteLength)
  const view = new DataView(buffer)
  new Uint8Array(buffer, 0, 4).set([0x4c, 0x53, 0x46, 0x50])
  view.setUint8(4, 1)
  view.setUint32(6, sequence, true)
  view.setUint16(10, 1, true)
  view.setUint32(12, 1, true)
  new Uint8Array(buffer, 16).set(output)
  return buffer
}

describe("LivePreview", () => {
  beforeEach(() => {
    MockPreviewWebSocket.instances = []
    vi.stubGlobal("WebSocket", MockPreviewWebSocket)
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      fillStyle: "",
      shadowBlur: 0,
      shadowColor: "",
      globalAlpha: 1,
    } as unknown as CanvasRenderingContext2D)
  })

  it("renders the live status and uses Canvas when WebGPU is unavailable", async () => {
    render(<LivePreview state={initialState} />)

    expect(screen.getByTestId("live-preview")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText("Canvas fallback")).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText("Connecting")).toBeInTheDocument())

    MockPreviewWebSocket.instances[0]?.emit(frame())

    await waitFor(() => expect(screen.getByText("Live")).toBeInTheDocument())
    expect(screen.getByText("Primary · 41px")).toBeInTheDocument()
  })
})
