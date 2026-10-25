import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { StripeManagementPanel } from "@/components/dashboard/stripe-management-panel"
import type { DashboardController } from "@/hooks/use-dashboard"
import { initialState } from "@/test/fixtures"

describe("StripeManagementPanel", () => {
  it("applies a deferred topology update after focus leaves an untouched field", async () => {
    const user = userEvent.setup()
    const controller = {
      state: initialState,
      pendingCommand: null,
      updateStripes: vi.fn().mockResolvedValue(true),
      testStripe: vi.fn().mockResolvedValue(true),
    } as unknown as DashboardController

    const view = render(
      <MemoryRouter>
        <StripeManagementPanel controller={controller} />
      </MemoryRouter>,
    )

    const name = screen.getByLabelText("Name")
    await user.click(name)
    const updatedState = {
      ...initialState,
      revision: initialState.revision + 1,
      stripe_topology: {
        ...initialState.stripe_topology,
        outputs: initialState.stripe_topology.outputs.map((output) => ({ ...output, name: "Remote name" })),
      },
    }
    view.rerender(
      <MemoryRouter>
        <StripeManagementPanel controller={{ ...controller, state: updatedState }} />
      </MemoryRouter>,
    )
    expect(screen.getByLabelText("Name")).toHaveFocus()

    await user.click(document.body)
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Remote name"))
  })

  it("keeps a focused field mounted while newer stripe settings arrive", async () => {
    const user = userEvent.setup()
    const updateStripes = vi.fn().mockResolvedValue(true)
    const controller = {
      state: initialState,
      pendingCommand: null,
      updateStripes,
      testStripe: vi.fn().mockResolvedValue(true),
    } as unknown as DashboardController

    const view = render(
      <MemoryRouter>
        <StripeManagementPanel controller={controller} />
      </MemoryRouter>,
    )

    const name = screen.getByLabelText("Name")
    await user.click(name)
    expect(name).toHaveFocus()

    const updatedState = {
      ...initialState,
      revision: initialState.revision + 1,
      stripe_topology: {
        ...initialState.stripe_topology,
        outputs: initialState.stripe_topology.outputs.map((output) => ({ ...output, name: "Remote name" })),
      },
    }
    view.rerender(
      <MemoryRouter>
        <StripeManagementPanel controller={{ ...controller, state: updatedState }} />
      </MemoryRouter>,
    )

    const stillFocusedName = screen.getByLabelText("Name")
    expect(stillFocusedName).toBe(name)
    expect(stillFocusedName).toHaveFocus()

    await user.type(stillFocusedName, " edited")
    expect(stillFocusedName).toHaveValue("Primary edited")
    await user.click(screen.getByRole("button", { name: "Save & apply" }))

    await waitFor(() => expect(updateStripes).toHaveBeenCalledWith(expect.objectContaining({
      outputs: [expect.objectContaining({ name: "Primary edited" })],
    })))
  })

  it("saves the supply capacity and Pi reserve while preserving the LED-only limit", async () => {
    const user = userEvent.setup()
    const updateStripes = vi.fn().mockResolvedValue(true)
    const controller = {
      state: initialState,
      pendingCommand: null,
      updateStripes,
      testStripe: vi.fn().mockResolvedValue(true),
    } as unknown as DashboardController

    render(
      <MemoryRouter>
        <StripeManagementPanel controller={controller} />
      </MemoryRouter>,
    )

    await user.click(screen.getByRole("button", { name: "Shared supply limit off" }))

    const capacity = screen.getByLabelText("PSU capacity (W)")
    const reserve = screen.getByLabelText("Pi + buck input at PSU (W)")
    expect(capacity).toHaveValue(200)
    expect(reserve).toHaveValue(15)
    expect(screen.getByText(/LED allowance: 185\.0 W/)).toBeInTheDocument()

    fireEvent.change(reserve, { target: { value: "18" } })
    await user.click(screen.getByRole("button", { name: "Save & apply" }))

    await waitFor(() => expect(updateStripes).toHaveBeenCalledWith(expect.objectContaining({
      power_budget_enabled: false,
      power_budget_watts: null,
      shared_supply_enabled: true,
      supply_capacity_watts: 200,
      pi_reserve_watts: 18,
    })))
  })

  it("shows zone LED ranges and saves count and alternate direction", async () => {
    const user = userEvent.setup()
    const updateStripes = vi.fn().mockResolvedValue(true)
    const controller = {
      state: initialState,
      pendingCommand: null,
      updateStripes,
      testStripe: vi.fn().mockResolvedValue(true),
    } as unknown as DashboardController

    render(
      <MemoryRouter>
        <StripeManagementPanel controller={controller} />
      </MemoryRouter>,
    )

    expect(screen.getByText("Zone 1: LEDs 1–41")).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Zones per stripe"), { target: { value: "4" } })
    expect(screen.getByText("Zone 1: LEDs 1–11")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Alternate" }))
    await user.click(screen.getByRole("button", { name: "Save & apply" }))

    await waitFor(() => expect(updateStripes).toHaveBeenCalledWith(expect.objectContaining({
      outputs: [expect.objectContaining({ zone_count: 4, zone_pattern: "alternate" })],
    })))
  })
})
