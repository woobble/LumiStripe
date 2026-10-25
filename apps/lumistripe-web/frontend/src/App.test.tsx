import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import App from "@/App"
import { emptyTelemetry } from "@/components/dashboard/audio-telemetry"
import type { BluetoothStatusResponse, SpotifyStatusResponse } from "@/lib/api"
import { animations, initialState, jsonResponse } from "@/test/fixtures"

const audioValues = {
  target_level: 0.36,
  noise_floor: 0.015,
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

const bluetoothStatus = {
  available: true,
  powered: true,
  adapter_alias: "LumiStripe",
  scanning: false,
  streaming: false,
  devices: [{ address: "AA:BB:CC:DD:EE:FF", name: "Dual-role device", paired: true, connected: false, roles: ["input", "output"] }],
  connected_inputs: [],
  connected_outputs: [],
  connected_device: null,
  input_source: null,
  output_devices: [],
  default_sink: null,
  output_volume: null,
  output_muted: false,
  output_ready: false,
  capabilities: { operations: [], max_inputs: 1, max_outputs: 1 },
  operation: null,
  operation_id: null,
  operation_state: "idle",
  error: null,
}

const bluetoothStatusWithOutputs: BluetoothStatusResponse = {
  ...(bluetoothStatus as unknown as BluetoothStatusResponse),
  output_devices: [
    { selector: "sink-main", name: "Built-in Output", volume: 0.5, muted: false, bluetooth: false, connected: true },
    { selector: "sink-bt", name: "Bluetooth Speaker", volume: 0.62, muted: false, bluetooth: true, connected: true },
  ],
  default_sink: "sink-main",
  output_volume: 0.5,
  output_ready: true,
  capabilities: { operations: ["output_select", "output_volume", "output_mute"], max_inputs: 1, max_outputs: 1 },
}

const spotifyStatus = {
  configured: true,
  connected: true,
  logged_in: true,
  is_active: true,
  device_name: "LumiStripe",
  status: "playing" as const,
  track: {
    uri: "spotify:track:test-track",
    name: "Test track",
    artists: ["Test artist"],
    album: "Test album",
    cover_url: null,
    duration_ms: 120_000,
  },
  position_ms: 10_000,
  duration_ms: 120_000,
  volume: 40,
  shuffle: false,
  repeat: "off" as const,
  error: null,
}

const audioSettings = {
  source: "mic",
  active_source: "mic",
  monitoring: true,
  sync_route: "mic|USB Mic|sink-main",
  sync_offset_ms: -85,
  active_device: "2",
  fallback_device: "2",
  active_device_name: "USB Mic",
  devices: [
    { selector: "2", name: "USB Mic", settings: audioValues },
    { selector: "3", name: "Built-in Mic", settings: audioValues },
  ],
  settings: audioValues,
  configured_noise_floor: 0.015,
  hardware_gain_supported: false,
  hardware_gain_writable: false,
  hardware_gain_backend: null,
  hardware_gain_control: null,
  hardware_gain_value: null,
  hardware_gain_error: null,
  error: null,
  bluetooth: bluetoothStatus,
  spotify: spotifyStatus,
}

function Router({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>
}

function emitAudioSourceState(revision: number, configured: string, active: string, monitoring: boolean) {
  const stateSocket = MockWebSocket.instances.find((instance) => instance.url.endsWith("/ws/state"))
  if (!stateSocket) throw new Error("Dashboard state WebSocket was not created")
  act(() => stateSocket.emit({
    ...initialState,
    revision,
    configured_audio_source: configured,
    active_audio_source: active,
    audio_monitoring: monitoring,
  }))
}

class MockWebSocket {
  static instances: MockWebSocket[] = []
  readonly url: string
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    queueMicrotask(() => this.onopen?.())
  }

  close() {}

  disconnect() {
    this.onclose?.(new CloseEvent("close", { code: 1006 }))
  }

  emit(state: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(state) }))
  }
}

describe("App", () => {
  let pendingBrightnessResponse: Promise<Response> | null = null
  let pendingSpotifyControlResponse: Promise<Response> | null = null
  let pendingSyncCalibrationStart: Promise<Response> | null = null
  let pendingSyncCalibrationStop: Promise<Response> | null = null
  let spotifyStatusResponse: SpotifyStatusResponse = spotifyStatus
  let spotifySettingsStatus: SpotifyStatusResponse = spotifyStatus
  let failAudioSourceChange = false
  let failAudioDeviceChange = false
  let failOutputVolume = false
  let failOutputMute = false
  let currentBluetoothStatus: BluetoothStatusResponse = bluetoothStatus as unknown as BluetoothStatusResponse
  let currentSyncRoute = audioSettings.sync_route
  let currentAudioSource = audioSettings.source
  let currentMicrophone = audioSettings.active_device
  let currentSyncOffsets: Record<string, number> = {}
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const path = String(input)
    if (path === "/api/auth/status") {
      return jsonResponse({ required: false, authenticated: true })
    }
    if (path === "/api/animations") return jsonResponse({ items: animations })
    if (path === "/api/audio/settings" || path === "/api/audio/settings/reset") {
      const microphone = audioSettings.devices.find((device) => device.selector === currentMicrophone)
      return jsonResponse({ ...audioSettings, source: currentAudioSource, active_device: currentMicrophone, active_device_name: microphone?.name ?? null, spotify: spotifySettingsStatus, bluetooth: currentBluetoothStatus, sync_route: currentSyncRoute, sync_offset_ms: currentSyncOffsets[currentSyncRoute] ?? 0 })
    }
    if (path === "/api/audio/bluetooth" || path === "/api/audio/bluetooth/connect") {
      return jsonResponse(currentBluetoothStatus)
    }
    if (path === "/api/audio/bluetooth/alias") {
      const body = JSON.parse(String(init?.body)) as { alias: string }
      currentBluetoothStatus = { ...currentBluetoothStatus, adapter_alias: body.alias }
      return jsonResponse(currentBluetoothStatus)
    }
    if (path === "/api/audio/spotify") return jsonResponse(spotifyStatusResponse)
    if (path === "/api/audio/source") {
      if (failAudioSourceChange) return jsonResponse({ detail: "Audio source unavailable." }, 503)
      const body = JSON.parse(String(init?.body)) as { source: string }
      currentAudioSource = body.source
      const microphone = audioSettings.devices.find((device) => device.selector === currentMicrophone)
      const inputName = body.source === "mic" ? microphone?.name ?? "USB Mic" : body.source === "bluetooth" ? "Dual-role device" : body.source === "spotify" ? "Spotify" : body.source
      currentSyncRoute = `${body.source}|${inputName}|${currentBluetoothStatus.default_sink ?? "sink-main"}`
      return jsonResponse({ ...audioSettings, bluetooth: currentBluetoothStatus, source: body.source, active_source: body.source, monitoring: body.source !== "off", sync_route: currentSyncRoute, sync_offset_ms: currentSyncOffsets[currentSyncRoute] ?? 0 })
    }
    if (path === "/api/audio/output") {
      const body = JSON.parse(String(init?.body)) as { selector: string }
      const output = currentBluetoothStatus.output_devices.find((device) => device.selector === body.selector)
      currentBluetoothStatus = { ...currentBluetoothStatus, default_sink: body.selector, output_volume: output?.volume ?? null }
      const microphone = audioSettings.devices.find((device) => device.selector === currentMicrophone)
      const inputName = currentAudioSource === "mic" ? microphone?.name ?? "USB Mic" : currentAudioSource === "bluetooth" ? "Dual-role device" : currentAudioSource === "spotify" ? "Spotify" : currentAudioSource
      currentSyncRoute = `${currentAudioSource}|${inputName}|${body.selector}`
      return jsonResponse(currentBluetoothStatus)
    }
    if (path === "/api/audio/output/volume") {
      if (failOutputVolume) return jsonResponse({ detail: "Output volume could not be applied." }, 503)
      const body = JSON.parse(String(init?.body)) as { selector: string; volume: number }
      currentBluetoothStatus = {
        ...currentBluetoothStatus,
        output_volume: body.volume,
        output_devices: currentBluetoothStatus.output_devices.map((device) => device.selector === body.selector ? { ...device, volume: body.volume } : device),
      }
      return jsonResponse(currentBluetoothStatus)
    }
    if (path === "/api/audio/output/mute") {
      if (failOutputMute) return jsonResponse({ detail: "Output mute could not be applied." }, 503)
      const body = JSON.parse(String(init?.body)) as { selector: string; muted: boolean }
      currentBluetoothStatus = {
        ...currentBluetoothStatus,
        output_muted: body.muted,
        output_devices: currentBluetoothStatus.output_devices.map((device) => device.selector === body.selector ? { ...device, muted: body.muted } : device),
      }
      return jsonResponse(currentBluetoothStatus)
    }
    if (path === "/api/audio/sync-offset") {
      const body = JSON.parse(String(init?.body)) as { offset_ms: number }
      currentSyncOffsets[currentSyncRoute] = body.offset_ms
      return jsonResponse({ ...audioSettings, bluetooth: currentBluetoothStatus, sync_route: currentSyncRoute, sync_offset_ms: body.offset_ms })
    }
    if (path === "/api/audio/sync-calibration") {
      const body = JSON.parse(String(init?.body)) as { active: boolean }
      if (body.active && pendingSyncCalibrationStart) return pendingSyncCalibrationStart
      if (!body.active && pendingSyncCalibrationStop) return pendingSyncCalibrationStop
      return jsonResponse({ start_at_ms: body.active ? Date.now() + 250 : null, sync_route: currentSyncRoute, sync_offset_ms: currentSyncOffsets[currentSyncRoute] ?? 0 })
    }
    if (path === "/api/audio/spotify/control") {
      if (pendingSpotifyControlResponse) return pendingSpotifyControlResponse
      return jsonResponse(spotifyStatus)
    }
    if (path === "/api/audio/device") {
      if (failAudioDeviceChange) return jsonResponse({ detail: "Microphone input could not be changed." }, 503)
      const body = JSON.parse(String(init?.body)) as { device: string }
      const device = audioSettings.devices.find((item) => item.selector === body.device)
      currentMicrophone = body.device
      currentSyncRoute = `mic|${device?.name ?? "unknown"}|${currentBluetoothStatus.default_sink ?? "sink-main"}`
      return jsonResponse({ ...audioSettings, active_device: body.device, active_device_name: device?.name ?? null, sync_route: currentSyncRoute, sync_offset_ms: currentSyncOffsets[currentSyncRoute] ?? 0 })
    }
    if (path === "/api/startup") {
      const enabled = init?.body
        ? (JSON.parse(String(init.body)) as { restore_last_state: boolean }).restore_last_state
        : false
      return jsonResponse({
        restore_last_state: enabled,
        remembered: { mode: "static", solid_color: "#7C3AED", animation: "aurora_wave", brightness: 0.72, blackout: false },
      })
    }
    if (path === "/api/calibration/session") {
      return jsonResponse({
        session_id: "calibration-session",
        state: {
          ...initialState,
          revision: 6,
          calibration: { active: true, output_index: 0, pattern: "white", expires_in_seconds: 300 },
        },
      })
    }
    if (path === "/api/calibration/session/calibration-session/finish") {
      return jsonResponse({
        ...initialState,
        revision: 8,
        calibration: { active: false, output_index: null, pattern: null, expires_in_seconds: null },
      })
    }
    if (path === "/api/calibration/session/calibration-session") {
      const body = JSON.parse(String(init?.body)) as { red: number; green: number; blue: number; pattern: string }
      return jsonResponse({
        ...initialState,
        revision: 7,
        color_corrections: [{ ...initialState.color_corrections[0], red: body.red, green: body.green, blue: body.blue }],
        calibration: { active: true, output_index: 0, pattern: body.pattern, expires_in_seconds: 300 },
      })
    }
    if (path === "/api/blackout") return jsonResponse({ ...initialState, revision: 2, blackout: true })
    if (path === "/api/animation") {
      const body = JSON.parse(String(init?.body)) as { name: string }
      return jsonResponse({ ...initialState, revision: 3, animation: body.name })
    }
    if (path === "/api/mode") {
      const body = JSON.parse(String(init?.body)) as { mode: string; color?: string }
      return jsonResponse({
        ...initialState,
        revision: body.color ? 5 : 4,
        mode: body.mode,
        solid_color: body.color?.toUpperCase() ?? initialState.solid_color,
      })
    }
    if (path === "/api/brightness" && pendingBrightnessResponse) {
      return pendingBrightnessResponse
    }
    return jsonResponse(initialState)
  })

  beforeEach(() => {
    MockWebSocket.instances = []
    pendingBrightnessResponse = null
    pendingSpotifyControlResponse = null
    pendingSyncCalibrationStart = null
    pendingSyncCalibrationStop = null
    spotifyStatusResponse = spotifyStatus
    spotifySettingsStatus = spotifyStatus
    failAudioSourceChange = false
    failAudioDeviceChange = false
    failOutputVolume = false
    failOutputMute = false
    currentBluetoothStatus = bluetoothStatus as unknown as BluetoothStatusResponse
    currentSyncRoute = audioSettings.sync_route
    currentAudioSource = audioSettings.source
    currentMicrophone = audioSettings.active_device
    currentSyncOffsets = { "mic|USB Mic|sink-main": -85, "mic|USB Mic|sink-bt": 0, "spotify|Spotify|sink-main": 0, "bluetooth|Dual-role device|sink-main": 25 }
    fetchMock.mockClear()
    vi.stubGlobal("fetch", fetchMock)
    vi.stubGlobal("WebSocket", MockWebSocket)
  })

  it("keeps the local brightness draft while live state arrives", async () => {
    pendingBrightnessResponse = new Promise(() => undefined)
    const { container } = render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    const input = container.querySelector<HTMLInputElement>('input[type="range"]')
    expect(input).not.toBeNull()
    fireEvent.change(input!, { target: { value: "35" } })
    expect(screen.getByText("35%")).toBeInTheDocument()

    act(() => MockWebSocket.instances[0].emit({ ...initialState, revision: 20, brightness: 0.9 }))
    expect(screen.getByText("35%")).toBeInTheDocument()
  })

  it("renders the phone controls and applies blackout immediately", async () => {
    const user = userEvent.setup()
    const { container } = render(<App />, { wrapper: Router })

    expect(await screen.findByText(/aurora wave/i)).toBeInTheDocument()
    expect(container.querySelectorAll("[data-slot=slider-thumb]")).toHaveLength(1)
    await user.click(screen.getByRole("button", { name: "Blackout" }))

    expect(await screen.findByRole("button", { name: "Restore lights" })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/blackout",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ enabled: true }) })
    )
  })

  it("keeps the live preview off until it is explicitly enabled", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    expect(screen.queryByTestId("live-preview")).not.toBeInTheDocument()
    expect(MockWebSocket.instances.some((socket) => socket.url.endsWith("/ws/preview"))).toBe(false)

    await user.click(screen.getByRole("button", { name: "Enable live preview" }))
    expect(await screen.findByTestId("live-preview")).toBeInTheDocument()
    expect(MockWebSocket.instances.some((socket) => socket.url.endsWith("/ws/preview"))).toBe(true)

    await user.click(screen.getByRole("button", { name: "Hide live preview" }))
    await waitFor(() => expect(screen.queryByTestId("live-preview")).not.toBeInTheDocument())
  })

  it("keeps the routed bottom navigation fixed and evenly sized", async () => {
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    const navigation = screen.getByRole("navigation", { name: "Dashboard" })
    expect(navigation).toHaveClass("grid-cols-4", "items-stretch")
    expect(navigation.parentElement?.parentElement).toHaveClass("fixed", "inset-x-0", "bottom-0")
    expect(screen.getByRole("link", { name: "Control" })).toHaveClass("w-full")
    expect(screen.getByRole("link", { name: "Audio" })).toHaveClass("w-full")
    expect(screen.getByRole("link", { name: "Setup" })).toHaveClass("w-full")
    expect(screen.getByRole("link", { name: "Status" })).toHaveClass("w-full")
  })

  it("renders independent stripe targets as a full-width segmented control", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    act(() => MockWebSocket.instances[0].emit({
      ...initialState,
      revision: 2,
      stripe_topology: {
        layout: "independent",
        outputs: [
          { ...initialState.stripe_topology.outputs[0], id: "left", name: "Left" },
          { ...initialState.stripe_topology.outputs[0], id: "right", name: "Right" },
        ],
      },
      stripe_playback: [
        { ...initialState.stripe_playback[0], stripe_id: "left" },
        { ...initialState.stripe_playback[0], stripe_id: "right", brightness: 0.4 },
      ],
    }))

    const targets = await screen.findByRole("group", { name: "Control target" })
    expect(targets).toHaveClass("grid", "w-full", "gap-1")
    expect(screen.getByRole("button", { name: "All" })).toHaveClass("border-0", "min-w-0")
    await user.click(screen.getByRole("button", { name: "Right" }))
    expect(screen.getByText("Right", { selector: "span.text-violet-200" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Right" })).toHaveAttribute("data-pressed")
    expect(screen.getByText("40%")).toBeInTheDocument()
  })

  it("runs a guided color calibration session", async () => {
    const user = userEvent.setup()
    const { container } = render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    await user.click(await screen.findByRole("link", { name: "Color" }))
    expect(await screen.findByRole("heading", { name: "Color calibration" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Start calibration" }))

    expect(await screen.findByText("Channel gains")).toBeInTheDocument()
    expect(container.querySelectorAll("[data-slot=slider-thumb]")).toHaveLength(3)
    await user.click(screen.getByRole("button", { name: "Red" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/calibration/session/calibration-session",
      expect.objectContaining({ method: "PUT" }),
    ))
    await user.click(screen.getByRole("button", { name: "Save profile" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/calibration/session/calibration-session/finish",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ save: true }) }),
    ))
  })

  it("adds and applies a second stripe from Setup", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    expect(await screen.findByText("Layout")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Add stripe" }))
    await user.click(screen.getByRole("button", { name: "Save & apply" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/stripes",
      expect.objectContaining({ method: "PUT" }),
    ))
    const request = fetchMock.mock.calls.find(([path]) => String(path) === "/api/stripes")
    const body = JSON.parse(String(request?.[1]?.body)) as { outputs: unknown[] }
    expect(body.outputs).toHaveLength(2)
  })

  it("selects the audio input from Setup", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    await user.click(await screen.findByRole("combobox", { name: "Microphone input device" }))
    await user.click(await screen.findByRole("option", { name: "Built-in Mic" }))
    await user.click(screen.getByRole("button", { name: "Save microphone input" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/device",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ device: "3" }) }),
    ))
  })

  it("keeps a failed microphone selection as a draft and shows the API error", async () => {
    const user = userEvent.setup()
    failAudioDeviceChange = true
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    await user.click(await screen.findByRole("combobox", { name: "Microphone input device" }))
    await user.click(await screen.findByRole("option", { name: "Built-in Mic" }))
    await user.click(screen.getByRole("button", { name: "Save microphone input" }))

    expect(await screen.findByText("Microphone input could not be changed.")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Microphone input device" })).toHaveTextContent("Built-in Mic")
    expect(screen.getByRole("button", { name: "Save microphone input" })).toBeEnabled()
  })

  it("shows configured and active audio sources and updates them after a source change", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))

    const status = await screen.findByText("Configured source")
    expect(status.parentElement).toHaveTextContent("Microphone")
    expect(screen.getByText("Active source").parentElement).toHaveTextContent("Microphone")
    expect(screen.getByText("Stream connection").parentElement).toHaveTextContent("Audio stream connected")

    await user.click(screen.getByRole("combobox", { name: "Audio input source" }))
    await user.click(await screen.findByRole("option", { name: "Spotify Connect" }))
    await user.click(screen.getByRole("button", { name: "Save audio input" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/source",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ source: "spotify" }) }),
    ))
    await waitFor(() => expect(screen.getByText("Configured source").parentElement).toHaveTextContent("Spotify Connect"))
    expect(screen.getByText("Active source").parentElement).toHaveTextContent("Spotify Connect")
  })

  it("keeps an unsaved source selection and reports a failed source change", async () => {
    const user = userEvent.setup()
    failAudioSourceChange = true
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    await user.click(await screen.findByRole("combobox", { name: "Audio input source" }))
    await user.click(await screen.findByRole("option", { name: "Spotify Connect" }))
    await user.click(screen.getByRole("button", { name: "Save audio input" }))

    expect(await screen.findByText("Audio source unavailable.")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Audio input source" })).toHaveTextContent("Spotify Connect")
    expect(screen.getByText("Configured source").parentElement).toHaveTextContent("Microphone")
  })

  it("navigates all four setup sections with a compact keyboard-accessible tab grid", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))

    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    expect(audioNav).toHaveClass("grid-cols-2")
    expect(within(audioNav).getByRole("link", { name: "Sources" })).toHaveAttribute("href", "/setup/audio")
    expect(within(audioNav).getByRole("link", { name: "Devices" })).toHaveAttribute("href", "/setup/audio/devices")
    expect(within(audioNav).getByRole("link", { name: "Tuning" })).toHaveAttribute("href", "/setup/audio/tuning")
    expect(within(audioNav).getByRole("link", { name: "Sync" })).toHaveAttribute("href", "/setup/audio/sync")
    expect(within(audioNav).getByRole("link", { name: "Sources" })).toHaveAttribute("aria-current", "page")
    expect(fetchMock.mock.calls.filter(([path]) => String(path) === "/api/audio/bluetooth")).toHaveLength(0)

    within(audioNav).getByRole("link", { name: "Sources" }).focus()
    await user.tab()
    expect(within(audioNav).getByRole("link", { name: "Devices" })).toHaveFocus()
    await user.keyboard("{Enter}")
    expect(await screen.findByRole("heading", { name: "Audio outputs" })).toBeInTheDocument()
    expect(within(audioNav).getByRole("link", { name: "Devices" })).toHaveAttribute("aria-current", "page")
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => String(path) === "/api/audio/bluetooth").length).toBeGreaterThan(0))
    const bluetoothPolls = fetchMock.mock.calls.filter(([path]) => String(path) === "/api/audio/bluetooth").length

    await user.click(within(audioNav).getByRole("link", { name: "Tuning" }))
    expect(await screen.findByRole("heading", { name: "Audio tuning" })).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([path]) => String(path) === "/api/audio/bluetooth")).toHaveLength(bluetoothPolls)
    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    expect(await screen.findByText("Route: mic|USB Mic|sink-main")).toBeInTheDocument()
    await user.click(within(audioNav).getByRole("link", { name: "Sources" }))
    expect(await screen.findByRole("combobox", { name: "Audio input source" })).toBeInTheDocument()
  })

  it("preserves unsaved source and microphone drafts when audio settings refresh", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))

    await user.click(await screen.findByRole("combobox", { name: "Audio input source" }))
    await user.click(await screen.findByRole("option", { name: "Spotify Connect" }))
    await user.click(await screen.findByRole("combobox", { name: "Microphone input device" }))
    await user.click(await screen.findByRole("option", { name: "Built-in Mic" }))

    await user.click(screen.getByRole("button", { name: "Refresh microphone devices" }))

    expect(await screen.findByRole("combobox", { name: "Audio input source" })).toHaveTextContent("Spotify Connect")
    expect(await screen.findByRole("combobox", { name: "Microphone input device" })).toHaveTextContent("Built-in Mic")
    expect(screen.getByRole("button", { name: "Save microphone input" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "Save audio input" })).toBeEnabled()

    const audioNav = screen.getByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    expect(await screen.findByRole("heading", { name: "Leave without saving?" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Stay" }))
    expect(screen.getByRole("combobox", { name: "Audio input source" })).toHaveTextContent("Spotify Connect")
  })

  it("keeps output and sync settings attached to their selected routes", async () => {
    const user = userEvent.setup()
    currentBluetoothStatus = bluetoothStatusWithOutputs
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    expect(await screen.findByText("Output volume")).toBeInTheDocument()
    await user.click(await screen.findByRole("combobox", { name: "Audio output device" }))
    await user.click(await screen.findByRole("option", { name: "Bluetooth Speaker · Bluetooth" }))

    expect(screen.getByText("62%")).toBeInTheDocument()

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-bt" }) }),
    ))
    const outputVolume = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="range"]'))[0]
    expect(outputVolume).toBeDefined()
    fireEvent.change(outputVolume!, { target: { value: "0.77" } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output/volume",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-bt", volume: 0.77 }) }),
    ))

    await user.click(screen.getByRole("switch", { name: "Enable audio output" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output/mute",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-bt", muted: true }) }),
    ))

    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    expect(await screen.findByText("Route: mic|USB Mic|sink-bt")).toBeInTheDocument()
    expect(screen.getByText("0 ms")).toBeInTheDocument()
    const syncOffset = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="range"]'))[0]
    expect(syncOffset).toBeDefined()
    fireEvent.change(syncOffset!, { target: { value: "125" } })
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    expect(await screen.findByRole("heading", { name: "Leave without saving?" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Stay" }))
    expect(screen.getByText("+125 ms")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Save route offset" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/sync-offset",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ offset_ms: 125 }) }),
    ))
    expect(screen.getByText("+125 ms")).toBeInTheDocument()

    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    await user.click(await screen.findByRole("combobox", { name: "Audio output device" }))
    await user.click(await screen.findByRole("option", { name: "Built-in Output" }))
    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    expect(await screen.findByText("Route: mic|USB Mic|sink-main")).toBeInTheDocument()
    expect(screen.getByText("-85 ms")).toBeInTheDocument()

    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    await user.click(await screen.findByRole("combobox", { name: "Audio output device" }))
    await user.click(await screen.findByRole("option", { name: "Bluetooth Speaker · Bluetooth" }))
    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    expect(await screen.findByText("+125 ms")).toBeInTheDocument()
  })

  it("stops an active calibration cue when navigating to another setup section", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    await user.click(await screen.findByRole("button", { name: "Play sync cue" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/sync-calibration",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ active: true }) }),
    ))
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    await screen.findByRole("heading", { name: "Audio outputs" })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/sync-calibration",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ active: false }) }),
    ))
  })

  it("preserves an edited Bluetooth alias across refresh and guards leaving Devices", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    const aliasInput = await screen.findByRole("textbox", { name: "Visible Bluetooth name" })
    await user.clear(aliasInput)
    await user.type(aliasInput, "Studio Speaker")
    await user.click(screen.getByRole("button", { name: "Refresh Bluetooth status" }))
    expect(await screen.findByRole("textbox", { name: "Visible Bluetooth name" })).toHaveValue("Studio Speaker")

    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    expect(await screen.findByRole("heading", { name: "Leave without saving?" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Stay" }))
    expect(screen.getByRole("textbox", { name: "Visible Bluetooth name" })).toHaveValue("Studio Speaker")
    await user.click(screen.getByRole("button", { name: "Save name" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/bluetooth/alias",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ alias: "Studio Speaker" }) }),
    ))
  })

  it("respects Bluetooth input and output role capability limits", async () => {
    const user = userEvent.setup()
    currentBluetoothStatus = {
      ...(bluetoothStatus as unknown as BluetoothStatusResponse),
      capabilities: { operations: ["connect"], max_inputs: 1, max_outputs: 1 },
      connected_inputs: [{ address: "11:22:33:44:55:66", name: "Existing input", paired: true, connected: true, roles: ["input"] }],
    }
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))

    expect(await screen.findByRole("button", { name: "Connect as input" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Connect as output" })).toBeEnabled()
    await user.click(screen.getByRole("button", { name: "Connect as output" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/bluetooth/connect",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ address: "AA:BB:CC:DD:EE:FF", role: "output" }) }),
    ))
  })

  it("shows unavailable Bluetooth service and provider error on Devices", async () => {
    const user = userEvent.setup()
    currentBluetoothStatus = {
      ...(bluetoothStatus as unknown as BluetoothStatusResponse),
      available: false,
      error: "Bluetooth provider unavailable.",
      capabilities: { operations: [], max_inputs: 1, max_outputs: 1 },
    }
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))

    expect(await screen.findByText(/Bluetooth tools are unavailable/)).toBeInTheDocument()
    expect(screen.getByText("Bluetooth provider unavailable.")).toBeInTheDocument()
    expect(screen.getByRole("switch", { name: "Enable Bluetooth" })).toHaveAttribute("aria-disabled", "true")
  })

  it("keeps sync offsets separate when the configured input source changes", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    expect(await screen.findByText("-85 ms")).toBeInTheDocument()
    const offset = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="range"]'))[0]
    fireEvent.change(offset!, { target: { value: "55" } })
    await user.click(screen.getByRole("button", { name: "Save route offset" }))
    await waitFor(() => expect(screen.getByText("+55 ms")).toBeInTheDocument())

    await user.click(within(audioNav).getByRole("link", { name: "Sources" }))
    await user.click(await screen.findByRole("combobox", { name: "Audio input source" }))
    await user.click(await screen.findByRole("option", { name: "Bluetooth input" }))
    await user.click(screen.getByRole("button", { name: "Save audio input" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/source",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ source: "bluetooth" }) }),
    ))

    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    expect(await screen.findByText("Route: bluetooth|Dual-role device|sink-main")).toBeInTheDocument()
    expect(screen.getByText("+25 ms")).toBeInTheDocument()

    await user.click(within(audioNav).getByRole("link", { name: "Sources" }))
    await user.click(await screen.findByRole("combobox", { name: "Audio input source" }))
    await user.click(await screen.findByRole("option", { name: "Microphone" }))
    await user.click(screen.getByRole("button", { name: "Save audio input" }))
    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    expect(await screen.findByText("+55 ms")).toBeInTheDocument()
  })

  it("stops the calibration cue if its start request finishes after navigation", async () => {
    const user = userEvent.setup()
    let resolveStart: ((response: Response) => void) | undefined
    pendingSyncCalibrationStart = new Promise<Response>((resolve) => { resolveStart = resolve })
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    await user.click(await screen.findByRole("button", { name: "Play sync cue" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/sync-calibration",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ active: true }) }),
    ))

    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    await screen.findByRole("heading", { name: "Audio outputs" })
    resolveStart?.(await jsonResponse({ start_at_ms: Date.now() + 250, sync_route: currentSyncRoute, sync_offset_ms: 0 }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/sync-calibration",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ active: false }) }),
    ))
  })

  it("waits for calibration cleanup before changing the output route", async () => {
    const user = userEvent.setup()
    currentBluetoothStatus = bluetoothStatusWithOutputs
    let resolveStop: ((response: Response) => void) | undefined
    pendingSyncCalibrationStop = new Promise<Response>((resolve) => { resolveStop = resolve })
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Sync" }))
    await user.click(await screen.findByRole("button", { name: "Play sync cue" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/sync-calibration",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ active: true }) }),
    ))

    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    await screen.findByRole("heading", { name: "Audio outputs" })
    await user.click(await screen.findByRole("combobox", { name: "Audio output device" }))
    await user.click(await screen.findByRole("option", { name: "Bluetooth Speaker · Bluetooth" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/sync-calibration",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ active: false }) }),
    ))
    expect(fetchMock.mock.calls.some(([path]) => String(path) === "/api/audio/output")).toBe(false)

    resolveStop?.(await jsonResponse({ start_at_ms: null, sync_route: currentSyncRoute, sync_offset_ms: 0 }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-bt" }) }),
    ))
  })

  it("restores the confirmed output volume when applying a volume change fails", async () => {
    const user = userEvent.setup()
    currentBluetoothStatus = bluetoothStatusWithOutputs
    failOutputVolume = true
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))
    await screen.findByText("Output volume")
    const outputVolume = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="range"]'))[0]
    expect(outputVolume).toBeDefined()
    fireEvent.change(outputVolume!, { target: { value: "0.77" } })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output/volume",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-main", volume: 0.77 }) }),
    ))
    await waitFor(() => expect(screen.getByText("50%")).toBeInTheDocument())
  })

  it("updates active source from live dashboard state without settings polling", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)
    await user.click(screen.getByRole("link", { name: "Audio" }))
    await screen.findByText("Configured source")

    emitAudioSourceState(2, "auto", "bluetooth", true)
    await screen.findByText("Configured source")
    expect(screen.getByText("Configured source").parentElement).toHaveTextContent("Automatic")
    expect(screen.getByText("Active source").parentElement).toHaveTextContent("Bluetooth input")

    emitAudioSourceState(3, "auto", "mic", true)
    await screen.findByText("Active source")
    expect(screen.getByText("Active source").parentElement).toHaveTextContent("Microphone")
    expect(fetchMock.mock.calls.filter(([path]) => String(path) === "/api/audio/settings")).toHaveLength(1)
  })

  it("shows source-appropriate status as live dashboard source changes", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)
    await user.click(screen.getByRole("link", { name: "Audio" }))
    await screen.findByText("Configured source")

    emitAudioSourceState(2, "spotify", "off", false)
    expect(await screen.findByText("No audio source is active.")).toBeInTheDocument()
    expect(screen.queryByText("Now Playing")).not.toBeInTheDocument()

    emitAudioSourceState(3, "mic", "mic", true)
    expect(await screen.findByText("Listening to USB Mic.")).toBeInTheDocument()
    expect(screen.queryByText("Now Playing")).not.toBeInTheDocument()

    emitAudioSourceState(4, "bluetooth", "bluetooth", false)
    expect(await screen.findByText("Bluetooth input is disconnected or its audio stream is unavailable.")).toBeInTheDocument()

    emitAudioSourceState(5, "auto", "bluetooth", false)
    expect(await screen.findByText("Automatic audio source")).toBeInTheDocument()
    expect(screen.getByText("Configured source").parentElement).toHaveTextContent("Automatic")

    emitAudioSourceState(6, "demo", "demo", false)
    expect(await screen.findByText("Using the internal demo beat; no microphone capture is required.")).toBeInTheDocument()

    emitAudioSourceState(7, "off", "off", false)
    expect(await screen.findByText("No active audio source")).toBeInTheDocument()
    expect(screen.getByText("No audio source is active.")).toBeInTheDocument()
  })

  it("shows Spotify artwork and controls only when playback is ready", async () => {
    const user = userEvent.setup()
    spotifySettingsStatus = {
      ...spotifyStatus,
      track: { ...spotifyStatus.track, cover_url: "https://example.test/cover.jpg" },
    }
    spotifyStatusResponse = spotifySettingsStatus
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)
    await user.click(screen.getByRole("link", { name: "Audio" }))
    await screen.findByText("Configured source")
    emitAudioSourceState(2, "spotify", "spotify", true)

    expect(await screen.findByText("Now Playing")).toBeInTheDocument()
    expect(await screen.findByRole("img", { name: "Test album cover art" })).toHaveAttribute("src", "https://example.test/cover.jpg")
    expect(screen.getByRole("button", { name: "Pause Spotify" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Previous Spotify track" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Next Spotify track" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Shuffle" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Repeat" })).toBeInTheDocument()
  })

  it("shows play for paused Spotify and hides playback controls when Spotify is unavailable", async () => {
    const user = userEvent.setup()
    spotifySettingsStatus = { ...spotifyStatus, status: "paused" }
    spotifyStatusResponse = spotifySettingsStatus
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)
    await user.click(screen.getByRole("link", { name: "Audio" }))
    await screen.findByText("Configured source")
    emitAudioSourceState(2, "spotify", "spotify", true)

    expect(await screen.findByRole("button", { name: "Play Spotify" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Pause Spotify" })).not.toBeInTheDocument()

    spotifySettingsStatus = { ...spotifyStatus, connected: false, logged_in: false, status: "idle" }
    spotifyStatusResponse = spotifySettingsStatus
    emitAudioSourceState(3, "spotify", "spotify", true)
    expect(await screen.findByText("Unavailable")).toBeInTheDocument()
    expect(await screen.findByText("Playback controls will be available when Spotify is connected and signed in.")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Play Spotify" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Previous Spotify track" })).not.toBeInTheDocument()
  })

  it("keeps playback commands and Spotify sliders available on the audio dashboard", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)
    await user.click(screen.getByRole("link", { name: "Audio" }))
    await screen.findByText("Configured source")
    emitAudioSourceState(2, "spotify", "spotify", true)

    await user.click(await screen.findByRole("button", { name: "Previous Spotify track" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/audio/spotify/control", expect.objectContaining({ method: "POST", body: JSON.stringify({ action: "skip_prev" }) })))
    await user.click(screen.getByRole("button", { name: "Pause Spotify" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/audio/spotify/control", expect.objectContaining({ method: "POST", body: JSON.stringify({ action: "pause" }) })))
    await user.click(screen.getByRole("button", { name: "Next Spotify track" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/audio/spotify/control", expect.objectContaining({ method: "POST", body: JSON.stringify({ action: "skip_next" }) })))
    await user.click(screen.getByRole("button", { name: "Shuffle" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/audio/spotify/control", expect.objectContaining({ method: "POST", body: JSON.stringify({ action: "set_shuffle", value: true }) })))
    await user.click(screen.getByRole("button", { name: "Repeat" }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/audio/spotify/control", expect.objectContaining({ method: "POST", body: JSON.stringify({ action: "set_repeat", value: "context" }) })))
  })

  it("keeps Spotify setup concise and leaves playback controls on Audio", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)
    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))

    expect(await screen.findByRole("heading", { name: "Spotify Connect" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /open controls/i })).toHaveAttribute("href", "/audio")
    expect(screen.queryByRole("button", { name: "Previous Spotify track" })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([path]) => String(path) === "/api/audio/spotify")).toHaveLength(0)
    expect(screen.getByRole("combobox", { name: "Audio input source" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Audio output device" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Refresh microphone devices" })).toBeInTheDocument()
  })

  it("applies and rolls back quick output controls on Audio", async () => {
    const user = userEvent.setup()
    currentBluetoothStatus = bluetoothStatusWithOutputs
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)
    await user.click(screen.getByRole("link", { name: "Audio" }))

    const outputVolume = await waitFor(() => {
      const slider = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="range"]'))[0]
      expect(slider).toBeDefined()
      return slider!
    })
    fireEvent.change(outputVolume, { target: { value: "0.74" } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output/volume",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-main", volume: 0.74 }) }),
    ))
    const outputSwitch = screen.getByRole("switch", { name: "Enable audio output" })
    await user.click(outputSwitch)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output/mute",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-main", muted: true }) }),
    ))
    expect(outputSwitch).not.toBeChecked()
  })

  it("restores quick output volume and mute state when requests fail", async () => {
    const user = userEvent.setup()
    currentBluetoothStatus = bluetoothStatusWithOutputs
    failOutputVolume = true
    failOutputMute = true
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)
    await user.click(screen.getByRole("link", { name: "Audio" }))

    const outputVolume = await waitFor(() => {
      const slider = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="range"]'))[0]
      expect(slider).toBeDefined()
      return slider!
    })
    fireEvent.change(outputVolume, { target: { value: "0.77" } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output/volume",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-main", volume: 0.77 }) }),
    ))
    await waitFor(() => expect(screen.getByText("50%")).toBeInTheDocument())

    const outputSwitch = screen.getByRole("switch", { name: "Enable audio output" })
    expect(outputSwitch).toBeChecked()
    await user.click(outputSwitch)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/output/mute",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ selector: "sink-main", muted: true }) }),
    ))
    expect(outputSwitch).toBeChecked()
  })

  it("applies Spotify polling updates without changing the audio source draft", async () => {
    const user = userEvent.setup()
    spotifyStatusResponse = {
      ...spotifyStatus,
      track: { ...spotifyStatus.track, name: "Polling update track" },
      position_ms: 18_000,
      volume: 66,
    }
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Audio" }))
    emitAudioSourceState(2, "spotify", "spotify", true)

    expect(await screen.findByText("Polling update track", {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByText("66%")).toBeInTheDocument()
    expect(screen.getByText("Configured source").parentElement).toHaveTextContent("Spotify Connect")
    expect(screen.getByText("Active source").parentElement).toHaveTextContent("Spotify Connect")
  })

  it("shows stream freshness updates in the shared audio source status", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Audio" }))
    const audioSocket = await waitFor(() => {
      const socket = MockWebSocket.instances.find((instance) => instance.url.includes("/ws/audio"))
      expect(socket).toBeDefined()
      return socket!
    })
    expect(await screen.findByText("Waiting for audio samples")).toBeInTheDocument()
    expect(MockWebSocket.instances.filter((instance) => instance.url.endsWith("/ws/audio"))).toHaveLength(1)

    act(() => audioSocket.emit({ ...emptyTelemetry, fresh: true }))
    expect(await screen.findByText("Audio stream live")).toBeInTheDocument()
    expect(screen.getByText("Configured source").parentElement).toHaveTextContent("Microphone")
    expect(screen.getByText("Active source").parentElement).toHaveTextContent("Microphone")
  })

  it("keeps Spotify sliders interactive while a control request is pending", async () => {
    const user = userEvent.setup()
    pendingSpotifyControlResponse = new Promise(() => undefined)
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Audio" }))
    await screen.findByText("Configured source")
    emitAudioSourceState(2, "spotify", "spotify", true)
    await screen.findByText("Now Playing")
    const [position, volume] = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="range"]'))
    expect(position).toBeDefined()
    expect(volume).toBeDefined()
    expect(volume).not.toBeDisabled()
    fireEvent.change(position, { target: { value: "20000" } })
    fireEvent.change(volume, { target: { value: "41" } })
    expect(screen.getByText("0:20")).toBeInTheDocument()
    expect(screen.getByText("41%")).toBeInTheDocument()

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/spotify/control",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "seek", value: 20_000 }),
      }),
    ))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/spotify/control",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "set_volume", value: 41 }),
      }),
    ))
    expect(volume).not.toBeDisabled()
    expect(screen.getByText("41%")).toBeInTheDocument()
  })

  it("offers separate connection actions for dual-role Bluetooth devices", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Devices" }))

    expect(await screen.findByRole("button", { name: "Connect as input" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Connect as output" })).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Connect as input" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/bluetooth/connect",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ address: "AA:BB:CC:DD:EE:FF", role: "input" }),
      }),
    ))
  })

  it("enables restoration of the last shared state from Setup", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Startup" }))
    await user.click(await screen.findByRole("button", { name: "Restore last state off" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/startup",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ restore_last_state: true }) }),
    ))
    expect(await screen.findByRole("button", { name: "Restore last state on" })).toHaveAttribute("data-pressed")
  })

  it("shows and applies the stripe direction while the layout fills its card", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    expect(await screen.findByRole("group", { name: "Stripe layout" })).toHaveClass("w-full")

    await user.click(screen.getByRole("button", { name: "Reverse direction off" }))
    expect(screen.getByRole("button", { name: "Reverse direction on" })).toHaveAttribute("data-pressed")
    expect(screen.getByText("On")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Save & apply" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/stripes",
      expect.objectContaining({ method: "PUT" }),
    ))
    const request = fetchMock.mock.calls.find(([path]) => String(path) === "/api/stripes")
    const body = JSON.parse(String(request?.[1]?.body)) as { outputs: Array<{ reversed: boolean }> }
    expect(body.outputs[0].reversed).toBe(true)
  })

  it("adds the first stripe when randomUUID is unavailable over plain HTTP", async () => {
    const originalCrypto = globalThis.crypto
    const originalImplementation = fetchMock.getMockImplementation()
    vi.stubGlobal("crypto", {})
    fetchMock.mockImplementation((input: string | URL | Request) => {
      const path = String(input)
      if (path === "/api/auth/status") return jsonResponse({ required: false, authenticated: true })
      if (path === "/api/animations") return jsonResponse({ items: animations })
      return jsonResponse({
        ...initialState,
        stripe_topology: { layout: "mirrored", outputs: [] },
        stripe_playback: [],
        color_corrections: [],
      })
    })
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    await user.click(await screen.findByRole("button", { name: "Add stripe" }))

    expect(screen.getByDisplayValue("Stripe 1")).toBeInTheDocument()
    vi.stubGlobal("crypto", originalCrypto)
    fetchMock.mockImplementation(originalImplementation!)
  })

  it("searches animations by mood and selects a result", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("button", { name: /Animation/i }))
    const search = await screen.findByRole("searchbox", { name: "Search animations" })
    await user.type(search, "bass heavy")

    expect(screen.getByText(/bass pulse/i)).toBeInTheDocument()
    expect(screen.queryByText(/quiet stars/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /Bass pulse/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/animation",
      expect.objectContaining({ body: JSON.stringify({ name: "bass_pulse" }) })
    ))
  })

  it("selects solid mode and applies a preset color", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("button", { name: "Solid" }))
    const preset = await screen.findByRole("button", { name: "Set solid color #EF4444" })
    await user.click(preset)

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/mode",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ mode: "solid", color: "#EF4444" }),
      })
    )
  })

  it("navigates to the diagnostics page and shows runtime health", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Status" }))

    expect(await screen.findByRole("heading", { name: "Diagnostics" })).toBeInTheDocument()
    expect(screen.getByText("1m 5s")).toBeInTheDocument()
    expect(screen.getByText("20.0 FPS")).toBeInTheDocument()
    expect(screen.getByText("0.1.0")).toBeInTheDocument()
    expect(screen.getByText("No problems detected.")).toBeInTheDocument()
  })

  it("keeps diagnostics available when live state omits optional power telemetry", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    const { power_budget: _powerBudget, ...legacyState } = initialState
    act(() => MockWebSocket.instances[0].emit({ ...legacyState, revision: 2 }))
    await user.click(screen.getByRole("link", { name: "Status" }))

    expect(await screen.findByRole("heading", { name: "Diagnostics" })).toBeInTheDocument()
    expect(screen.getByText("Power limiting is disabled.")).toBeInTheDocument()
  })

  it("shows live audio telemetry without unlocking Setup", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Audio" }))
    expect(await screen.findByRole("heading", { name: "Audio" })).toBeInTheDocument()
    expect(screen.getByText("Live audio analysis")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText("Configured source").parentElement).toHaveTextContent("Microphone"))
    expect(screen.getByText("Listening to USB Mic.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Open audio setup" })).toHaveAttribute("href", "/setup/audio")

    const audioSocket = await waitFor(() => {
      const socket = [...MockWebSocket.instances].reverse().find((instance) => instance.url.endsWith("/ws/audio"))
      if (!socket) throw new Error("Audio WebSocket was not created")
      return socket
    })
    expect(await screen.findByText("Waiting for audio samples")).toBeInTheDocument()
    act(() => audioSocket.emit({
      sequence: 4,
      fresh: true,
      input_level: 0.42,
      processed_level: 0.35,
      bands: [0.1, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2],
      beat: true,
      beat_strength: 0.8,
      bpm: 128,
      bpm_confidence: 0.8,
      estimated_noise_floor: 0.012,
      configured_noise_floor: 0.015,
      normalization_gain: 1.4,
      program_loudness: 0.38,
      musical_impact: 0.92,
      gate: "music",
      gate_preview: false,
      gate_energy: 0.4,
      gate_onset: 0.2,
      gate_beat_density: 0.3,
      gate_brightness: 0.25,
      music_structure_phase: "buildup",
      music_structure_confidence: 0.87,
      music_structure_buildup_progress: 0.72,
      music_structure_drop_count: 3,
      music_structure_active: true,
      music_structure_fresh: true,
      drop_accent_status: "skipped",
      drop_accent_reason: "beat-clock-uncertain",
      health: "healthy",
    }))
    expect(screen.getByText("42%")).toBeInTheDocument()
    expect(screen.getByText("128 BPM")).toBeInTheDocument()
    expect(screen.getByText("80%")).toBeInTheDocument()
    expect(screen.getByText("Beat confidence")).toBeInTheDocument()
    expect(screen.getByLabelText("Audio frequency bands")).toBeInTheDocument()
    await user.click(screen.getByText("Advanced audio diagnostics"))
    expect(screen.getByText("music")).toBeInTheDocument()
    expect(screen.getAllByText("Music structure")).toHaveLength(2)
    expect(screen.getByText("Build-up")).toBeInTheDocument()
    expect(screen.getAllByText("87% confidence")).toHaveLength(2)
    expect(screen.getAllByText("72%")).toHaveLength(2)
    expect(screen.getByText("3")).toBeInTheDocument()
    expect(screen.getByText("Skipped · Beat clock confidence was too low")).toBeInTheDocument()
    await user.click(screen.getByText("Advanced audio diagnostics"))

    act(() => audioSocket.emit({
      sequence: 5,
      fresh: true,
      input_level: 0.18,
      processed_level: 0.12,
      bands: [0.05, 0.1, 0.15, 0.2, 0.25, 0.2, 0.15, 0.1],
      beat: false,
      beat_strength: 0.1,
      bpm: 96,
      estimated_noise_floor: 0.01,
      configured_noise_floor: 0.015,
      normalization_gain: 1.1,
      program_loudness: 0.2,
      musical_impact: 0.3,
      gate: "calm",
      gate_preview: true,
      gate_energy: 0.1,
      gate_onset: 0.08,
      gate_beat_density: 0.02,
      gate_brightness: 0.12,
      music_structure_phase: "groove",
      music_structure_confidence: 0.5,
      music_structure_buildup_progress: 0.2,
      music_structure_drop_count: 3,
      music_structure_active: false,
      music_structure_fresh: false,
      drop_accent_status: "idle",
      drop_accent_reason: null,
      health: "healthy",
    }))
    expect(screen.getByText("18%")).toBeInTheDocument()
    expect(screen.getByText("96 BPM")).toBeInTheDocument()

    act(() => audioSocket.emit({
      ...emptyTelemetry,
      sequence: 6,
      fresh: false,
      music_structure_active: true,
      music_structure_fresh: true,
      music_structure_phase: "buildup",
    }))
    expect(await screen.findByText("Audio samples stale")).toBeInTheDocument()
    expect(screen.getByText("Estimate held")).toBeInTheDocument()

    act(() => audioSocket.disconnect())
    expect(await screen.findByText("Stream reconnecting · last sample stale")).toBeInTheDocument()
  })

  it("keeps audio tuning behind Setup and resets the active microphone profile", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Setup" }))
    const setupNav = await screen.findByRole("navigation", { name: "Setup sections" })
    await user.click(within(setupNav).getByRole("link", { name: "Audio" }))
    const audioNav = await screen.findByRole("navigation", { name: "Audio setup sections" })
    await user.click(within(audioNav).getByRole("link", { name: "Tuning" }))
    expect(await screen.findByRole("heading", { name: "Audio tuning" })).toBeInTheDocument()
    expect(screen.getByText("USB Mic")).toBeInTheDocument()
    expect(screen.getByRole("group", { name: "Musical response" })).toBeInTheDocument()
    expect(screen.getByText("0.650")).toBeInTheDocument()

    const audioSocket = await waitFor(() => {
      const socket = [...MockWebSocket.instances].reverse().find((instance) => instance.url.endsWith("/ws/audio"))
      if (!socket) throw new Error("Audio WebSocket was not created")
      return socket
    })
    act(() => audioSocket.emit({
      sequence: 4,
      fresh: true,
      input_level: 0.42,
      processed_level: 0.35,
      bands: [0.1, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2],
      beat: true,
      beat_strength: 0.8,
      bpm: 128,
      estimated_noise_floor: 0.012,
      configured_noise_floor: 0.015,
      normalization_gain: 1.4,
      program_loudness: 0.38,
      musical_impact: 0.92,
      gate: "music",
      gate_preview: false,
      gate_energy: 0.4,
      gate_onset: 0.2,
      gate_beat_density: 0.3,
      gate_brightness: 0.25,
      health: "healthy",
    }))
    expect(screen.getByText("42%")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Advanced DSP settings and diagnostics" }))
    expect(screen.getByText("128 BPM")).toBeInTheDocument()
    expect(screen.getByText("92%")).toBeInTheDocument()
    expect(screen.getByText("music")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Reset/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/audio/settings/reset",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ device: "2" }) }),
    ))
  })

  it("shows retry UI when the initial API load fails", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      if (String(input) === "/api/auth/status") {
        return jsonResponse({ required: false, authenticated: true })
      }
      return Promise.reject(new Error("Network unreachable"))
    }))
    render(<App />, { wrapper: Router })

    expect(await screen.findByText("LumiStripe is unavailable")).toBeInTheDocument()
    expect(screen.getByText("Network unreachable")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument()
  })

  it("pairs before connecting to the protected dashboard", async () => {
    const protectedFetch = vi.fn((input: string | URL | Request) => {
      const path = String(input)
      if (path === "/api/auth/status") {
        return jsonResponse({ required: true, authenticated: false })
      }
      if (path === "/api/auth/pair") {
        return jsonResponse({ required: true, authenticated: true })
      }
      if (path === "/api/animations") return jsonResponse({ items: animations })
      return jsonResponse(initialState)
    })
    vi.stubGlobal("fetch", protectedFetch)
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })

    const code = await screen.findByLabelText("Pairing code")
    expect(MockWebSocket.instances).toHaveLength(0)
    await user.type(code, "1234")
    await user.click(screen.getByRole("button", { name: "Pair device" }))

    expect(await screen.findByText(/aurora wave/i)).toBeInTheDocument()
    expect(protectedFetch).toHaveBeenCalledWith(
      "/api/auth/pair",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ code: "1234" }),
      })
    )
    expect(MockWebSocket.instances.filter((socket) => socket.url.endsWith("/ws/state"))).toHaveLength(1)
  })

  it("guards a direct Setup URL before requesting protected settings", async () => {
    const protectedFetch = vi.fn((input: string | URL | Request) => {
      const path = String(input)
      if (path === "/api/auth/status") return jsonResponse({ required: true, authenticated: false })
      if (path === "/api/animations") return jsonResponse({ items: animations })
      return jsonResponse(initialState)
    })
    vi.stubGlobal("fetch", protectedFetch)

    render(
      <MemoryRouter initialEntries={["/setup/audio/tuning"]}>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByLabelText("Pairing code")).toBeInTheDocument()
    expect(protectedFetch.mock.calls.some(([path]) => String(path) === "/api/audio/settings")).toBe(false)
  })
})
