import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { StripeManagementPanel } from "@/components/dashboard/stripe-management-panel"
import type { DashboardController } from "@/hooks/use-dashboard"
import { initialState } from "@/test/fixtures"

describe("StripeManagementPanel shared supply budget", () => {
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
})
