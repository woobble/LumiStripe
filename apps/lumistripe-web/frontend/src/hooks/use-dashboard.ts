import { useCallback, useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import {
  dashboardApi,
  parseDashboardState,
  websocketUrl,
  type CalibrationPattern,
  type DashboardState,
  type SequenceCreateRequest,
  type SequenceDefinitionRequest,
  type PlaybackMode,
  type SwitchQuantization,
  type StripeTopology,
} from "@/lib/api"
import { useWebSocketStream } from "@/hooks/use-websocket-stream"

export type ConnectionStatus = "connecting" | "connected" | "reconnecting"
export type CommandName = "mode" | "brightness" | "solidColor" | "animation" | "blackout" | "calibration" | "stripes" | "stripeTest" | "sequencer" | "sequence"
export const dashboardStateQueryKey = ["dashboard-state"] as const
export const animationsQueryKey = ["animations"] as const
export const sequenceLibraryQueryKey = ["animation-sequences"] as const

type SequenceCommand =
  | { operation: "create"; sequence: SequenceCreateRequest }
  | { operation: "update"; sequenceId: string; sequence: SequenceDefinitionRequest }
  | { operation: "delete"; sequenceId: string }
  | { operation: "active"; sequenceId: string | null }

function newerState(current: DashboardState | null, incoming: DashboardState) {
  return current === null || incoming.revision >= current.revision ? incoming : current
}

function parseDashboardMessage(value: unknown): DashboardState {
  return parseDashboardState(JSON.parse(String(value)))
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
  const sequenceLibraryQuery = useQuery({
    queryKey: sequenceLibraryQueryKey,
    queryFn: dashboardApi.getAnimationSequences,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  })
  const sequenceMutation = useMutation<DashboardState, Error, SequenceCommand>({
    mutationFn: (command) => {
      switch (command.operation) {
        case "create": return dashboardApi.createAnimationSequence(command.sequence)
        case "update": return dashboardApi.updateAnimationSequence(command.sequenceId, command.sequence)
        case "delete": return dashboardApi.deleteAnimationSequence(command.sequenceId)
        case "active": return dashboardApi.setActiveAnimationSequence(command.sequenceId)
      }
    },
  })
  const [pendingCommand, setPendingCommand] = useState<CommandName | null>(null)
  const commandActive = useRef(false)

  const { data: incomingState, status: connection } = useWebSocketStream<DashboardState>({
    url: websocketUrl(),
    parse: parseDashboardMessage,
  })

  const state = stateQuery.data ?? null
  const animations = animationsQuery.data ?? []
  const loading = stateQuery.isPending
  const loadError = stateQuery.error instanceof Error
    ? stateQuery.error.message
    : animationsQuery.error instanceof Error ? animationsQuery.error.message : null

  const refresh = useCallback(async () => {
    await Promise.all([stateQuery.refetch(), animationsQuery.refetch(), sequenceLibraryQuery.refetch()])
  }, [animationsQuery, sequenceLibraryQuery, stateQuery])

  useEffect(() => {
    if (incomingState === null) return
    queryClient.setQueryData<DashboardState>(dashboardStateQueryKey, (current) => newerState(current ?? null, incomingState))
  }, [incomingState, queryClient])

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

  const runSequenceCommand = useCallback(async (command: SequenceCommand) => {
    if (commandActive.current) return false
    commandActive.current = true
    setPendingCommand("sequence")
    sequenceMutation.reset()
    try {
      const nextState = await sequenceMutation.mutateAsync(command)
      queryClient.setQueryData<DashboardState>(dashboardStateQueryKey, (current) => newerState(current ?? null, nextState))
      await queryClient.invalidateQueries({ queryKey: sequenceLibraryQueryKey })
      return true
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The sequence change could not be applied.")
      return false
    } finally {
      commandActive.current = false
      setPendingCommand(null)
    }
  }, [queryClient, sequenceMutation])

  const refreshSequences = useCallback(async () => {
    await sequenceLibraryQuery.refetch()
  }, [sequenceLibraryQuery])

  return {
    state,
    animations,
    sequences: sequenceLibraryQuery.data?.sequences ?? [],
    sequenceLibraryLoading: sequenceLibraryQuery.isPending,
    sequenceLibraryError: sequenceLibraryQuery.error instanceof Error ? sequenceLibraryQuery.error.message : null,
    animationsLoading: animationsQuery.isPending,
    animationsError: animationsQuery.error instanceof Error ? animationsQuery.error.message : null,
    sequenceActionError: sequenceMutation.error instanceof Error ? sequenceMutation.error.message : null,
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
    setSequencerSettings: (mode: SwitchQuantization, barsPerPhrase: 4 | 8 | 16) =>
      runCommand("sequencer", () => dashboardApi.setSequencerSettings(mode, barsPerPhrase)),
    createSequence: (sequence: SequenceCreateRequest) => runSequenceCommand({ operation: "create", sequence }),
    updateSequence: (sequenceId: string, sequence: SequenceDefinitionRequest) => runSequenceCommand({ operation: "update", sequenceId, sequence }),
    deleteSequence: (sequenceId: string) => runSequenceCommand({ operation: "delete", sequenceId }),
    setActiveSequence: (sequenceId: string | null) => runSequenceCommand({ operation: "active", sequenceId }),
    refreshSequences,
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
