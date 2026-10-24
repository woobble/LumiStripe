import { render, screen, waitFor } from "@testing-library/react"
import type { ComponentProps } from "react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { SequenceLibrary } from "@/components/dashboard/sequence-library"
import type { AnimationSequenceContract, DashboardState } from "@/lib/api"
import { animations, initialState } from "@/test/fixtures"

const savedSequence: AnimationSequenceContract = {
  id: "night-drive",
  name: "Night drive",
  repeat: true,
  steps: [
    { animation_name: "aurora_wave", duration_bars: 2 },
    { animation_name: "bass_pulse", duration_bars: 2 },
  ],
}

function sequenceState(overrides: Partial<DashboardState> = {}): DashboardState {
  return { ...initialState, ...overrides }
}

function renderLibrary(overrides: Partial<ComponentProps<typeof SequenceLibrary>> = {}) {
  const props: ComponentProps<typeof SequenceLibrary> = {
    state: initialState,
    target: "all",
    animations,
    sequences: [],
    loading: false,
    loadError: null,
    actionError: null,
    saving: false,
    onRefresh: vi.fn().mockResolvedValue(undefined),
    onCreate: vi.fn().mockResolvedValue(true),
    onUpdate: vi.fn().mockResolvedValue(true),
    onDelete: vi.fn().mockResolvedValue(true),
    onSetActive: vi.fn().mockResolvedValue(true),
    ...overrides,
  }
  return { props, ...render(<SequenceLibrary {...props} />) }
}

async function openEditor(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Open sequence library" }))
  await user.click(screen.getByRole("button", { name: "New sequence" }))
}

describe("SequenceLibrary", () => {
  it("creates a sequence with eligible animations and ordered, quantized steps", async () => {
    const user = userEvent.setup()
    const state = sequenceState({
      stripe_playback: [{
        ...initialState.stripe_playback[0]!,
        sequencer_diagnostics: { ...initialState.stripe_playback[0]!.sequencer_diagnostics!, bpm: 120 },
      }],
    })
    const { props } = renderLibrary({ state })
    await openEditor(user)
    expect(screen.getByRole("dialog").className).toContain("h-[100svh]")

    expect(screen.getByLabelText(/Sequence name/)).toHaveValue("New sequence")
    const animationSelect = screen.getByLabelText("Animation for step 1")
    expect(animationSelect.querySelector('option[value="quiet_stars"]')).toBeNull()
    expect(screen.getByRole("switch", { name: "Repeat sequence" })).toBeChecked()

    await user.selectOptions(screen.getByLabelText("Duration for step 1"), "2")
    await user.click(screen.getByRole("button", { name: "Add step" }))
    await user.selectOptions(screen.getByLabelText("Animation for step 2"), "bass_pulse")
    await user.selectOptions(screen.getByLabelText("Duration for step 2"), "4")
    expect(screen.getByText("About 0:12 at 120 BPM")).toBeInTheDocument()
    screen.getByRole("button", { name: "Move step 2 up" }).focus()
    await user.keyboard("{Enter}")
    await user.click(screen.getByRole("button", { name: "Save sequence" }))

    await waitFor(() => expect(props.onCreate).toHaveBeenCalledOnce())
    const created = vi.mocked(props.onCreate).mock.calls[0]?.[0]
    expect(created?.id).toMatch(/^sequence-/)
    expect(created?.steps).toEqual([
      { animation_name: "bass_pulse", duration_bars: 4 },
      { animation_name: "aurora_wave", duration_bars: 2 },
    ])
    expect(created?.repeat).toBe(true)
  })

  it("preserves the draft after a failed create and confirms discarding unsaved edits", async () => {
    const user = userEvent.setup()
    const onCreate = vi.fn().mockResolvedValue(false)
    renderLibrary({ onCreate })
    await openEditor(user)
    await user.clear(screen.getByLabelText(/Sequence name/))
    await user.type(screen.getByLabelText(/Sequence name/), "Saved after retry")
    await user.click(screen.getByRole("button", { name: "Save sequence" }))

    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce())
    expect(screen.getByLabelText(/Sequence name/)).toHaveValue("Saved after retry")

    await user.click(screen.getByRole("button", { name: "Cancel" }))
    expect(screen.getByRole("alertdialog", { name: "Discard unsaved sequence edits?" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Keep editing" }))
    expect(screen.getByLabelText(/Sequence name/)).toHaveValue("Saved after retry")
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    await user.click(screen.getByRole("button", { name: "Discard edits" }))
    expect(screen.queryByRole("heading", { name: "Create sequence" })).not.toBeInTheDocument()
  })

  it("edits stable IDs, duplicates with a new ID, and supports repeat changes", async () => {
    const user = userEvent.setup()
    const onUpdate = vi.fn().mockResolvedValue(true)
    const onCreate = vi.fn().mockResolvedValue(true)
    renderLibrary({ sequences: [savedSequence], onUpdate, onCreate })
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    await user.click(screen.getByRole("button", { name: "Edit" }))
    await user.clear(screen.getByLabelText(/Sequence name/))
    await user.type(screen.getByLabelText(/Sequence name/), "Night drive edit")
    await user.click(screen.getByRole("button", { name: "Save changes" }))
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith("night-drive", expect.objectContaining({ name: "Night drive edit" })))

    await user.click(screen.getByRole("button", { name: "Duplicate" }))
    expect(screen.getByLabelText(/Sequence name/)).toHaveValue("Copy of Night drive")
    await user.click(screen.getByRole("switch", { name: "Repeat sequence" }))
    await user.click(screen.getByRole("button", { name: "Save sequence" }))
    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce())
    const duplicate = vi.mocked(onCreate).mock.calls[0]?.[0]
    expect(duplicate?.id).not.toBe(savedSequence.id)
    expect(duplicate?.name).toBe("Copy of Night drive")
    expect(duplicate?.repeat).toBe(false)
    expect(duplicate?.steps).toEqual(savedSequence.steps)
  })

  it("requires a non-empty name and a valid minimum step, and enforces the step limit", async () => {
    const user = userEvent.setup()
    const longSequence: AnimationSequenceContract = {
      ...savedSequence,
      steps: Array.from({ length: 32 }, (_, index) => ({
        animation_name: index % 2 === 0 ? "aurora_wave" : "bass_pulse",
        duration_bars: 1 as const,
      })),
    }
    renderLibrary({ sequences: [longSequence] })
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    await user.click(screen.getByRole("button", { name: "Edit" }))
    expect(screen.getByRole("button", { name: "Add step" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Remove step 1" })).toBeEnabled()
    expect(screen.getByLabelText(/Sequence name/)).toHaveAttribute("maxLength", "80")
    await user.clear(screen.getByLabelText(/Sequence name/))
    await user.click(screen.getByRole("button", { name: "Save changes" }))
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a sequence name")
  })

  it("activates and deactivates a sequence and confirms deletion", async () => {
    const user = userEvent.setup()
    const onSetActive = vi.fn().mockResolvedValue(true)
    const onDelete = vi.fn().mockResolvedValue(true)
    const { props, rerender } = renderLibrary({ sequences: [savedSequence], onSetActive, onDelete })
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    await user.click(screen.getByRole("button", { name: "Activate" }))
    expect(onSetActive).toHaveBeenCalledWith(savedSequence.id)

    const selectedState = sequenceState({
      sequence_library: { sequences: [{ id: savedSequence.id, name: savedSequence.name }], active_sequence_id: savedSequence.id },
    })
    rerender(<SequenceLibrary {...props} state={selectedState} />)
    await user.click(screen.getByRole("button", { name: "Deactivate" }))
    expect(onSetActive).toHaveBeenLastCalledWith(null)

    await user.click(screen.getByRole("button", { name: "Delete" }))
    expect(screen.getByText(/If it is selected, playback will return/)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Delete sequence" }))
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(savedSequence.id))
  })

  it("shows playback progress, confidence waiting, and estimated-grid wording", async () => {
    const user = userEvent.setup()
    const state = sequenceState({
      sequence_library: { sequences: [{ id: savedSequence.id, name: savedSequence.name }], active_sequence_id: savedSequence.id },
      stripe_playback: [{
        ...initialState.stripe_playback[0]!,
        mode: "dynamic",
        music_active: true,
        sequence_playback: {
          selected_sequence_id: savedSequence.id,
          active: true,
          current_step_index: 1,
          animation_name: "bass_pulse",
          remaining_bars: 1,
          waiting_for_clock: false,
          next_transition_in_s: 4.2,
        },
      }],
    })
    const { unmount } = renderLibrary({ state, sequences: [savedSequence] })
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    expect(screen.getByText("Playing")).toBeInTheDocument()
    expect(screen.getByText(/Step 2: bass pulse · 1 bars remaining · next transition in 5s/)).toBeInTheDocument()
    unmount()

    const waitingState = sequenceState({
      sequence_library: { sequences: [{ id: savedSequence.id, name: savedSequence.name }], active_sequence_id: savedSequence.id },
      stripe_playback: [{
        ...initialState.stripe_playback[0]!,
        mode: "dynamic",
        music_active: true,
        sequence_playback: {
          selected_sequence_id: savedSequence.id,
          active: false,
          current_step_index: null,
          animation_name: null,
          remaining_bars: null,
          waiting_for_clock: true,
          next_transition_in_s: null,
        },
      }],
    })
    render(<SequenceLibrary
      state={waitingState}
      target="all"
      animations={animations}
      sequences={[savedSequence]}
      loading={false}
      loadError={null}
      actionError={null}
      saving={false}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onCreate={vi.fn().mockResolvedValue(true)}
      onUpdate={vi.fn().mockResolvedValue(true)}
      onDelete={vi.fn().mockResolvedValue(true)}
      onSetActive={vi.fn().mockResolvedValue(true)}
    />)
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    expect(screen.getByText("Waiting for clock")).toBeInTheDocument()
    expect(screen.getByText(/confident beat clock/)).toBeInTheDocument()
  })

  it("shows Dynamic-mode requirements and finished non-repeating state separately from deactivation", async () => {
    const user = userEvent.setup()
    const state = sequenceState({
      sequence_library: { sequences: [{ id: savedSequence.id, name: savedSequence.name }], active_sequence_id: savedSequence.id },
      stripe_playback: [{
        ...initialState.stripe_playback[0]!,
        mode: "dynamic",
        music_active: true,
        sequence_playback: {
          selected_sequence_id: savedSequence.id,
          active: false,
          current_step_index: 1,
          animation_name: "bass_pulse",
          remaining_bars: 0,
          waiting_for_clock: false,
          next_transition_in_s: null,
        },
      }],
    })
    renderLibrary({ state, sequences: [{ ...savedSequence, repeat: false }] })
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    expect(screen.getByText("Finished · automatic selection resumed")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Deactivate" })).toBeEnabled()
  })

  it("shows the scheduled initial bar countdown without labeling it finished", async () => {
    const user = userEvent.setup()
    const state = sequenceState({
      sequence_library: { sequences: [{ id: savedSequence.id, name: savedSequence.name }], active_sequence_id: savedSequence.id },
      stripe_playback: [{
        ...initialState.stripe_playback[0]!,
        mode: "dynamic",
        music_active: true,
        sequence_playback: {
          selected_sequence_id: savedSequence.id,
          active: false,
          current_step_index: null,
          animation_name: null,
          remaining_bars: null,
          waiting_for_clock: false,
          next_transition_in_s: 6.1,
        },
      }],
    })
    renderLibrary({ state, sequences: [savedSequence] })
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    expect(screen.getByText("Starting at next bar")).toBeInTheDocument()
    expect(screen.getByText("Starts on the estimated bar in 7s.")).toBeInTheDocument()
    expect(screen.queryByText("Finished · automatic selection resumed")).not.toBeInTheDocument()
  })

  it("shows loading, empty, and retryable error states and enforces the library cap", async () => {
    const user = userEvent.setup()
    const many = Array.from({ length: 20 }, (_, index) => ({ ...savedSequence, id: `seq-${index}`, name: `Sequence ${index}` }))
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    const { rerender } = renderLibrary({ loading: true, onRefresh })
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    expect(screen.getByRole("status")).toHaveTextContent("Loading saved sequences")
    rerender(<SequenceLibrary
      state={initialState}
      target="all"
      animations={animations}
      sequences={[]}
      loading={false}
      loadError="Could not load saved sequences."
      actionError={null}
      saving={false}
      onRefresh={onRefresh}
      onCreate={vi.fn().mockResolvedValue(true)}
      onUpdate={vi.fn().mockResolvedValue(true)}
      onDelete={vi.fn().mockResolvedValue(true)}
      onSetActive={vi.fn().mockResolvedValue(true)}
    />)
    expect(screen.getByText("No saved sequences yet")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Retry" }))
    expect(onRefresh).toHaveBeenCalledOnce()
    rerender(<SequenceLibrary
      state={initialState}
      target="all"
      animations={animations}
      sequences={many}
      loading={false}
      loadError={null}
      actionError={null}
      saving={false}
      onRefresh={onRefresh}
      onCreate={vi.fn().mockResolvedValue(true)}
      onUpdate={vi.fn().mockResolvedValue(true)}
      onDelete={vi.fn().mockResolvedValue(true)}
      onSetActive={vi.fn().mockResolvedValue(true)}
    />)
    expect(screen.getByRole("button", { name: "New sequence" })).toBeDisabled()
    expect(screen.getByText("The library has reached its 20-sequence limit.")).toBeInTheDocument()
  })

  it("reports independent output progress for all targets", async () => {
    const user = userEvent.setup()
    const basePlayback = initialState.stripe_playback[0]!
    const state = sequenceState({
      stripe_topology: {
        ...initialState.stripe_topology,
        layout: "independent",
        outputs: [
          { ...initialState.stripe_topology.outputs[0]!, id: "left", name: "Left" },
          { ...initialState.stripe_topology.outputs[0]!, id: "right", name: "Right" },
        ],
      },
      stripe_playback: [
        { ...basePlayback, stripe_id: "left", mode: "dynamic", sequence_playback: { selected_sequence_id: savedSequence.id, active: true, current_step_index: 0, animation_name: "aurora_wave", remaining_bars: 2, waiting_for_clock: false, next_transition_in_s: 10 } },
        { ...basePlayback, stripe_id: "right", mode: "dynamic", sequence_playback: { selected_sequence_id: savedSequence.id, active: true, current_step_index: 1, animation_name: "bass_pulse", remaining_bars: 1, waiting_for_clock: false, next_transition_in_s: 5 } },
      ],
      sequence_library: { sequences: [{ id: savedSequence.id, name: savedSequence.name }], active_sequence_id: savedSequence.id },
    })
    renderLibrary({ state, sequences: [savedSequence] })
    await user.click(screen.getByRole("button", { name: "Open sequence library" }))
    expect(screen.getByText(/Left: Step 1: aurora wave/)).toBeInTheDocument()
    expect(screen.getByText(/Right: Step 2: bass pulse/)).toBeInTheDocument()
  })
})
