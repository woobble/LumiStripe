import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"

const api = vi.hoisted(() => ({
  getAudioSettings: vi.fn(),
  updateAudioSettings: vi.fn(),
  resetAudioSettings: vi.fn(),
  startAudioCalibration: vi.fn(),
  getAudioCalibration: vi.fn(),
  beginAudioCalibrationMusic: vi.fn(),
  finishAudioCalibration: vi.fn(),
}))

vi.mock("@/lib/api", () => ({ dashboardApi: api }))
vi.mock("@/components/dashboard/audio-telemetry", () => ({
  AudioHistoryChart: () => <div>Detailed signal history</div>,
  AudioTelemetryCards: () => <div>Detailed gate metrics</div>,
  useAudioTelemetry: () => ({
    telemetry: {
      fresh: true, input_level: 0.22, processed_level: 0.18, input_peak: 0.25,
      clipping_fraction: 0, gate_energy: 0.04, gate_onset: 0.03,
      gate_beat_density: 0.08, gate_brightness: 0.12,
    },
    history: [],
    connection: "connected",
  }),
}))

import { AudioTuningPanel } from "./audio-tuning-panel"

const tuningValues = {
  target_level: 0.36,
  noise_floor: 0.015,
  input_noise_floor: 0.003,
  hardware_gain_target: null,
  dynamic_response: 0.65,
  rms_attack: 0.45,
  rms_release: 0.12,
  band_attack: 0.4,
  band_release: 0.1,
  beat_release: 0.18,
  energy_threshold: 0.03,
  onset_threshold: 0.025,
  beat_density_threshold: 0.05,
  brightness_threshold: 0.08,
  spectral_balance_ratio: 0.35,
}

const audioSettings = {
  source: "mic",
  active_source: "mic",
  monitoring: true,
  active_device: "mic-usb",
  active_device_name: "USB microphone",
  devices: [{ selector: "mic-usb", name: "USB microphone", settings: tuningValues }],
  settings: tuningValues,
  sync_route: "mic|USB microphone|sink-main",
  sync_offset_ms: 0,
  configured_noise_floor: 0.015,
  hardware_gain_supported: false,
  hardware_gain_writable: false,
  hardware_gain_backend: null,
  hardware_gain_control: null,
  hardware_gain_value: null,
  hardware_gain_error: null,
  bluetooth: {},
  spotify: {},
  error: null,
}

const calibration = {
  session_id: "mic-session",
  phase: "quiet",
  status: "capturing",
  elapsed_seconds: 0,
  remaining_seconds: 8,
  result: null,
  error: null,
}

function changeSlider(label: string, value: number) {
  const group = screen.getByRole("group", { name: label })
  const input = group.querySelector<HTMLInputElement>('input[type="range"]')
  if (!input) throw new Error(`Slider input not found for ${label}`)
  fireEvent.change(input, { target: { value: String(value) } })
}

describe("audio tuning presentation", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    api.getAudioSettings.mockResolvedValue(audioSettings)
    api.updateAudioSettings.mockImplementation((_device: string, values: typeof tuningValues) => Promise.resolve({ ...audioSettings, settings: values }))
    api.resetAudioSettings.mockResolvedValue(audioSettings)
    api.startAudioCalibration.mockResolvedValue(calibration)
    api.getAudioCalibration.mockResolvedValue({ ...calibration, status: "waiting", phase: "waiting_music" })
    api.beginAudioCalibrationMusic.mockResolvedValue({
      ...calibration,
      status: "complete",
      phase: "music",
      result: {
        measured_floor: 0.002,
        measured_peak: 0.1,
        measured_music_level: 0.02,
        recommended_noise_floor: 0.015,
        recommended_input_noise_floor: 0.0022,
        recommended_target_level: 0.24,
      },
    })
    api.finishAudioCalibration.mockResolvedValue({})
  })

  it("keeps dirty values when switching Basic and Advanced views", async () => {
    const user = userEvent.setup()
    render(<AudioTuningPanel />, { wrapper: MemoryRouter })
    await screen.findByRole("group", { name: "Target level" })
    changeSlider("Target level", 0.365)
    expect(await screen.findByText("0.365")).toBeInTheDocument()

    const advanced = screen.getByRole("button", { name: "Advanced DSP settings and diagnostics" })
    await user.click(advanced)
    expect(advanced).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByText("Detailed gate metrics")).toBeInTheDocument()
    expect(screen.getByRole("group", { name: "Target level" })).toBeInTheDocument()
    expect(screen.getByText("0.365")).toBeInTheDocument()

    await user.click(advanced)
    expect(advanced).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByText("Detailed gate metrics")).not.toBeInTheDocument()
    expect(screen.getByText("0.365")).toBeInTheDocument()
  })

  it("preserves tuning drafts when a calibration refresh completes", async () => {
    const user = userEvent.setup()
    api.getAudioSettings
      .mockResolvedValueOnce(audioSettings)
      .mockResolvedValueOnce({
        ...audioSettings,
        active_device: "mic-other",
        active_device_name: "Other microphone",
        devices: [
          ...audioSettings.devices,
          { selector: "mic-other", name: "Other microphone", settings: { ...tuningValues, target_level: 0.6 } },
        ],
        settings: { ...tuningValues, target_level: 0.6 },
      })
    render(<AudioTuningPanel />, { wrapper: MemoryRouter })
    await screen.findByRole("group", { name: "Target level" })
    changeSlider("Target level", 0.365)
    expect(await screen.findByText("0.365")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Start quiet measurement" }))
    expect(screen.getByRole("button", { name: "Apply & save" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reset" })).toBeDisabled()
    await screen.findByRole("button", { name: "Measure music" })
    await user.click(screen.getByRole("button", { name: "Measure music" }))
    await user.click(await screen.findByRole("button", { name: "Apply" }))

    await waitFor(() => expect(api.getAudioSettings).toHaveBeenCalledTimes(2))
    expect(screen.getByText("0.365")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Apply & save" }))
    await waitFor(() => expect(api.updateAudioSettings).toHaveBeenCalledWith("mic-usb", expect.objectContaining({ target_level: 0.365 })))
  })

  it("applies and saves the edited tuning values and can reset the profile", async () => {
    const user = userEvent.setup()
    render(<AudioTuningPanel />, { wrapper: MemoryRouter })
    await screen.findByRole("group", { name: "Target level" })
    changeSlider("Target level", 0.365)
    await user.click(screen.getByRole("button", { name: "Apply & save" }))
    await waitFor(() => expect(api.updateAudioSettings).toHaveBeenCalledOnce())
    expect(api.updateAudioSettings).toHaveBeenCalledWith("mic-usb", expect.objectContaining({ target_level: expect.any(Number) }))

    await user.click(screen.getByRole("button", { name: "Reset" }))
    await waitFor(() => expect(api.resetAudioSettings).toHaveBeenCalledWith("mic-usb"))
  })

  it("shows editable hardware gain only when the selected input supports it", async () => {
    api.getAudioSettings.mockResolvedValueOnce({
      ...audioSettings,
      hardware_gain_supported: true,
      hardware_gain_writable: true,
      hardware_gain_value: 0.42,
    })
    const { rerender } = render(<AudioTuningPanel />, { wrapper: MemoryRouter })
    expect(await screen.findByRole("group", { name: "Hardware gain" })).toBeInTheDocument()
    expect(screen.queryByText(/Hardware gain is unavailable/)).not.toBeInTheDocument()

    api.getAudioSettings.mockResolvedValueOnce(audioSettings)
    rerender(<AudioTuningPanel key="software-only" />)
    expect(await screen.findByText(/Hardware gain is unavailable/)).toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "Hardware gain" })).not.toBeInTheDocument()
  })

  it("shows a retry state when audio settings cannot be loaded", async () => {
    api.getAudioSettings.mockRejectedValueOnce(new Error("Settings service unavailable"))
    render(<AudioTuningPanel />, { wrapper: MemoryRouter })
    await waitFor(() => expect(screen.getByText("Audio tuning unavailable")).toBeInTheDocument())
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled()
  })
})
