import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useAudioSettings } from "@/hooks/use-audio-settings"
import { dashboardApi } from "@/lib/api"

type SettingsResponse = Awaited<ReturnType<typeof dashboardApi.getAudioSettings>>

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function settings(source: string) {
  return { source } as unknown as SettingsResponse
}

function queryWrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

afterEach(() => vi.restoreAllMocks())

describe("useAudioSettings shared query", () => {
  it("deduplicates concurrent consumers and shares the confirmed result", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const getSettings = vi.spyOn(dashboardApi, "getAudioSettings").mockResolvedValue(settings("mic"))
    const { result } = renderHook(() => [useAudioSettings(), useAudioSettings()] as const, { wrapper: queryWrapper(client) })

    await waitFor(() => expect(result.current[0].settings).toEqual(settings("mic")))
    expect(getSettings).toHaveBeenCalledTimes(1)
    expect(result.current[1].settings).toEqual(result.current[0].settings)
    client.clear()
  })

  it("keeps a successful mutation when an older settings fetch resolves later", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const pending = deferred<SettingsResponse>()
    const getSettings = vi.spyOn(dashboardApi, "getAudioSettings").mockReturnValue(pending.promise)
    const { result } = renderHook(() => useAudioSettings(), { wrapper: queryWrapper(client) })
    await waitFor(() => expect(getSettings).toHaveBeenCalledTimes(1))

    const confirmed = settings("spotify")
    act(() => result.current.applySettings(confirmed))
    await act(async () => { pending.resolve(settings("mic")); await pending.promise })

    await waitFor(() => expect(result.current.settings).toEqual(confirmed))
    expect(client.getQueryData(["audio-settings"])).toEqual(confirmed)
    client.clear()
  })
})
