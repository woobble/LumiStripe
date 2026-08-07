import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
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
  devices: [{ selector: "2", name: "USB Mic", settings: audioValues }],
  settings: audioValues,
  configured_noise_floor: 0.015,
  error: null,
}

function Router({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>
}

class MockWebSocket {
  static instances: MockWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor() {
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

  it("keeps the routed bottom navigation fixed and evenly sized", async () => {
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    const navigation = screen.getByRole("navigation", { name: "Dashboard" })
    expect(navigation).toHaveClass("grid-cols-4", "items-stretch")
    expect(navigation.parentElement?.parentElement).toHaveClass("fixed", "inset-x-0", "bottom-0")
    expect(screen.getByRole("link", { name: "Control" })).toHaveClass("w-full")
    expect(screen.getByRole("link", { name: "Audio" })).toHaveClass("w-full")
    expect(screen.getByRole("link", { name: "Color" })).toHaveClass("w-full")
    expect(screen.getByRole("link", { name: "Status" })).toHaveClass("w-full")
  })

  it("runs a guided color calibration session", async () => {
    const user = userEvent.setup()
    const { container } = render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Color" }))
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

  it("shows live audio telemetry and resets the active microphone profile", async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: Router })
    await screen.findByText(/aurora wave/i)

    await user.click(screen.getByRole("link", { name: "Audio" }))
    expect(await screen.findByRole("heading", { name: "Audio tuning" })).toBeInTheDocument()
    expect(screen.getByText("USB Mic")).toBeInTheDocument()
    expect(screen.getByRole("group", { name: "Calm ↔ Dramatic" })).toBeInTheDocument()
    expect(screen.getByText("0.650")).toBeInTheDocument()

    act(() => MockWebSocket.instances[1].emit({
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
    expect(MockWebSocket.instances).toHaveLength(1)
  })
})
