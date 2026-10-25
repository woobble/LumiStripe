import { render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { LivePreview } from "@/components/dashboard/live-preview"
import { initialState } from "@/test/fixtures"

describe("LivePreview", () => {
  beforeEach(() => {
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

  it("renders the supplied shared frame and uses Canvas when WebGPU is unavailable", async () => {
    const frame = {
      sequence: 1,
      outputs: [new Uint8Array([255, 0, 100, 255])],
    }
    render(<LivePreview state={initialState} frame={frame} connection="connected" />)

    expect(screen.getByTestId("live-preview")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText("Canvas fallback")).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText("Live")).toBeInTheDocument())
    expect(screen.getByText("Primary · 41px")).toBeInTheDocument()
  })

  it("marks zone boundaries over the physical output preview", async () => {
    const state = {
      ...initialState,
      stripe_topology: {
        ...initialState.stripe_topology,
        outputs: initialState.stripe_topology.outputs.map((output) => ({
          ...output,
          zone_count: 4,
        })),
      },
    }
    const { container } = render(
      <LivePreview
        state={state}
        frame={{ sequence: 1, outputs: [new Uint8Array(41 * 4)] }}
        connection="connected"
      />,
    )

    await waitFor(() => expect(screen.getByText("Canvas fallback")).toBeInTheDocument())
    expect(container.querySelectorAll('[data-testid="zone-boundary"]')).toHaveLength(3)
  })
})
