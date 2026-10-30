import { render, screen, waitFor } from "@testing-library/react"
import type { ComponentProps } from "react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { AnimationSheet } from "@/components/dashboard/animation-sheet"
import type { AnimationOption, AnimationSettingsResponse } from "@/lib/api"
import { initialState } from "@/test/fixtures"

const cometOptions = [
  {
    id: "speed",
    label: "Speed",
    type: "number" as const,
    default: 65,
    description: "How quickly the comet moves.",
    minimum: 0,
    maximum: 100,
    step: 1,
    unit: "%",
    choices: [],
    visible_when_option: null,
    visible_when_value: null,
  },
  {
    id: "intensity",
    label: "Intensity",
    type: "number" as const,
    default: 80,
    description: "Maximum brightness.",
    minimum: 0,
    maximum: 100,
    step: 1,
    unit: "%",
    choices: [],
    visible_when_option: null,
    visible_when_value: null,
  },
  {
    id: "direction",
    label: "Direction",
    type: "enum" as const,
    default: "bounce",
    description: "Direction of travel.",
    minimum: null,
    maximum: null,
    step: null,
    unit: null,
    choices: ["forward", "reverse", "bounce"],
    visible_when_option: null,
    visible_when_value: null,
  },
  {
    id: "palette",
    label: "Color palette",
    type: "enum" as const,
    default: "rainbow",
    description: "Choose a palette.",
    minimum: null,
    maximum: null,
    step: null,
    unit: null,
    choices: ["rainbow", "neon", "fire", "custom"],
    visible_when_option: null,
    visible_when_value: null,
  },
  {
    id: "custom_color",
    label: "Custom color",
    type: "color" as const,
    default: "#7C3AED",
    description: "Choose the custom color.",
    minimum: null,
    maximum: null,
    step: null,
    unit: null,
    choices: [],
    visible_when_option: "palette",
    visible_when_value: "custom",
  },
]

const cometSettings: AnimationSettingsResponse = {
  animation: "comet",
  stripe_id: null,
  options: cometOptions,
  values: { speed: 65, intensity: 80, direction: "bounce", palette: "rainbow", custom_color: "#7C3AED" },
}

const animations: AnimationOption[] = [
  {
    name: "comet",
    display_name: "Comet",
    description: "A moving light leaves a fading tail.",
    role: "base",
    mood: "dynamic",
    dynamic_safe: true,
    sequence_eligible: true,
    settings: cometOptions,
  },
  {
    name: "rainbow",
    display_name: "Rainbow",
    description: "A continuous rainbow cycles across the strip.",
    role: "base",
    mood: "ambient",
    dynamic_safe: true,
    sequence_eligible: true,
    settings: [],
  },
  {
    name: "beat_wave",
    display_name: "Beat Wave",
    description: "A transparent wave expands on each beat.",
    role: "effect",
    mood: "rhythmic",
    dynamic_safe: true,
    sequence_eligible: false,
    settings: [],
  },
]

function renderSheet(overrides: Partial<ComponentProps<typeof AnimationSheet>> = {}) {
  const props: ComponentProps<typeof AnimationSheet> = {
    animations,
    current: "comet",
    disabled: false,
    previewEnabled: false,
    previewFrame: null,
    previewConnection: "reconnecting",
    previewTopology: initialState.stripe_topology,
    onEnablePreview: vi.fn(),
    onDisablePreview: vi.fn(),
    onEditorVisibilityChange: vi.fn(),
    onSelect: vi.fn().mockResolvedValue(true),
    onLoadSettings: vi.fn().mockResolvedValue(cometSettings),
    onSaveSettings: vi.fn().mockImplementation(async (_name, values) => ({
      ...cometSettings,
      values,
    })),
    ...overrides,
  }
  return { props, ...render(<AnimationSheet {...props} />) }
}

describe("AnimationSheet", () => {
  it("selects an idle animation without exposing playback settings", async () => {
    const user = userEvent.setup()
    const { props } = renderSheet({ selectionOnly: true })
    await user.click(screen.getByRole("button", { name: /Idle animation.*Change/i }))
    expect(screen.getByText("Choose idle animation")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Configure Comet" })).toBeNull()
    await user.type(screen.getByRole("searchbox", { name: "Search animations" }), "wave")
    await user.click(screen.getByRole("button", { name: /Beat Wave/ }))
    expect(props.onSelect).toHaveBeenCalledWith("beat_wave")
  })
  it("groups entries by role and marks the selected, music-ready animation", async () => {
    const user = userEvent.setup()
    renderSheet()
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))

    expect(screen.getByRole("region", { name: "Base animations" })).toBeInTheDocument()
    expect(screen.getByRole("region", { name: "Effects" })).toBeInTheDocument()
    expect(screen.getByText("A transparent wave expands on each beat.")).toBeInTheDocument()
    expect(screen.getAllByText("Music-ready")).toHaveLength(3)
    expect(screen.getByRole("button", { name: /Comet.*Selected/ })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getAllByText("Sequencer-ready")).toHaveLength(2)
  })

  it("generates palette controls and conditionally shows the custom color input", async () => {
    const user = userEvent.setup()
    const { props } = renderSheet()
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))
    await user.click(await screen.findByRole("button", { name: "Configure Comet" }))

    const palette = await screen.findByLabelText("Color palette")
    expect(screen.queryByLabelText("Custom color")).toBeNull()
    await user.selectOptions(palette, "custom")
    expect(await screen.findByLabelText("Custom color")).toHaveValue("#7c3aed")

    await user.click(screen.getByRole("button", { name: "Save settings" }))
    await waitFor(() => expect(props.onSaveSettings).toHaveBeenCalledWith(
      "comet",
      { speed: 65, intensity: 80, direction: "bounce", palette: "custom", custom_color: "#7C3AED" },
      undefined,
    ))
  })

  it("loads defaults into the draft without persisting them", async () => {
    const user = userEvent.setup()
    const { props } = renderSheet({ stripeId: "left" })
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))
    await user.click(await screen.findByRole("button", { name: "Configure Comet" }))
    await user.selectOptions(await screen.findByLabelText("Color palette"), "custom")
    await user.click(screen.getByRole("button", { name: "Reset draft" }))

    expect(await screen.findByLabelText("Color palette")).toHaveValue("rainbow")
    expect(screen.queryByLabelText("Custom color")).not.toBeInTheDocument()
    expect(screen.getByText("Defaults loaded into this draft. Save to apply them.")).toBeInTheDocument()
    expect(props.onSaveSettings).not.toHaveBeenCalled()
  })

  it("disables per-animation settings until an independent stripe is selected", async () => {
    const user = userEvent.setup()
    renderSheet({ settingsDisabledReason: "Choose a stripe to edit its animation settings." })
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))

    expect(screen.getByRole("button", { name: "Configure Comet" })).toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent("Choose a stripe")
  })

  it("applies presets to numeric style controls while retaining color and direction", async () => {
    const user = userEvent.setup()
    renderSheet()
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))
    await user.click(await screen.findByRole("button", { name: "Configure Comet" }))
    await user.selectOptions(await screen.findByLabelText("Direction"), "reverse")
    await user.selectOptions(await screen.findByLabelText("Color palette"), "custom")
    await user.click(screen.getByRole("button", { name: /energetic/i }))

    expect(screen.getByText("88%")).toBeInTheDocument()
    expect(screen.getByText("93%")).toBeInTheDocument()
    expect(screen.getByLabelText("Direction")).toHaveValue("reverse")
    expect(screen.getByLabelText("Color palette")).toHaveValue("custom")
    expect(screen.getByLabelText("Custom color")).toHaveValue("#7c3aed")
    expect(screen.getByText("Unsaved edits are not applied to the LEDs. Save settings to update this preview.")).toBeInTheDocument()
  })

  it("asks before discarding a dirty draft and keeps editing on cancel", async () => {
    const user = userEvent.setup()
    renderSheet()
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))
    await user.click(await screen.findByRole("button", { name: "Configure Comet" }))
    await user.selectOptions(await screen.findByLabelText("Color palette"), "custom")
    await user.click(screen.getByRole("button", { name: "Back to animations" }))

    expect(await screen.findByRole("alertdialog")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Keep editing" }))
    expect(screen.getByLabelText("Color palette")).toHaveValue("custom")

    await user.click(screen.getByRole("button", { name: "Back to animations" }))
    await user.click(screen.getByRole("button", { name: "Discard changes" }))
    expect(await screen.findByRole("heading", { name: "Choose animation" })).toBeInTheDocument()
  })

  it("preserves the dirty draft when saving fails", async () => {
    const user = userEvent.setup()
    renderSheet({
      onSaveSettings: vi.fn().mockRejectedValue(new Error("Device is unavailable.")),
    })
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))
    await user.click(await screen.findByRole("button", { name: "Configure Comet" }))
    await user.selectOptions(await screen.findByLabelText("Color palette"), "custom")
    await user.click(screen.getByRole("button", { name: "Save settings" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("Device is unavailable.")
    expect(screen.getByLabelText("Color palette")).toHaveValue("custom")
    expect(screen.getByLabelText("Custom color")).toHaveValue("#7c3aed")
  })

  it("shows the shared saved preview and lets users turn it off from the editor", async () => {
    const user = userEvent.setup()
    const { props } = renderSheet({
      previewEnabled: true,
      previewFrame: { sequence: 4, outputs: [new Uint8Array([0, 255, 80, 255])] },
      previewConnection: "connected",
    })
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))
    await user.click(await screen.findByRole("button", { name: "Configure Comet" }))

    expect(await screen.findByText("Saved output")).toBeInTheDocument()
    expect(screen.getByText("This preview shows the settings currently saved on the selected output.")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Hide live preview" }))
    expect(props.onDisablePreview).toHaveBeenCalledOnce()
  })
})
