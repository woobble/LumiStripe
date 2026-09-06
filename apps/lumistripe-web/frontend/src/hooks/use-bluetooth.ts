import { useCallback, useEffect, useRef, useState } from "react"

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

/**
 * Own the Bluetooth status lifecycle independently of the audio setup cards.
 * The backend reports the provider's capabilities and operation state; this
 * hook only coordinates polling and protects the UI from stale responses.
 */
export function useBluetooth(initialStatus?: BluetoothStatusResponse | null) {
  const [status, setStatus] = useState<BluetoothStatusResponse | null>(initialStatus ?? null)
  const [busyAction, setBusyAction] = useState<BluetoothAction | null>(null)
  const statusRef = useRef(status)
  const requestVersion = useRef(0)

  useEffect(() => {
    statusRef.current = status
  }, [status])

  const replaceStatus = useCallback((next: BluetoothStatusResponse) => {
    requestVersion.current += 1
    statusRef.current = next
    setStatus(next)
  }, [])

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current
    try {
      const next = await dashboardApi.getBluetoothStatus()
      if (version === requestVersion.current) {
        statusRef.current = next
        setStatus(next)
      }
      return next
    } catch {
      // Background refresh must not replace a useful status with a transient
      // network error. Explicit actions still surface their own errors.
      return null
    }
  }, [])

  useEffect(() => {
    let disposed = false
    let timer: number | undefined

    const schedule = () => {
      if (disposed) return
      const activeOperation = statusRef.current?.operation_state === "running"
        || Boolean(statusRef.current?.operation)
      const hidden = document.visibilityState === "hidden"
      const delay = activeOperation ? 500 : hidden ? 10_000 : 2_000
      timer = window.setTimeout(() => {
        void refresh().finally(schedule)
      }, delay)
    }

    const poll = () => {
      if (disposed) return
      if (document.visibilityState === "hidden") {
        schedule()
        return
      }
      void refresh().finally(schedule)
    }

    const handleVisibilityChange = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      poll()
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    poll()
    return () => {
      disposed = true
      if (timer !== undefined) window.clearTimeout(timer)
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [refresh])

  const run = useCallback(async (action: BluetoothAction, operation: BluetoothOperation) => {
    if (busyAction || statusRef.current?.operation_state === "running" || statusRef.current?.operation) {
      return null
    }
    setBusyAction(action)
    const version = ++requestVersion.current
    try {
      const next = await operation()
      if (version === requestVersion.current) {
        statusRef.current = next
        setStatus(next)
      }
      return next
    } finally {
      setBusyAction(null)
    }
  }, [busyAction])

  return {
    status,
    busyAction,
    busy: Boolean(busyAction || status?.operation_state === "running" || status?.operation),
    refresh,
    replaceStatus,
    run,
  }
}
