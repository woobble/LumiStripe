import { render, screen, waitFor } from "@testing-library/react"
import type { ComponentProps } from "react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { AnimationSheet } from "@/components/dashboard/animation-sheet"
import type { AnimationOption, AnimationSettingsResponse } from "@/lib/api"

const cometOptions = [
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
  values: { palette: "rainbow", custom_color: "#7C3AED" },
}

const animations: AnimationOption[] = [
  {
    name: "comet",
    mood: "dynamic",
    dynamic_safe: true,
    sequence_eligible: true,
    settings: cometOptions,
  },
  {
    name: "rainbow",
    mood: "ambient",
    dynamic_safe: true,
    sequence_eligible: true,
    settings: [],
  },
]

function renderSheet(overrides: Partial<ComponentProps<typeof AnimationSheet>> = {}) {
  const props: ComponentProps<typeof AnimationSheet> = {
    animations,
    current: "comet",
    disabled: false,
    onSelect: vi.fn().mockResolvedValue(true),
    onLoadSettings: vi.fn().mockResolvedValue(cometSettings),
    onSaveSettings: vi.fn().mockImplementation(async (_name, values) => ({
      ...cometSettings,
      values,
    })),
    onResetSettings: vi.fn().mockResolvedValue(cometSettings),
    ...overrides,
  }
  return { props, ...render(<AnimationSheet {...props} />) }
}

describe("AnimationSheet", () => {
  it("generates palette controls and conditionally shows the custom color input", async () => {
    const user = userEvent.setup()
    const { props } = renderSheet()
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))
    await user.click(await screen.findByRole("button", { name: "Configure comet" }))

    const palette = await screen.findByLabelText("Color palette")
    expect(screen.queryByLabelText("Custom color")).toBeNull()
    await user.selectOptions(palette, "custom")
    expect(await screen.findByLabelText("Custom color")).toHaveValue("#7c3aed")

    await user.click(screen.getByRole("button", { name: "Save settings" }))
    await waitFor(() => expect(props.onSaveSettings).toHaveBeenCalledWith(
      "comet",
      { palette: "custom", custom_color: "#7C3AED" },
      undefined,
    ))
  })

  it("resets configurable animation settings to backend defaults", async () => {
    const user = userEvent.setup()
    const { props } = renderSheet({ stripeId: "left" })
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))
    await user.click(await screen.findByRole("button", { name: "Configure comet" }))
    await screen.findByLabelText("Color palette")
    await user.click(screen.getByRole("button", { name: "Reset to defaults" }))

    await waitFor(() => expect(props.onResetSettings).toHaveBeenCalledWith("comet", "left"))
  })

  it("disables per-animation settings until an independent stripe is selected", async () => {
    const user = userEvent.setup()
    renderSheet({ settingsDisabledReason: "Choose a stripe to edit its animation settings." })
    await user.click(screen.getByRole("button", { name: /Animation.*Change/i }))

    expect(screen.getByRole("button", { name: "Configure comet" })).toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent("Choose a stripe")
  })
})
