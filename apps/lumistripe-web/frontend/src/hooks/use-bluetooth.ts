import { useCallback, useSyncExternalStore, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { audioSettingsQueryKey, bluetoothStatusQueryKey } from "@/hooks/audio-query-keys"
import { dashboardApi, type BluetoothStatusResponse } from "@/lib/api"

export type BluetoothAction =
  | "refresh"
  | "scan"
  | "power:on"
  | "power:off"
  | "alias"
  | `pair:${string}`
  | `connect:${string}:${string}`
  | `disconnect:${string}`
  | `forget:${string}`
  | "select-output"
  | "output-volume"
  | "output-mute"

type BluetoothOperation = () => Promise<BluetoothStatusResponse>

type OperationCoordinator = {
  active: boolean
  revision: number
  listeners: Set<() => void>
  subscribe: (listener: () => void) => () => void
  getActive: () => boolean
  setActive: (active: boolean) => void
  advanceRevision: () => void
}

const coordinators = new WeakMap<object, OperationCoordinator>()

function coordinatorFor(client: object): OperationCoordinator {
  let coordinator = coordinators.get(client)
  if (!coordinator) {
    const listeners = new Set<() => void>()
    coordinator = {
      active: false,
      revision: 0,
      listeners,
      subscribe: (listener) => {
        listeners.add(listener)
        return () => listeners.delete(listener)
      },
      getActive: () => coordinator?.active ?? false,
      setActive: (active) => {
        if (coordinator?.active === active) return
        if (coordinator) {
          coordinator.active = active
          for (const listener of listeners) listener()
        }
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

/** Share confirmed Bluetooth/output status and coordinate polling with commands. */
export function useBluetooth(initialStatus?: BluetoothStatusResponse | null) {
  const queryClient = useQueryClient()
  const coordinator = coordinatorFor(queryClient)
  const operationActive = useSyncExternalStore(coordinator.subscribe, coordinator.getActive, coordinator.getActive)
  const [busyAction, setBusyAction] = useState<BluetoothAction | null>(null)
  const [actionError, setActionError] = useState<{ message: string; confirmedAt: number } | null>(null)

  const query = useQuery({
    queryKey: bluetoothStatusQueryKey,
    queryFn: async ({ signal }) => {
      const revision = coordinator.revision
      const current = queryClient.getQueryData<BluetoothStatusResponse>(bluetoothStatusQueryKey)
      if (coordinator.active && current) return current
      const next = await dashboardApi.getBluetoothStatus(signal)
      if (coordinator.active || revision !== coordinator.revision) {
        return queryClient.getQueryData<BluetoothStatusResponse>(bluetoothStatusQueryKey) ?? next
      }
      return next
    },
    initialData: initialStatus ?? undefined,
    staleTime: 0,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    refetchInterval: (current) => {
      if (coordinator.active) return false
      return current.state.data?.operation_state === "running" || current.state.data?.operation ? 500 : 2_000
    },
    refetchIntervalInBackground: false,
    retry: false,
  })
  const refetch = query.refetch

  const refresh = useCallback(async () => {
    if (coordinator.active) return queryClient.getQueryData<BluetoothStatusResponse>(bluetoothStatusQueryKey) ?? null
    const result = await refetch({ throwOnError: true, cancelRefetch: false })
    setActionError(null)
    return result.data ?? null
  }, [coordinator, queryClient, refetch])

  const replaceStatus = useCallback((next: BluetoothStatusResponse) => {
    coordinator.advanceRevision()
    void queryClient.cancelQueries({ queryKey: bluetoothStatusQueryKey, exact: true })
    queryClient.setQueryData(bluetoothStatusQueryKey, next)
    setActionError(null)
  }, [coordinator, queryClient])

  const run = useCallback(async (action: BluetoothAction, operation: BluetoothOperation) => {
    const current = queryClient.getQueryData<BluetoothStatusResponse>(bluetoothStatusQueryKey)
    if (coordinator.active || current?.operation_state === "running" || current?.operation) return null

    coordinator.advanceRevision()
    coordinator.setActive(true)
    setBusyAction(action)
    setActionError(null)
    void queryClient.cancelQueries({ queryKey: bluetoothStatusQueryKey, exact: true })
    try {
      const next = await operation()
      queryClient.setQueryData(bluetoothStatusQueryKey, next)
      if (!["scan", "alias", "output-volume", "output-mute"].includes(action)) {
        void queryClient.invalidateQueries({ queryKey: audioSettingsQueryKey, exact: true })
      }
      return next
    } catch (cause) {
      setActionError({
        message: cause instanceof Error ? cause.message : "Bluetooth operation failed.",
        confirmedAt: queryClient.getQueryState(bluetoothStatusQueryKey)?.dataUpdatedAt ?? 0,
      })
      throw cause
    } finally {
      setBusyAction(null)
      coordinator.setActive(false)
    }
  }, [coordinator, queryClient])

  const status = query.data ?? null
  const currentActionError = actionError?.confirmedAt === query.dataUpdatedAt ? actionError.message : null
  const error = currentActionError ?? (query.error instanceof Error ? query.error.message : null)
  return {
    status,
    busyAction,
    busy: Boolean(operationActive || status?.operation_state === "running" || status?.operation),
    loading: query.isPending || query.isFetching,
    error,
    refresh,
    replaceStatus,
    run,
  }
}
