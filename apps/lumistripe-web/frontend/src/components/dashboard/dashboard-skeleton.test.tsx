import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { DashboardSkeleton } from "@/components/dashboard/dashboard-skeleton"

describe("DashboardSkeleton", () => {
  it("announces loading accessibly and provides a responsive dashboard layout", () => {
    render(<DashboardSkeleton />)

    const loadingState = screen.getByRole("status", { name: "Loading dashboard" })
    expect(loadingState).toHaveAttribute("aria-busy", "true")

    const contentGrid = loadingState.querySelector(".grid.gap-4")
    expect(contentGrid).toHaveClass("xl:grid-cols-[minmax(0,1.2fr)_minmax(19rem,0.8fr)]")
    expect(loadingState.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(10)
  })
})
