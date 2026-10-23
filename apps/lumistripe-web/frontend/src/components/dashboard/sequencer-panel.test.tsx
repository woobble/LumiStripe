import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { SequencerPanel } from "@/components/dashboard/sequencer-panel"
import { initialState } from "@/test/fixtures"

describe("SequencerPanel", () => {
  it("applies a preset and phrase length, showing estimated-grid diagnostics", async () => {
    const user = userEvent.setup()
    const onApply = vi.fn().mockResolvedValue(true)
    const state = {
      ...initialState,
      stripe_topology: { ...initialState.stripe_topology, layout: "independent" as const },
      stripe_playback: [
        {
          ...initialState.stripe_playback[0]!,
          mode: "dynamic" as const,
          sequencer_diagnostics: {
            ...initialState.stripe_playback[0]!.sequencer_diagnostics!,
            mode: "phrase" as const,
            beat_index: 19,
            beat_in_bar: 3,
            bar_in_phrase: 5,
            phrase_index: 1,
            beat_clock_confidence: 0.87,
            alignment_estimated: true,
            pending_animation: "bass_pulse",
            next_switch_in_s: 4.2,
          },
        },
      ],
    }

    render(
      <SequencerPanel
        state={state}
        target="all"
        disabled={false}
        saving={false}
        onApply={onApply}
      />,
    )

    expect(screen.getByText("Clock confidence 87%")).toBeInTheDocument()
    expect(screen.getByText("Bar and phrase positions use an estimated 4/4 grid anchored to the first observed beat.")).toBeInTheDocument()
    expect(screen.getByText("bass_pulse")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Every Bar preset" }))
    await user.click(screen.getByRole("button", { name: "16 bars" }))
    await user.click(screen.getByRole("button", { name: "Apply and save sequencer settings" }))

    await waitFor(() => expect(onApply).toHaveBeenCalledWith("bar", 16))
  })

  it("shows every independent output for All and only the selected output when targeted", () => {
    const left = {
      ...initialState.stripe_playback[0]!,
      stripe_id: "left",
      sequencer_diagnostics: initialState.stripe_playback[0]!.sequencer_diagnostics,
    }
    const right = {
      ...left,
      stripe_id: "right",
      animation: "bass_pulse",
    }
    const state = {
      ...initialState,
      stripe_topology: {
        ...initialState.stripe_topology,
        layout: "independent" as const,
        outputs: [
          { ...initialState.stripe_topology.outputs[0]!, id: "left", name: "Left" },
          { ...initialState.stripe_topology.outputs[0]!, id: "right", name: "Right" },
        ],
      },
      stripe_playback: [left, right],
    }
    const props = {
      state,
      disabled: false,
      saving: false,
      onApply: vi.fn().mockResolvedValue(true),
    }

    const view = render(<SequencerPanel {...props} target="all" />)
    expect(screen.getByRole("region", { name: "Left sequencer status" })).toBeInTheDocument()
    expect(screen.getByRole("region", { name: "Right sequencer status" })).toBeInTheDocument()

    view.rerender(<SequencerPanel {...props} target="right" />)
    expect(screen.queryByRole("region", { name: "Left sequencer status" })).not.toBeInTheDocument()
    expect(screen.getByRole("region", { name: "Right sequencer status" })).toBeInTheDocument()
  })

  it("keeps the draft selected when apply fails", async () => {
    const onApply = vi.fn().mockResolvedValue(false)
    render(
      <SequencerPanel
        state={initialState}
        target="all"
        disabled={false}
        saving={false}
        onApply={onApply}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Every Phrase preset" }))
    fireEvent.click(screen.getByRole("button", { name: "Apply and save sequencer settings" }))

    await waitFor(() => expect(onApply).toHaveBeenCalledWith("phrase", 8))
    expect(screen.getByRole("button", { name: "Apply and save sequencer settings" })).toBeEnabled()
  })
})
