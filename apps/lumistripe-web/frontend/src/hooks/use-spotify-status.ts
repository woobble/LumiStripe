import { useCallback, useSyncExternalStore } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { spotifyStatusQueryKey } from "@/hooks/audio-query-keys"
import { dashboardApi, type SpotifyStatusResponse } from "@/lib/api"

type SpotifyCoordinator = {
  activeCommands: number
  revision: number
  listeners: Set<() => void>
  subscribe: (listener: () => void) => () => void
  getActive: () => boolean
  notify: () => void
  startCommand: () => void
  finishCommand: () => void
  advanceRevision: () => void
}

const coordinators = new WeakMap<object, SpotifyCoordinator>()

function coordinatorFor(client: object): SpotifyCoordinator {
  let coordinator = coordinators.get(client)
  if (!coordinator) {
    const listeners = new Set<() => void>()
    coordinator = {
      activeCommands: 0,
      revision: 0,
      listeners,
      subscribe: (listener) => {
        listeners.add(listener)
        return () => listeners.delete(listener)
      },
      getActive: () => (coordinators.get(client)?.activeCommands ?? 0) > 0,
      notify: () => { for (const listener of listeners) listener() },
      startCommand: () => {
        const current = coordinators.get(client)
        if (!current) return
        current.revision += 1
        current.activeCommands += 1
        current.notify()
      },
      finishCommand: () => {
        const current = coordinators.get(client)
        if (!current) return
        current.activeCommands = Math.max(0, current.activeCommands - 1)
        current.notify()
      },
      advanceRevision: () => {
        const current = coordinators.get(client)
        if (current) current.revision += 1
      },
    }
    coordinators.set(client, coordinator)
  }
  return coordinator
}

export function useSpotifyStatus(initialStatus: SpotifyStatusResponse) {
  const queryClient = useQueryClient()
  const coordinator = coordinatorFor(queryClient)
  const commandActive = useSyncExternalStore(coordinator.subscribe, coordinator.getActive, coordinator.getActive)
  const query = useQuery({
    queryKey: spotifyStatusQueryKey,
    queryFn: async ({ signal }) => {
      const revision = coordinator.revision
      const current = queryClient.getQueryData<SpotifyStatusResponse>(spotifyStatusQueryKey)
      if (coordinator.activeCommands > 0 && current) return current
      const next = await dashboardApi.getSpotifyStatus(signal)
      if (coordinator.activeCommands > 0 || revision !== coordinator.revision) {
        return queryClient.getQueryData<SpotifyStatusResponse>(spotifyStatusQueryKey) ?? next
      }
      return next
    },
    initialData: () => queryClient.getQueryData<SpotifyStatusResponse>(spotifyStatusQueryKey) ?? initialStatus,
    staleTime: 0,
    gcTime: 5 * 60_000,
    refetchInterval: () => commandActive ? false : 1_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
  })

  const beginCommand = useCallback(() => {
    coordinator.startCommand()
    void queryClient.cancelQueries({ queryKey: spotifyStatusQueryKey, exact: true })
  }, [coordinator, queryClient])

  const endCommand = useCallback(() => {
    coordinator.finishCommand()
  }, [coordinator])

  const publishStatus = useCallback((next: SpotifyStatusResponse) => {
    coordinator.advanceRevision()
    void queryClient.cancelQueries({ queryKey: spotifyStatusQueryKey, exact: true })
    queryClient.setQueryData(spotifyStatusQueryKey, next)
  }, [coordinator, queryClient])

  return {
    status: query.data ?? initialStatus,
    error: query.error instanceof Error ? query.error.message : null,
    beginCommand,
    endCommand,
    publishStatus,
  }
}
