import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SystemUpdateNotice } from "@/components/dashboard/system-update-notice"
import { dashboardApi, type UpdateStatus } from "@/lib/api"

const availableUpdate: UpdateStatus = {
  supported: true,
  installed_commit: "a".repeat(40),
  available_commit: "b".repeat(40),
  update_available: true,
  working_tree_dirty: false,
  checked_at: "2026-01-01T00:00:00Z",
  check_error: null,
  install_state: "idle",
  install_started_at: null,
  install_finished_at: null,
  install_error: null,
}

afterEach(() => vi.restoreAllMocks())

describe("SystemUpdateNotice", () => {
  it("offers an install action and reports that installation is running", async () => {
    vi.spyOn(dashboardApi, "getUpdateStatus").mockResolvedValue(availableUpdate)
    vi.spyOn(dashboardApi, "checkForUpdates").mockResolvedValue(availableUpdate)
    vi.spyOn(dashboardApi, "installUpdate").mockResolvedValue({
      ...availableUpdate,
      install_state: "running",
      install_started_at: "2026-01-01T00:01:00Z",
    })
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false)

    render(<SystemUpdateNotice />)
    const install = await screen.findByRole("button", { name: "Install update" })
    fireEvent.click(install)

    expect(await screen.findByText("Installing LumiStripe update")).toBeInTheDocument()
    expect(dashboardApi.installUpdate).toHaveBeenCalledOnce()
    online.mockRestore()
  })

  it("explains when local checkout changes prevent installation", async () => {
    vi.spyOn(dashboardApi, "getUpdateStatus").mockResolvedValue({
      ...availableUpdate,
      working_tree_dirty: true,
    })
    vi.spyOn(dashboardApi, "checkForUpdates").mockResolvedValue(availableUpdate)
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false)

    render(<SystemUpdateNotice />)

    expect(await screen.findByText(/checkout has local changes/i)).toBeInTheDocument()
    await waitFor(() => expect(dashboardApi.getUpdateStatus).toHaveBeenCalled())
  })
})
