import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { MemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SpotifyControls } from "@/components/dashboard/spotify-controls"
import { dashboardApi, type SpotifyStatusResponse } from "@/lib/api"

vi.mock("@/components/ui/slider", () => ({
  Slider: ({
    value,
    onValueChange,
    onValueCommitted,
    ...props
  }: {
    value: number[]
    onValueChange?: (value: number[]) => void
    onValueCommitted?: (value: number[]) => void
  }) => (
    <input
      {...props}
      type="range"
      value={value[0] ?? 0}
      onChange={(event) => onValueChange?.([Number(event.currentTarget.value)])}
      onMouseUp={(event) => onValueCommitted?.([Number(event.currentTarget.value)])}
    />
  ),
}))

const playing: SpotifyStatusResponse = {
  configured: true,
  connected: true,
  logged_in: true,
  is_active: true,
  device_name: "LumiStripe",
  status: "playing",
  track: { uri: "spotify:track:sample", name: "Sample track", artists: ["Artist"], album: "Album", cover_url: null, duration_ms: 120_000 },
  position_ms: 10_000,
  duration_ms: 120_000,
  volume: 40,
  shuffle: false,
  repeat: "off",
  error: null,
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function renderControls() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Wrapper({ children }: { children: ReactNode }) {
    return <MemoryRouter><QueryClientProvider client={client}>{children}</QueryClientProvider></MemoryRouter>
  }
  const view = render(<SpotifyControls initialStatus={playing} />, { wrapper: Wrapper })
  return { ...view, client }
}

afterEach(() => vi.restoreAllMocks())

describe("SpotifyControls shared status", () => {
  it("keeps a confirmed playback command when an older poll resolves afterward", async () => {
    const stalePoll = deferred<SpotifyStatusResponse>()
    vi.spyOn(dashboardApi, "getSpotifyStatus").mockReturnValue(stalePoll.promise)
    const paused: SpotifyStatusResponse = { ...playing, status: "paused", position_ms: 18_000 }
    vi.spyOn(dashboardApi, "controlSpotify").mockResolvedValue(paused)
    const { client, unmount } = renderControls()
    await waitFor(() => expect(dashboardApi.getSpotifyStatus).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByRole("button", { name: "Pause Spotify" }))
    await screen.findByRole("button", { name: "Play Spotify" })
    await act(async () => { stalePoll.resolve(playing); await stalePoll.promise })

    expect(screen.getByRole("button", { name: "Play Spotify" })).toBeInTheDocument()
    unmount()
    client.clear()
  })

  it("preserves confirmed slider volume against a late status poll", async () => {
    const stalePoll = deferred<SpotifyStatusResponse>()
    vi.spyOn(dashboardApi, "getSpotifyStatus").mockReturnValue(stalePoll.promise)
    const changed: SpotifyStatusResponse = { ...playing, volume: 64 }
    const control = vi.spyOn(dashboardApi, "controlSpotify").mockResolvedValue(changed)
    const { client, unmount } = renderControls()
    await waitFor(() => expect(dashboardApi.getSpotifyStatus).toHaveBeenCalledTimes(1))

    const slider = screen.getByRole("slider", { name: "Spotify volume" })
    fireEvent.change(slider, { target: { value: "64" } })
    fireEvent.mouseUp(slider, { target: { value: "64" } })
    await waitFor(() => expect(control).toHaveBeenCalledWith("set_volume", 64))
    await act(async () => { stalePoll.resolve(playing); await stalePoll.promise })

    expect(screen.getByText("64%")).toBeInTheDocument()
    unmount()
    client.clear()
  })
})
