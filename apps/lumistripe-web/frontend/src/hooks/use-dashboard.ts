import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import {
  dashboardApi,
  ACCESS_REVOKED_EVENT,
  websocketUrl,
  type CalibrationPattern,
  type DashboardState,
  type PlaybackMode,
  type StripeTopology,
} from "@/lib/api"

export type ConnectionStatus = "connecting" | "connected" | "reconnecting"
export type CommandName = "mode" | "brightness" | "solidColor" | "animation" | "blackout" | "calibration" | "stripes" | "stripeTest"
export const dashboardStateQueryKey = ["dashboard-state"] as const
export const animationsQueryKey = ["animations"] as const

function newerState(current: DashboardState | null, incoming: DashboardState) {
  return current === null || incoming.revision >= current.revision ? incoming : current
}

export function useDashboard() {
  const queryClient = useQueryClient()
  const stateQuery = useQuery({
    queryKey: dashboardStateQueryKey,
    queryFn: async () => newerState(queryClient.getQueryData<DashboardState>(dashboardStateQueryKey) ?? null, await dashboardApi.getState()),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  })
  const animationsQuery = useQuery({ queryKey: animationsQueryKey, queryFn: dashboardApi.getAnimations, staleTime: 5 * 60_000, refetchOnWindowFocus: false, retry: false })
  const [connection, setConnection] = useState<ConnectionStatus>("connecting")
  const [pendingCommand, setPendingCommand] = useState<CommandName | null>(null)
  const commandActive = useRef(false)

  const state = stateQuery.data ?? null
  const animations = animationsQuery.data ?? []
  const loading = stateQuery.isPending
  const loadError = stateQuery.error instanceof Error
    ? stateQuery.error.message
    : animationsQuery.error instanceof Error ? animationsQuery.error.message : null

  const refresh = useCallback(async () => {
    await Promise.all([stateQuery.refetch(), animationsQuery.refetch()])
  }, [animationsQuery, stateQuery])

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
          queryClient.setQueryData<DashboardState>(dashboardStateQueryKey, (current) => newerState(current ?? null, incoming))
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
  }, [queryClient])

  const runCommand = useCallback(
    async (name: CommandName, command: () => Promise<DashboardState>) => {
      if (commandActive.current) return false
      commandActive.current = true
      setPendingCommand(name)
      try {
        const nextState = await command()
        queryClient.setQueryData<DashboardState>(dashboardStateQueryKey, (current) => newerState(current ?? null, nextState))
        return true
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "The command could not be applied.")
        return false
      } finally {
        commandActive.current = false
        setPendingCommand(null)
      }
    },
    [queryClient]
  )

  const startCalibration = useCallback(async (outputIndex: number) => {
    if (commandActive.current) return null
    commandActive.current = true
    setPendingCommand("calibration")
    try {
      const response = await dashboardApi.startCalibration(outputIndex)
      queryClient.setQueryData<DashboardState>(dashboardStateQueryKey, (current) => newerState(current ?? null, response.state))
      return response.session_id
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Calibration could not be started.")
      return null
    } finally {
      commandActive.current = false
      setPendingCommand(null)
    }
  }, [queryClient])

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
    setMode: (mode: PlaybackMode, stripeId?: string, musicRecognitionEnabled?: boolean) =>
      runCommand("mode", () => dashboardApi.setMode(mode, undefined, stripeId, musicRecognitionEnabled)),
    setBrightness: (brightness: number, stripeId?: string) =>
      runCommand("brightness", () => dashboardApi.setBrightness(brightness, stripeId)),
    setSolidColor: (color: string, stripeId?: string) =>
      runCommand("solidColor", () => dashboardApi.setMode("solid", color, stripeId)),
    selectAnimation: (name: string, stripeId?: string) =>
      runCommand("animation", () => dashboardApi.selectAnimation(name, stripeId)),
    setBlackout: (enabled: boolean, stripeId?: string) =>
      runCommand("blackout", () => dashboardApi.setBlackout(enabled, stripeId)),
    updateStripes: (topology: StripeTopology) =>
      runCommand("stripes", () => dashboardApi.updateStripes(topology)),
    testStripe: (stripeId: string, pattern: "identify" | "red" | "green" | "blue" | "white", topology?: StripeTopology) =>
      runCommand("stripeTest", () => dashboardApi.testStripe(stripeId, pattern, topology)),
    startCalibration,
    updateCalibration,
    finishCalibration,
  }
}

export type DashboardController = ReturnType<typeof useDashboard>
