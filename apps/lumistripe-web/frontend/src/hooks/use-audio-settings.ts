import { useCallback } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { audioSettingsQueryKey } from "@/hooks/audio-query-keys"
import { dashboardApi, type AudioSettingsResponse } from "@/lib/api"

export { audioSettingsQueryKey }

export function useAudioSettings() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: audioSettingsQueryKey,
    queryFn: ({ signal }) => dashboardApi.getAudioSettings(signal),
    staleTime: 5_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  })
  const refetch = query.refetch

  const refresh = useCallback(async (_showLoading = true) => {
    const result = await refetch({ throwOnError: true, cancelRefetch: false })
    return result.data ?? null
  }, [refetch])

  const applySettings = useCallback((next: AudioSettingsResponse) => {
    // Canceling first makes TanStack Query discard any older in-flight read.
    // The API request also consumes the query signal so the network request
    // stops when the browser supports aborting it.
    void queryClient.cancelQueries({ queryKey: audioSettingsQueryKey, exact: true })
    queryClient.setQueryData(audioSettingsQueryKey, next)
  }, [queryClient])

  const loadError = query.error instanceof Error ? query.error.message : null
  return {
    settings: query.data ?? null,
    loading: query.isPending || query.isFetching,
    loadError,
    refresh,
    applySettings,
  }
}
