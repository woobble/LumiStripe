import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { TransitionSettingsPanel } from "@/components/dashboard/transition-settings-panel"
import type { DashboardController } from "@/hooks/use-dashboard"
import { initialState } from "@/test/fixtures"

describe("TransitionSettingsPanel", () => {
  it("toggles transitions and sends the current duration", async () => {
    const user = userEvent.setup()
    const setTransitionSettings = vi.fn().mockResolvedValue(true)
    const controller = {
      state: initialState,
      setTransitionSettings,
    } as unknown as DashboardController

    render(<TransitionSettingsPanel controller={controller} disabled={false} />)

    await user.click(screen.getByRole("button", { name: "Transitions" }))

    await waitFor(() => {
      expect(setTransitionSettings).toHaveBeenCalledWith(false, 300, "crossfade")
    })
  })

  it("disables duration control while transitions are off", () => {
    const controller = {
      state: {
        ...initialState,
        transition_settings: { enabled: false, duration_ms: 300, effect: "crossfade" },
      },
      setTransitionSettings: vi.fn().mockResolvedValue(true),
    } as unknown as DashboardController

    const { container } = render(<TransitionSettingsPanel controller={controller} disabled={false} />)

    expect(container.querySelector<HTMLInputElement>('input[type="range"]')).toBeDisabled()
  })

  it("applies duration changes in 50 ms steps", async () => {
    const setTransitionSettings = vi.fn().mockResolvedValue(true)
    const controller = {
      state: initialState,
      setTransitionSettings,
    } as unknown as DashboardController

    const { container } = render(<TransitionSettingsPanel controller={controller} disabled={false} />)
    const slider = container.querySelector<HTMLInputElement>('input[type="range"]')
    expect(slider).not.toBeNull()

    fireEvent.change(slider!, { target: { value: "350" } })

    await waitFor(() => {
      expect(setTransitionSettings).toHaveBeenCalledWith(true, 350, "crossfade")
    })
  })

  it("changes the transition effect immediately", async () => {
    const user = userEvent.setup()
    const setTransitionSettings = vi.fn().mockResolvedValue(true)
    const controller = {
      state: initialState,
      setTransitionSettings,
    } as unknown as DashboardController

    render(<TransitionSettingsPanel controller={controller} disabled={false} />)

    await user.click(screen.getByRole("combobox", { name: "Transition effect" }))
    await user.click(await screen.findByRole("option", { name: "Fade through black" }))

    await waitFor(() => {
      expect(setTransitionSettings).toHaveBeenCalledWith(true, 300, "fade_black")
    })
  })
})
