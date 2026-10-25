import { useState } from "react"
import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

vi.mock("@/components/ui/slider", () => ({
  Slider: ({
    value,
    min,
    max,
    step,
    onValueChange,
    "aria-label": label,
  }: {
    value: number[]
    min: number
    max: number
    step: number
    onValueChange: (value: number[]) => void
    "aria-label": string
  }) => <input type="range" aria-label={label} min={min} max={max} step={step} value={value[0]} onChange={(event) => onValueChange([Number(event.currentTarget.value)])} />,
}))

import { AudioSyncControls } from "./audio-sync-controls"
import type { AudioSettingsResponse } from "@/lib/api"

const settings = {
  source: "mic",
  active_source: "mic",
  active_device_name: "USB Mic",
  monitoring: true,
  sync_route: "mic|USB Mic|sink-main",
  sync_offset_ms: -85,
} as AudioSettingsResponse

function Harness({ onSave = vi.fn(async () => settings) }: { onSave?: (offset: number) => Promise<AudioSettingsResponse> }) {
  const [current, setCurrent] = useState(settings)
  const save = async (offset: number) => {
    const next = await onSave(offset)
    setCurrent({ ...next, sync_offset_ms: offset })
    return { ...next, sync_offset_ms: offset }
  }
  return <AudioSyncControls settings={current} calibrationRunning={false} onStartCalibration={vi.fn()} onStopCalibration={vi.fn()} onSave={save} />
}

describe("guided audio synchronization", () => {
  it("shows route confirmation, cue playback, adjustment direction and saved state", () => {
    render(<AudioSyncControls settings={settings} calibrationRunning={false} onStartCalibration={vi.fn()} onStopCalibration={vi.fn()} onSave={vi.fn(async () => settings)} />)
    expect(screen.getByRole("heading", { name: "1. Confirm the active route" })).toBeInTheDocument()
    expect(screen.getByText(/mic · USB Mic/i)).toBeInTheDocument()
    expect(screen.getByText("sink-main")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "2. Play the comparison cue" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "3. Adjust the timing" })).toBeInTheDocument()
    expect(screen.getByText(/If the LEDs flash before the sound/)).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "4. Stop, save and verify" })).toBeInTheDocument()
    expect(screen.getByText("-85 ms")).toBeInTheDocument()
  })

  it("keeps an offset draft on save failure, then saves it for the current route", async () => {
    const user = userEvent.setup()
    const save = vi.fn()
      .mockRejectedValueOnce(new Error("Offset service unavailable"))
      .mockImplementation(async (offset: number) => ({ ...settings, sync_offset_ms: offset }))
    render(<Harness onSave={save} />)
    const slider = screen.getByRole("slider", { name: "Audio to LED sync offset" })
    fireEvent.change(slider, { target: { value: "125" } })
    expect(screen.getByText("+125 ms")).toBeInTheDocument()

    const saveButton = screen.getByRole("button", { name: "Save route offset" })
    await user.click(saveButton)
    await waitFor(() => expect(saveButton).toBeEnabled())
    expect(screen.getByText("+125 ms")).toBeInTheDocument()

    await user.click(saveButton)
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    expect(save).toHaveBeenNthCalledWith(2, 125)
    expect(saveButton).toBeDisabled()
  })

  it("resets the draft when the input or output route changes", async () => {
    const user = userEvent.setup()
    const view = render(<AudioSyncControls settings={settings} calibrationRunning={false} onStartCalibration={vi.fn()} onStopCalibration={vi.fn()} onSave={vi.fn(async () => settings)} />)
    fireEvent.change(screen.getByRole("slider", { name: "Audio to LED sync offset" }), { target: { value: "55" } })
    expect(screen.getByText("+55 ms")).toBeInTheDocument()

    view.rerender(<AudioSyncControls settings={{ ...settings, sync_route: "bluetooth|Speaker|sink-bt", sync_offset_ms: 25 }} calibrationRunning={false} onStartCalibration={vi.fn()} onStopCalibration={vi.fn()} onSave={vi.fn(async () => settings)} />)
    await waitFor(() => expect(screen.getByRole("slider", { name: "Audio to LED sync offset" })).toHaveValue("25"))
    expect(screen.getByText("sink-bt")).toBeInTheDocument()
    expect(screen.getByRole("slider", { name: "Audio to LED sync offset" })).toHaveValue("25")
    await user.click(screen.getByRole("button", { name: "Play sync cue" }))
  })

  it("disables saving until the cue has stopped", () => {
    render(<AudioSyncControls settings={settings} calibrationRunning onStartCalibration={vi.fn()} onStopCalibration={vi.fn()} onSave={vi.fn(async () => settings)} />)
    fireEvent.change(screen.getByRole("slider", { name: "Audio to LED sync offset" }), { target: { value: "-80" } })
    expect(screen.getByRole("button", { name: "Stop sync cue" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "Save route offset" })).toBeDisabled()
  })
})
