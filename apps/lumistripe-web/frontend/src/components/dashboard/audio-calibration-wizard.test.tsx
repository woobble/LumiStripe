import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { AudioCalibrationWizard } from "./audio-calibration-wizard"

const api = vi.hoisted(() => ({
  startAudioCalibration: vi.fn(),
  getAudioCalibration: vi.fn(),
  beginAudioCalibrationMusic: vi.fn(),
  finishAudioCalibration: vi.fn(),
}))
vi.mock("@/lib/api", () => ({ dashboardApi: api }))

const capture = {
  session_id: "mic-session", phase: "quiet", status: "capturing",
  elapsed_seconds: 0, remaining_seconds: 8, result: null, error: null,
}

describe("microphone calibration", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    api.startAudioCalibration.mockResolvedValue(capture)
    api.getAudioCalibration.mockResolvedValue({ ...capture, phase: "waiting_music", status: "waiting" })
    api.beginAudioCalibrationMusic.mockResolvedValue({
      ...capture,
      phase: "music",
      status: "complete",
      result: { measured_floor: 0.002, measured_peak: 0.1, measured_music_level: 0.02,
        recommended_noise_floor: 0.015, recommended_input_noise_floor: 0.0022, recommended_target_level: 0.24 },
    })
    api.finishAudioCalibration.mockResolvedValue({})
  })

  it("waits for music, reviews recommendations, and applies only after review", async () => {
    const user = userEvent.setup()
    const applied = vi.fn()
    api.beginAudioCalibrationMusic.mockResolvedValue({ ...capture, phase: "music" })
    render(<AudioCalibrationWizard device="PCM2902" onApplied={applied} />)
    expect(screen.getByText(/Selected microphone: PCM2902/)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    expect(api.startAudioCalibration).toHaveBeenCalledWith("PCM2902", 8, true)
    await screen.findByRole("button", { name: "Measure music" })
    expect(api.finishAudioCalibration).not.toHaveBeenCalled()
    api.getAudioCalibration.mockResolvedValue({
      ...capture, phase: "music", status: "complete",
      result: { measured_floor: 0.002, measured_peak: 0.1, measured_music_level: 0.02,
        recommended_noise_floor: 0.015, recommended_input_noise_floor: 0.0022, recommended_target_level: 0.24 },
    })
    await user.click(screen.getByRole("button", { name: "Measure music" }))
    await screen.findByRole("button", { name: "Apply" })
    expect(api.beginAudioCalibrationMusic).toHaveBeenCalledWith("mic-session")
    expect(api.finishAudioCalibration).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "Apply" }))
    await waitFor(() => expect(applied).toHaveBeenCalledOnce())
    expect(api.finishAudioCalibration).toHaveBeenCalledWith("mic-session", true, { target_level: 0.24, noise_floor: 0.015 })
  })

  it("allows cancelling while capture is still running", async () => {
    const user = userEvent.setup()
    render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    await user.click(screen.getByRole("button", { name: "Cancel calibration" }))
    expect(api.finishAudioCalibration).toHaveBeenCalledWith("mic-session", false)
    await screen.findByRole("button", { name: "Start quiet measurement" })
  })

  it("shows failed measurements with discard and retry", async () => {
    const user = userEvent.setup()
    api.getAudioCalibration.mockResolvedValue({ ...capture, status: "failed", error: "Input is clipping. Lower capture gain." })
    render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    await screen.findByText("Input is clipping. Lower capture gain.")
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Discard and retry" }))
    await screen.findByRole("button", { name: "Start quiet measurement" })
  })

  it("keeps cancellation available after a polling error", async () => {
    const user = userEvent.setup()
    api.getAudioCalibration.mockRejectedValue(new Error("Connection interrupted"))
    render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    await screen.findByText("Connection interrupted")
    await user.click(screen.getByRole("button", { name: "Discard and retry" }))
    expect(api.finishAudioCalibration).toHaveBeenCalledWith("mic-session", false)
  })

  it("cancels a session when the selected microphone changes", async () => {
    const user = userEvent.setup()
    const view = render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    view.rerender(<AudioCalibrationWizard device="Built-in Mic" onApplied={vi.fn()} />)
    await waitFor(() => expect(api.finishAudioCalibration).toHaveBeenCalledWith("mic-session", false))
    expect(screen.queryByText(/Measuring room noise/)).not.toBeInTheDocument()
  })

  it("cancels an active session when navigating away", async () => {
    const user = userEvent.setup()
    const view = render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    view.unmount()
    await waitFor(() => expect(api.finishAudioCalibration).toHaveBeenCalledWith("mic-session", false))
  })

  it("announces accessible progress while the quiet sample is running", async () => {
    const user = userEvent.setup()
    api.getAudioCalibration.mockReturnValue(new Promise(() => undefined))
    render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    expect(await screen.findByRole("progressbar", { name: "Quiet measurement progress" })).toHaveAttribute("aria-valuemin", "0")
  })

  it("cancels a pending start after the wizard unmounts", async () => {
    const user = userEvent.setup()
    let resolveStart: ((value: typeof capture) => void) | undefined
    api.startAudioCalibration.mockReturnValueOnce(new Promise((resolve) => { resolveStart = resolve }))
    const view = render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    view.unmount()
    resolveStart?.(capture)
    await waitFor(() => expect(api.finishAudioCalibration).toHaveBeenCalledWith("mic-session", false))
  })

  it("keeps failed device-change cleanup visible and retryable", async () => {
    const user = userEvent.setup()
    api.finishAudioCalibration.mockRejectedValueOnce(new Error("Could not stop the session."))
    const view = render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    view.rerender(<AudioCalibrationWizard device="Built-in Mic" onApplied={vi.fn()} />)
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not stop the session.")
    await user.click(screen.getByRole("button", { name: "Discard and retry" }))
    await waitFor(() => expect(api.finishAudioCalibration).toHaveBeenCalledTimes(2))
    expect(screen.getByRole("button", { name: "Start quiet measurement" })).toBeInTheDocument()
  })

  it("shows start and music-step errors and allows retry", async () => {
    const user = userEvent.setup()
    api.startAudioCalibration.mockRejectedValueOnce(new Error("Microphone is unavailable."))
    render(<AudioCalibrationWizard device="PCM2902" onApplied={vi.fn()} />)
    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Microphone is unavailable.")

    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    api.beginAudioCalibrationMusic.mockRejectedValueOnce(new Error("Music measurement failed."))
    await user.click(await screen.findByRole("button", { name: "Measure music" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Music measurement failed.")
    await user.click(screen.getByRole("button", { name: "Measure music" }))
    expect(await screen.findByRole("button", { name: "Apply" })).toBeInTheDocument()
  })
})
