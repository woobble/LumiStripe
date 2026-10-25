import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { dashboardApi } from "@/lib/api"

export function useAudioSyncCalibration() {
  const mounted = useRef(false)
  const active = useRef(false)
  const cancelled = useRef(false)
  const startRequest = useRef<Promise<void> | null>(null)
  const stopRequest = useRef<Promise<void> | null>(null)
  const timer = useRef<number | null>(null)
  const [running, setRunning] = useState(false)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)

  const clearTimer = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }, [])

  const stop = useCallback(() => {
    if (stopRequest.current) return stopRequest.current
    cancelled.current = true
    clearTimer()
    if (mounted.current) setStopping(true)

    let request: Promise<void>
    request = (async () => {
      const pendingStart = startRequest.current
      if (pendingStart) await pendingStart

      if (!active.current) {
        if (mounted.current) {
          setRunning(false)
          setStarting(false)
          setStopping(false)
        }
        return
      }

      active.current = false
      if (mounted.current) setRunning(false)
      try {
        await dashboardApi.setAudioSyncCalibration(false)
      } catch (error) {
        if (mounted.current) toast.error(error instanceof Error ? error.message : "Could not stop sync calibration.")
      } finally {
        if (mounted.current) setStopping(false)
      }
    })().finally(() => {
      if (stopRequest.current === request) stopRequest.current = null
    })
    stopRequest.current = request
    return request
  }, [clearTimer])

  const start = useCallback(() => {
    if (active.current || startRequest.current || stopRequest.current) return
    cancelled.current = false
    if (mounted.current) setStarting(true)

    let request: Promise<void>
    request = dashboardApi.setAudioSyncCalibration(true)
      .then(async () => {
        if (!mounted.current || cancelled.current) {
          await dashboardApi.setAudioSyncCalibration(false).catch(() => undefined)
          return
        }
        active.current = true
        setRunning(true)
        timer.current = window.setTimeout(() => { void stop() }, 45_000)
      })
      .catch((error: unknown) => {
        if (mounted.current) toast.error(error instanceof Error ? error.message : "Could not start sync calibration.")
      })
      .finally(() => {
        if (startRequest.current === request) startRequest.current = null
        if (mounted.current) setStarting(false)
      })

    startRequest.current = request
    return request
  }, [stop])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      cancelled.current = true
      clearTimer()
      void stop()
    }
  }, [clearTimer, stop])

  return { running, starting, stopping, start, stop }
}
