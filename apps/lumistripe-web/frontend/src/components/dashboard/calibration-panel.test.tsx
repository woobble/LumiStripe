import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { CalibrationPanel } from "./calibration-panel"
import type { DashboardController } from "@/hooks/use-dashboard"
import { initialState } from "@/test/fixtures"

describe("CalibrationPanel", () => {
  it("waits for an in-flight preview update before cancelling on unmount", async () => {
    const user = userEvent.setup()
    let resolveUpdate: ((applied: boolean) => void) | undefined
    const update = new Promise<boolean>((resolve) => {
      resolveUpdate = resolve
    })
    const state = {
      ...initialState,
      calibration: { ...initialState.calibration, active: false },
    }
    const controller = {
      state,
      pendingCommand: null,
      startCalibration: vi.fn(async () => {
        state.calibration.active = true
        return "calibration-session"
      }),
      updateCalibration: vi.fn(() => update),
      finishCalibration: vi.fn(async () => true),
    } as unknown as DashboardController

    const view = render(<CalibrationPanel controller={controller} />)
    await user.click(screen.getByRole("button", { name: "Start calibration" }))
    await user.click(screen.getByRole("button", { name: "Red" }))
    expect(controller.updateCalibration).toHaveBeenCalledOnce()

    view.unmount()
    expect(controller.finishCalibration).not.toHaveBeenCalled()

    resolveUpdate?.(true)
    await waitFor(() => {
      expect(controller.finishCalibration).toHaveBeenCalledWith(
        "calibration-session",
        false,
      )
    })
  })
})
