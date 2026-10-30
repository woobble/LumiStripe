import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"

import { NetworkSetupPage } from "@/components/dashboard/network-setup-page"
import { dashboardApi } from "@/lib/api"
import type { NetworkStatusResponse, WifiScanResponse } from "@/lib/api"

vi.mock("@/lib/api", () => ({
  dashboardApi: {
    getNetworkStatus: vi.fn(),
    scanWifi: vi.fn(),
    connectWifi: vi.fn(),
  },
}))

const status: NetworkStatusResponse = {
  manager_available: true,
  error: null,
  adapters: [
    {
      interface: "wlan0",
      state: "connected",
      connection: "LumiStripe hotspot",
      current_ssid: "LumiStripe",
      hotspot: true,
      connectable: false,
      reason: "This adapter is providing a hotspot.",
    },
    {
      interface: "wlan1",
      state: "disconnected",
      connection: null,
      current_ssid: null,
      hotspot: false,
      connectable: true,
      reason: null,
    },
  ],
}

const scan: WifiScanResponse = {
  interface: "wlan1",
  networks: [
    { ssid: "Home Network", security: "WPA2", signal: 87, secured: true },
    { ssid: "Guest", security: "Open", signal: 44, secured: false },
  ],
}

function renderNetworkSetupPage() {
  return render(<MemoryRouter><NetworkSetupPage /></MemoryRouter>)
}

describe("NetworkSetupPage", () => {
  beforeEach(() => {
    vi.mocked(dashboardApi.getNetworkStatus).mockReset().mockResolvedValue(status)
    vi.mocked(dashboardApi.scanWifi).mockReset().mockResolvedValue(scan)
    vi.mocked(dashboardApi.connectWifi).mockReset().mockResolvedValue({
      interface: "wlan1",
      ssid: "Home Network",
      connected: true,
    })
  })

  it("scans with the selected internet adapter and connects without retaining the password", async () => {
    const user = userEvent.setup()
    renderNetworkSetupPage()

    await screen.findByRole("combobox", { name: "Internet adapter" })
    await user.click(screen.getByRole("button", { name: "Scan for networks" }))
    expect(dashboardApi.scanWifi).toHaveBeenCalledWith("wlan1")

    const networkPicker = await screen.findByRole("combobox", { name: "Wi-Fi network" })
    await user.click(networkPicker)
    await user.click(await screen.findByRole("option", { name: /Home Network/ }))
    const password = screen.getByLabelText("Wi-Fi password")
    await user.type(password, "correct-horse")
    await user.click(screen.getByRole("button", { name: "Connect" }))

    await waitFor(() => expect(dashboardApi.connectWifi).toHaveBeenCalledWith(
      "wlan1",
      "Home Network",
      "correct-horse",
    ))
    expect(await screen.findByRole("status")).toHaveTextContent("Connected to Home Network on wlan1.")
    expect(password).toHaveValue("")
    expect(screen.getByText(/Hotspot adapters .* are blocked/)).toBeInTheDocument()
  })

  it("shows the manager error and prevents scanning when NetworkManager is unavailable", async () => {
    vi.mocked(dashboardApi.getNetworkStatus).mockResolvedValue({
      manager_available: false,
      error: "NetworkManager is unavailable.",
      adapters: [],
    })
    renderNetworkSetupPage()

    expect(await screen.findByRole("alert")).toHaveTextContent("NetworkManager is unavailable.")
    expect(screen.getByRole("button", { name: "Scan for networks" })).toBeDisabled()
    expect(dashboardApi.scanWifi).not.toHaveBeenCalled()
  })

  it("reports a failed password without exposing it in the page", async () => {
    const user = userEvent.setup()
    vi.mocked(dashboardApi.connectWifi).mockRejectedValue(new Error("Could not connect to that Wi-Fi network. Check the password and try again."))
    renderNetworkSetupPage()
    await user.click(await screen.findByRole("button", { name: "Scan for networks" }))
    await user.click(await screen.findByRole("combobox", { name: "Wi-Fi network" }))
    await user.click(await screen.findByRole("option", { name: /Home Network/ }))
    await user.type(screen.getByLabelText("Wi-Fi password"), "bad-password")
    await user.click(screen.getByRole("button", { name: "Connect" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("Check the password")
    expect(screen.queryByText("bad-password")).not.toBeInTheDocument()
  })

  it("clears networks and credentials when refresh moves to another adapter", async () => {
    const user = userEvent.setup()
    const refreshedStatus: NetworkStatusResponse = {
      ...status,
      adapters: [
        status.adapters[0],
        {
          ...status.adapters[1],
          state: "unavailable",
          connectable: false,
          reason: "Adapter is unavailable.",
        },
        {
          ...status.adapters[1],
          interface: "wlan2",
        },
      ],
    }
    vi.mocked(dashboardApi.getNetworkStatus)
      .mockReset()
      .mockResolvedValueOnce(status)
      .mockResolvedValueOnce(refreshedStatus)
    renderNetworkSetupPage()

    await screen.findByRole("combobox", { name: "Internet adapter" })
    await user.click(screen.getByRole("button", { name: "Scan for networks" }))
    await user.click(await screen.findByRole("combobox", { name: "Wi-Fi network" }))
    await user.click(await screen.findByRole("option", { name: /Home Network/ }))
    await user.type(screen.getByLabelText("Wi-Fi password"), "private-password")

    await user.click(screen.getByRole("button", { name: "Refresh adapters" }))

    expect(await screen.findByRole("combobox", { name: "Internet adapter" })).toHaveTextContent("wlan2")
    expect(screen.queryByRole("combobox", { name: "Wi-Fi network" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Wi-Fi password")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Scan for networks" })).toBeInTheDocument()
  })
})
