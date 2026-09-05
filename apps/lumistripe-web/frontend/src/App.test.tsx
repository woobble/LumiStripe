import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"

import App from "@/App"
import { animations, initialState, jsonResponse } from "@/test/fixtures"

const audioValues = {
  target_level: 0.36,
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
  monitoring: true,
  active_device: "2",
  active_device_name: "USB Mic",
  devices: [
    { selector: "2", name: "USB Mic", settings: audioValues },
    { selector: "3", name: "Built-in Mic", settings: audioValues },
  ],
  settings: audioValues,
  configured_noise_floor: 0.015,
  error: null,
}

function Router({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>
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

  emit(state: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(state) }))
  }
}

describe("App", () => {
  let pendingBrightnessResponse: Promise<Response> | null = null
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const path = String(input)
    if (path === "/api/auth/status") {
      return jsonResponse({ required: false, authenticated: true })
    }
    if (path === "/api/animations") return jsonResponse({ items: animations })
    if (path === "/api/audio/settings" || path === "/api/audio/settings/reset") {
      return jsonResponse(audioSettings)
    }
    if (path === "/api/audio/device") {
      const body = JSON.parse(String(init?.body)) as { device: string }
      const device = audioSettings.devices.find((item) => item.selector === body.device)
      return jsonResponse({ ...audioSettings, active_device: body.device, active_device_name: device?.name ?? null })
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

  it("shows live audio telemetry without unlocking Setup", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Audio" }))
    expect(await screen.findByRole("heading", { name: "Audio status" })).toBeInTheDocument()
    expect(screen.getByText("Live input")).toBeInTheDocument()
    expect(screen.queryByText("USB Mic")).not.toBeInTheDocument()

    const audioSocket = MockWebSocket.instances.find((socket) => socket.url.endsWith("/ws/audio"))
    expect(audioSocket).toBeDefined()
    act(() => audioSocket!.emit({
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
    expect(screen.getByText("128 BPM")).toBeInTheDocument()
    expect(screen.getByText("92%")).toBeInTheDocument()
    expect(screen.getByText("music")).toBeInTheDocument()
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
    expect(screen.getByRole("group", { name: "Calm ↔ Dramatic" })).toBeInTheDocument()
    expect(screen.getByText("0.650")).toBeInTheDocument()

    const audioSocket = MockWebSocket.instances.find((socket) => socket.url.endsWith("/ws/audio"))
    expect(audioSocket).toBeDefined()
    act(() => audioSocket!.emit({
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
