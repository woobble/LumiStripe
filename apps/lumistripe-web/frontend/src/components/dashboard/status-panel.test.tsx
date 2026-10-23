import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { StatusPanel } from "@/components/dashboard/status-panel"
import type { DashboardController } from "@/hooks/use-dashboard"
import { dashboardApi, type DashboardState, type SystemStats } from "@/lib/api"
import { initialState } from "@/test/fixtures"

const systemStats: SystemStats = {
  cpu_temperature_c: 53.5,
  cpu_usage_percent: 60,
  memory_used_bytes: 768 * 1024,
  memory_total_bytes: 1024 * 1024,
  memory_usage_percent: 75,
  disk_used_bytes: 4 * 1024 ** 3,
  disk_total_bytes: 8 * 1024 ** 3,
  disk_usage_percent: 50,
  uptime_seconds: 1234,
  sampled_at: "2026-01-01T00:00:00Z",
}

function renderStatus(
  runtime: DashboardState["runtime"],
  powerBudget = initialState.power_budget,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  const controller = {
    state: { ...initialState, runtime, power_budget: powerBudget },
    connection: "connected",
  } as DashboardController
  return render(
    <QueryClientProvider client={queryClient}>
      <StatusPanel controller={controller} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe("StatusPanel system resources", () => {
  it("shows host metrics in hardware mode and refreshes every five seconds", async () => {
    const getSystemStats = vi.spyOn(dashboardApi, "getSystemStats").mockResolvedValue(systemStats)
    vi.useFakeTimers()
    const view = renderStatus("hardware")

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getByText("System resources")).toBeInTheDocument()
    expect(screen.getByText("53.5 °C")).toBeInTheDocument()
    expect(screen.getByText("60.0%")).toBeInTheDocument()
    expect(screen.getByText("768 KiB / 1 MiB (75%)")).toBeInTheDocument()
    expect(getSystemStats).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999)
    })
    expect(getSystemStats).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
      await Promise.resolve()
    })
    expect(getSystemStats).toHaveBeenCalledTimes(2)

    view.unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(getSystemStats).toHaveBeenCalledTimes(2)
  })

  it("shows shared supply capacity, Pi reserve, and available LED power", () => {
    renderStatus("simulation", {
      ...initialState.power_budget,
      enabled: true,
      budget_watts: 185,
      estimated_watts: 120,
      applied_scale: 1,
      shared_supply_enabled: true,
      supply_capacity_watts: 200,
      pi_reserve_watts: 15,
      available_led_watts: 185,
    })

    expect(screen.getByText("PSU capacity")).toBeInTheDocument()
    expect(screen.getByText("Pi + buck reserve")).toBeInTheDocument()
    expect(screen.getByText("Available to LEDs")).toBeInTheDocument()
    expect(screen.getByText("200.0 W")).toBeInTheDocument()
    expect(screen.getByText("15.0 W")).toBeInTheDocument()
    expect(screen.getAllByText("185.0 W")).toHaveLength(2)
  })

  it("does not fetch or show host metrics in simulation mode", async () => {
    const getSystemStats = vi.spyOn(dashboardApi, "getSystemStats").mockResolvedValue(systemStats)
    renderStatus("simulation")

    expect(screen.queryByText("System resources")).not.toBeInTheDocument()
    await act(async () => {
      await Promise.resolve()
    })
    expect(getSystemStats).not.toHaveBeenCalled()
  })
})
