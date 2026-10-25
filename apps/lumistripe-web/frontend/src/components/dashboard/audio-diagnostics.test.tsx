import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { AudioDiagnostics } from "@/components/dashboard/audio-diagnostics"
import { getAudioDiagnostics, type AudioDiagnosticsInput } from "@/components/dashboard/audio-diagnostics-model"
import { emptyTelemetry } from "@/components/dashboard/audio-telemetry"
import type { AudioSettingsResponse, BluetoothStatusResponse } from "@/lib/api"

const readyOutput = {
  available: true,
  powered: true,
  adapter_alias: "LumiStripe",
  scanning: false,
  streaming: false,
  devices: [],
  connected_inputs: [],
  connected_outputs: [],
  connected_device: null,
  input_source: null,
  output_devices: [{ selector: "sink-main", name: "Built-in Output", volume: 0.5, muted: false, bluetooth: false, connected: true }],
  default_sink: "sink-main",
  output_volume: 0.5,
  output_muted: false,
  output_ready: true,
  capabilities: { operations: ["output_select", "output_volume", "output_mute"], max_inputs: 1, max_outputs: 1 },
  operation: null,
  operation_id: null,
  operation_state: "idle",
  error: null,
} as unknown as BluetoothStatusResponse

function input(overrides: Partial<AudioDiagnosticsInput> = {}): AudioDiagnosticsInput {
  return {
    settings: null,
    settingsLoading: false,
    settingsError: null,
    audioStatus: "Input: USB microphone",
    audioHealth: "healthy",
    configuredSource: "mic",
    activeSource: "mic",
    monitoring: true,
    telemetry: { ...emptyTelemetry, sequence: 5, fresh: true, input_peak: 0.1 },
    connection: "connected",
    bluetoothStatus: readyOutput,
    outputLoading: false,
    bluetoothError: null,
    ...overrides,
  }
}

describe("audio diagnostics", () => {
  it("does not report intentional Off or Demo sources as missing input", () => {
    expect(getAudioDiagnostics(input({ configuredSource: "off", activeSource: "off", monitoring: false })).map((issue) => issue.id)).toEqual([])
    expect(getAudioDiagnostics(input({ configuredSource: "demo", activeSource: "demo", monitoring: false })).map((issue) => issue.id)).toEqual([])
  })

  it("shows loading and configuration problems with direct recovery paths", () => {
    const loading = getAudioDiagnostics(input({ settingsLoading: true, configuredSource: null, activeSource: null, settings: null }))
    expect(loading.map((issue) => issue.id)).toContain("source-status-loading")

    const outputLoading = getAudioDiagnostics(input({ bluetoothStatus: null, outputLoading: true }))
    expect(outputLoading.map((issue) => issue.id)).toContain("output-status-loading")

    const configuration = getAudioDiagnostics(input({
      settings: { error: "Configured input device is missing." } as AudioSettingsResponse,
    }))
    expect(configuration.find((issue) => issue.id === "source-configuration")?.action).toMatchObject({ type: "link", to: "/setup/audio" })

    const selectedOutputMissing = getAudioDiagnostics(input({
      bluetoothStatus: { ...readyOutput, output_devices: [] },
    }))
    expect(selectedOutputMissing.find((issue) => issue.id === "output-selection-unavailable")?.action).toMatchObject({ type: "link", to: "/setup/audio/devices" })
  })

  it("reports Spotify setup failures only when Spotify is the configured or active source", () => {
    const settings = {
      spotify: { configured: false, connected: false, logged_in: false, is_active: false, status: "idle", position_ms: 0, volume: 0, shuffle: false, repeat: "off", error: null },
    } as unknown as AudioSettingsResponse
    const issues = getAudioDiagnostics(input({ settings, configuredSource: "spotify" }))
    expect(issues.find((issue) => issue.id === "spotify-configuration")?.action).toMatchObject({ type: "link", to: "/setup/audio" })
    expect(getAudioDiagnostics(input({ settings, configuredSource: "mic" })).map((issue) => issue.id)).not.toContain("spotify-configuration")
  })

  it("shows startup as informational, then identifies stale or missing active input", () => {
    const starting = getAudioDiagnostics(input({
      audioHealth: "starting",
      telemetry: { ...emptyTelemetry, sequence: 0 },
      connection: "connecting",
    }))
    expect(starting).toMatchObject([{ id: "input-stale", severity: "info", action: null }])

    const stale = getAudioDiagnostics(input({ connection: "reconnecting" }))
    expect(stale[0]).toMatchObject({ id: "input-stale", severity: "warning", action: { type: "link", to: "/setup/audio" } })

    const missing = getAudioDiagnostics(input({ monitoring: false, activeSource: null, audioStatus: "Microphone unavailable: device missing" }))
    expect(missing[0]).toMatchObject({ id: "input-unavailable", message: "Microphone unavailable: device missing" })
  })

  it("distinguishes live silence and clipping and links to tuning", () => {
    const silent = getAudioDiagnostics(input({ telemetry: { ...emptyTelemetry, sequence: 5, fresh: true, input_peak: 0 } }))
    expect(silent).toMatchObject([{ id: "input-silent", action: { type: "link", to: "/setup/audio/tuning" } }])

    const clipping = getAudioDiagnostics(input({ telemetry: { ...emptyTelemetry, sequence: 5, fresh: true, input_peak: 0.8, clipping_fraction: 0.05 } }))
    expect(clipping).toMatchObject([{ id: "input-clipping", message: expect.stringContaining("5%"), action: { type: "link", to: "/setup/audio/tuning" } }])
  })

  it("identifies unavailable, disconnected, and muted outputs with appropriate recovery", () => {
    const unavailable = getAudioDiagnostics(input({ bluetoothStatus: { ...readyOutput, output_ready: false } }))
    expect(unavailable.find((issue) => issue.id === "output-unavailable")?.action).toMatchObject({ type: "link", to: "/setup/audio/devices" })

    const disconnected = getAudioDiagnostics(input({
      bluetoothStatus: { ...readyOutput, output_ready: false, output_devices: [{ ...readyOutput.output_devices[0], connected: false }] },
    }))
    expect(disconnected.find((issue) => issue.id === "output-disconnected")?.title).toBe("Selected audio output is disconnected")

    const muted = getAudioDiagnostics(input({ bluetoothStatus: { ...readyOutput, output_muted: true } }))
    expect(muted.find((issue) => issue.id === "output-muted")?.action).toMatchObject({ type: "output-controls" })
  })

  it("offers retry for settings and output errors and clears the issue on recovery", () => {
    const unavailable = getAudioDiagnostics(input({ settingsError: "Network temporarily unavailable", bluetoothError: "PipeWire is restarting" }))
    expect(unavailable.map(({ id, action }) => [id, action?.type])).toEqual([
      ["settings-unavailable", "retry-settings"],
      ["output-status-error", "retry-output"],
    ])

    expect(getAudioDiagnostics(input())).toEqual([])
  })

  it("renders setup links and invokes retry actions", async () => {
    const user = userEvent.setup()
    const retrySettings = vi.fn()
    const retryOutput = vi.fn()
    const issues = getAudioDiagnostics(input({ settingsError: "Settings unavailable", bluetoothError: "Output status unavailable" }))
    const view = render(<MemoryRouter><AudioDiagnostics issues={issues} onRetrySettings={retrySettings} onRetryOutput={retryOutput} /></MemoryRouter>)

    await user.click(screen.getByRole("button", { name: /Retry settings/ }))
    await user.click(screen.getByRole("button", { name: /Retry output status/ }))
    expect(retrySettings).toHaveBeenCalledOnce()
    expect(retryOutput).toHaveBeenCalledOnce()

    view.rerender(<MemoryRouter><AudioDiagnostics issues={getAudioDiagnostics(input({ telemetry: { ...emptyTelemetry, sequence: 3, fresh: true, input_peak: 0 } }))} onRetrySettings={retrySettings} onRetryOutput={retryOutput} /></MemoryRouter>)
    expect(screen.getByRole("link", { name: "Check tuning" })).toHaveAttribute("href", "/setup/audio/tuning")
  })

  it("describes informational startup states without calling them problems", () => {
    const issues = getAudioDiagnostics(input({
      settingsLoading: true,
      configuredSource: null,
      activeSource: null,
      settings: null,
      bluetoothStatus: null,
      outputLoading: true,
    }))
    render(<MemoryRouter><AudioDiagnostics issues={issues} onRetrySettings={vi.fn()} onRetryOutput={vi.fn()} /></MemoryRouter>)
    expect(screen.getByText("Audio status is still being checked.")).toBeInTheDocument()
    expect(screen.getByText("Starting")).toBeInTheDocument()
  })
})
