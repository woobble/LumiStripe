import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import {
  dashboardApi,
  ACCESS_REVOKED_EVENT,
  websocketUrl,
  type AnimationOption,
  type CalibrationPattern,
  type DashboardState,
  type PlaybackMode,
} from "@/lib/api"

export type ConnectionStatus = "connecting" | "connected" | "reconnecting"
export type CommandName = "mode" | "brightness" | "solidColor" | "animation" | "blackout" | "calibration"

function newerState(current: DashboardState | null, incoming: DashboardState) {
  return current === null || incoming.revision >= current.revision ? incoming : current
}

export function useDashboard() {
  const [state, setState] = useState<DashboardState | null>(null)
  const [animations, setAnimations] = useState<AnimationOption[]>([])
  const [connection, setConnection] = useState<ConnectionStatus>("connecting")
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [pendingCommand, setPendingCommand] = useState<CommandName | null>(null)
  const commandActive = useRef(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [nextState, nextAnimations] = await Promise.all([
        dashboardApi.getState(),
        dashboardApi.getAnimations(),
      ])
      setState((current) => newerState(current, nextState))
      setAnimations(nextAnimations)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Could not reach LumiStripe.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    let disposed = false
    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined
    let attempts = 0

    const connect = () => {
      if (disposed) return
      setConnection(attempts === 0 ? "connecting" : "reconnecting")
      socket = new WebSocket(websocketUrl())

      socket.onopen = () => {
        attempts = 0
        setConnection("connected")
      }
      socket.onmessage = (event) => {
        try {
          const incoming = JSON.parse(String(event.data)) as DashboardState
          setState((current) => newerState(current, incoming))
        } catch {
          socket?.close()
        }
      }
      socket.onerror = () => socket?.close()
      socket.onclose = (event) => {
        if (disposed) return
        if (event.code === 4401) {
          window.dispatchEvent(new Event(ACCESS_REVOKED_EVENT))
          return
        }
        attempts += 1
        setConnection("reconnecting")
        const delay = Math.min(1000 * 2 ** (attempts - 1), 10_000)
        reconnectTimer = window.setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      disposed = true
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

  const runCommand = useCallback(
    async (name: CommandName, command: () => Promise<DashboardState>) => {
      if (commandActive.current) return false
      commandActive.current = true
      setPendingCommand(name)
      try {
        const nextState = await command()
        setState((current) => newerState(current, nextState))
        return true
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "The command could not be applied.")
        return false
      } finally {
        commandActive.current = false
        setPendingCommand(null)
      }
    },
    []
  )

  const startCalibration = useCallback(async (outputIndex: number) => {
    if (commandActive.current) return null
    commandActive.current = true
    setPendingCommand("calibration")
    try {
      const response = await dashboardApi.startCalibration(outputIndex)
      setState((current) => newerState(current, response.state))
      return response.session_id
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Calibration could not be started.")
      return null
    } finally {
      commandActive.current = false
      setPendingCommand(null)
    }
  }, [])

  const updateCalibration = useCallback((
    sessionId: string,
    correction: { red: number; green: number; blue: number },
    pattern: CalibrationPattern,
  ) => runCommand(
    "calibration",
    () => dashboardApi.updateCalibration(sessionId, correction, pattern),
  ), [runCommand])

  const finishCalibration = useCallback((sessionId: string, save: boolean) =>
    runCommand(
      "calibration",
      () => dashboardApi.finishCalibration(sessionId, save),
    ), [runCommand])

  return {
    state,
    animations,
    connection,
    loading,
    loadError,
    pendingCommand,
    refresh,
    setMode: (mode: PlaybackMode) => runCommand("mode", () => dashboardApi.setMode(mode)),
    setBrightness: (brightness: number) =>
      runCommand("brightness", () => dashboardApi.setBrightness(brightness)),
    setSolidColor: (color: string) =>
      runCommand("solidColor", () => dashboardApi.setMode("solid", color)),
    selectAnimation: (name: string) =>
      runCommand("animation", () => dashboardApi.selectAnimation(name)),
    setBlackout: (enabled: boolean) =>
      runCommand("blackout", () => dashboardApi.setBlackout(enabled)),
    startCalibration,
    updateCalibration,
    finishCalibration,
  }
}

export type DashboardController = ReturnType<typeof useDashboard>
