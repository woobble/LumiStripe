import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { AudioOutputControls } from "@/components/dashboard/audio-output-controls"
import { AudioSourceStatus } from "@/components/dashboard/audio-source-status"
import type { BluetoothStatusResponse } from "@/lib/api"

const noOutputs: BluetoothStatusResponse = {
  available: false,
  powered: false,
  scanning: false,
  streaming: false,
  devices: [],
  connected_inputs: [],
  connected_outputs: [],
  output_devices: [],
  output_muted: false,
  output_ready: false,
  capabilities: { max_inputs: 1, max_outputs: 1, operations: [] },
  operation_state: "idle",
  error: "PipeWire output discovery failed.",
}

function renderOutput(status: BluetoothStatusResponse, variant: "setup" | "compact" = "setup") {
  const onAction = vi.fn(async () => null)
  return render(
    <MemoryRouter>
      <AudioOutputControls status={status} busy={false} onAction={onAction} variant={variant} />
    </MemoryRouter>,
  )
}

describe("audio routing layouts", () => {
  it("shows a readable compact empty state and keeps discovery errors visible", () => {
    renderOutput(noOutputs, "compact")

    expect(screen.getByText("No PipeWire output devices are available.")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("PipeWire output discovery failed.")
    expect(screen.getByRole("link", { name: "Open audio device setup" })).toHaveAttribute("href", "/setup/audio/devices")
  })

  it("keeps long output names readable in Audio Setup with controls available", () => {
    const outputName = "USB audio interface for the listening room and studio monitors"
    const status: BluetoothStatusResponse = {
      ...noOutputs,
      output_devices: [{
        selector: "alsa_output.usb-studio",
        name: outputName,
        bluetooth: false,
        connected: true,
        muted: false,
        volume: 0.42,
      }],
      default_sink: "alsa_output.usb-studio",
      output_ready: true,
      error: null,
    }

    renderOutput(status)

    const selector = screen.getByRole("combobox", { name: "Audio output device" })
    expect(selector).toHaveTextContent(outputName)
    expect(selector).not.toBeDisabled()
    expect(screen.getByText(outputName, { selector: "p" })).not.toHaveClass("truncate")
    expect(screen.getByRole("group", { name: "Audio output volume" })).toBeInTheDocument()
    expect(screen.getByRole("switch", { name: "Enable audio output" })).toBeInTheDocument()
  })

  it("renders each primary input status as a full-width row", () => {
    const { container } = render(
      <AudioSourceStatus
        configuredSource="auto"
        activeSource="bluetooth"
        monitoring
        connection="connected"
        fresh
        compact
      />,
    )

    for (const label of ["Configured source", "Active source", "Audio monitor"]) {
      const labelElement = screen.getByText(label)
      expect(labelElement.parentElement).toHaveClass("flex", "justify-between")
    }
    expect(screen.getByText("Automatic")).toBeInTheDocument()
    expect(screen.getByText("Bluetooth input")).toBeInTheDocument()
    expect(screen.getByText("Running")).toBeInTheDocument()
    expect(container.querySelector("[class*='grid-cols-2']")).toBeNull()
  })
})
