import { useCallback, useEffect, useRef, useState } from "react"
import { CheckCircle2Icon, CircleAlertIcon, Mic2Icon, RotateCcwIcon, SlidersHorizontalIcon } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { dashboardApi, type AudioCalibrationSessionResponse } from "@/lib/api"

type Props = { device: string | null; onApplied: () => void; onBusyChange?: (busy: boolean) => void; microphone?: boolean }

export function AudioCalibrationWizard({ device, onApplied, onBusyChange, microphone = true }: Props) {
  const [session, setSession] = useState<AudioCalibrationSessionResponse | null>(null)
  const [sessionDevice, setSessionDevice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [duration, setDuration] = useState(8)
  const [actionError, setActionError] = useState<string | null>(null)
  const mounted = useRef(false)
  const deviceProp = useRef(device)
  const sessionRef = useRef<AudioCalibrationSessionResponse | null>(null)
  const sessionDeviceRef = useRef<string | null>(null)
  const deviceCancellationTarget = useRef<string | null | undefined>(undefined)
  const finalizing = useRef(new Set<string>())
  const pendingSessionCommands = useRef(new Map<string, Promise<unknown>>())
  const cancelRequests = useRef(new Map<string, Promise<void>>())

  useEffect(() => { deviceProp.current = device }, [device])

  const updateSession = useCallback((next: AudioCalibrationSessionResponse | null) => {
    sessionRef.current = next
    if (mounted.current) setSession(next)
  }, [])

  const cancelRemote = useCallback((sessionId: string) => {
    const existing = cancelRequests.current.get(sessionId)
    if (existing) return existing
    let request: Promise<void>
    request = (async () => {
      const pending = pendingSessionCommands.current.get(sessionId)
      if (pending) await pending.catch(() => undefined)
      await dashboardApi.finishAudioCalibration(sessionId, false)
    })().finally(() => {
      if (cancelRequests.current.get(sessionId) === request) cancelRequests.current.delete(sessionId)
    })
    cancelRequests.current.set(sessionId, request)
    return request
  }, [])

  const cancelActiveSession = useCallback(() => {
    const current = sessionRef.current
    if (current && !finalizing.current.has(current.session_id)) {
      void cancelRemote(current.session_id).catch(() => undefined)
    }
  }, [cancelRemote])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      cancelActiveSession()
    }
  }, [cancelActiveSession])

  useEffect(() => {
    const current = sessionRef.current
    if (!current || sessionDeviceRef.current === device || deviceCancellationTarget.current === device) return
    deviceCancellationTarget.current = device
    setActionError(null)
    setBusy(true)
    void cancelRemote(current.session_id)
      .then(() => {
        if (sessionRef.current?.session_id !== current.session_id) return
        updateSession(null)
        sessionDeviceRef.current = null
        setSessionDevice(null)
        deviceCancellationTarget.current = undefined
      })
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : "Could not cancel microphone calibration."
        if (sessionRef.current?.session_id === current.session_id) updateSession({ ...current, status: "failed", error: message })
        toast.error(message)
      })
      .finally(() => { if (mounted.current) setBusy(false) })
  }, [cancelRemote, device, updateSession])

  useEffect(() => {
    if (!session || session.status !== "capturing") return
    let active = true
    let pending = false
    const sessionId = session.session_id
    const timer = window.setInterval(async () => {
      if (pending) return
      pending = true
      try {
        const next = await dashboardApi.getAudioCalibration(sessionId)
        if (active && mounted.current && sessionRef.current?.session_id === sessionId && !finalizing.current.has(sessionId)) {
          updateSession(next)
        }
      } catch (error) {
        if (active && mounted.current && sessionRef.current?.session_id === sessionId) {
          const message = error instanceof Error ? error.message : "Calibration status unavailable."
          toast.error(message)
          updateSession({ ...session, status: "failed", error: message })
        }
      } finally { pending = false }
    }, 250)
    return () => { active = false; window.clearInterval(timer) }
  }, [session, updateSession])

  const start = async () => {
    if (!device || busy) return
    setBusy(true)
    setActionError(null)
    const requestedDevice = device
    try {
      const next = await dashboardApi.startAudioCalibration(requestedDevice, duration, microphone)
      if (!mounted.current || deviceProp.current !== requestedDevice) {
        await cancelRemote(next.session_id).catch(() => undefined)
        return
      }
      deviceCancellationTarget.current = undefined
      sessionDeviceRef.current = requestedDevice
      setSessionDevice(requestedDevice)
      updateSession(next)
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not start calibration."
      if (mounted.current) {
        setActionError(message)
        toast.error(message)
      }
    } finally { if (mounted.current) setBusy(false) }
  }

  const measureMusic = async () => {
    if (!session || busy) return
    const current = session
    setBusy(true)
    setActionError(null)
    const request = dashboardApi.beginAudioCalibrationMusic(current.session_id)
    pendingSessionCommands.current.set(current.session_id, request)
    try {
      const next = await request
      if (mounted.current && deviceProp.current === sessionDeviceRef.current && sessionRef.current?.session_id === current.session_id) updateSession(next)
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not measure music."
      if (mounted.current) {
        setActionError(message)
        toast.error(message)
      }
    } finally {
      if (pendingSessionCommands.current.get(current.session_id) === request) pendingSessionCommands.current.delete(current.session_id)
      if (mounted.current) setBusy(false)
    }
  }

  const finish = async (apply: boolean) => {
    const current = sessionRef.current
    if (!current || busy) return
    const sessionId = current.session_id
    const result = current.result
    setBusy(true)
    setActionError(null)
    finalizing.current.add(sessionId)
    try {
      if (apply) {
        if (!result) return
        await dashboardApi.finishAudioCalibration(sessionId, true, {
          target_level: result.recommended_target_level,
          noise_floor: result.recommended_noise_floor,
        })
      } else {
        await cancelRemote(sessionId)
      }
      updateSession(null)
      sessionDeviceRef.current = null
      deviceCancellationTarget.current = undefined
      if (mounted.current) setSessionDevice(null)
      if (apply) {
        onApplied()
        toast.success("Audio calibration applied.")
      } else toast.success("Calibration discarded.")
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not finish calibration."
      if (mounted.current) {
        setActionError(message)
        toast.error(message)
      } else {
        void cancelRemote(sessionId).catch(() => undefined)
      }
    } finally {
      finalizing.current.delete(sessionId)
      if (mounted.current) setBusy(false)
    }
  }

  const result = session?.result
  const operationActive = busy || session !== null
  const isQuietCapture = session?.status === "capturing" && session.phase !== "music"
  const isMusicCapture = session?.status === "capturing" && session.phase === "music"
  const progress = session ? Math.max(0, Math.min(100, (session.elapsed_seconds / Math.max(0.01, session.elapsed_seconds + session.remaining_seconds)) * 100)) : 0

  useEffect(() => { onBusyChange?.(operationActive) }, [onBusyChange, operationActive])
  useEffect(() => () => onBusyChange?.(false), [onBusyChange])

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Mic2Icon className="size-4 text-emerald-300" />Guided microphone calibration</CardTitle>
        <CardDescription>Measure a quiet room and normal music level, then review recommendations before applying them.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ol aria-label="Calibration steps" className="grid grid-cols-4 gap-1 text-center text-[11px] sm:gap-2 sm:text-xs">
          {(["Prepare", "Quiet", "Music", "Review"] as const).map((step, index) => {
            const currentStep = session?.status === "complete" ? 3 : isMusicCapture || session?.phase === "waiting_music" ? 2 : isQuietCapture ? 1 : 0
            return <li key={step} aria-current={currentStep === index ? "step" : undefined} className={`rounded-lg px-1 py-2 ${currentStep === index ? "bg-emerald-400/15 font-semibold text-emerald-100" : "bg-white/[0.035] text-muted-foreground"}`}>{index + 1}. {step}</li>
          })}
        </ol>

        {!session && <div className="space-y-4">
          <div className="rounded-xl bg-white/[0.035] p-3">
            <p className="text-sm font-medium">1. Prepare the input</p>
            <p className="mt-1 text-sm text-muted-foreground">Selected microphone: {device ?? "No microphone selected"}. Keep the room quiet for the first measurement, then play music at your usual listening volume. Leave capture gain unchanged.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2"><label htmlFor="calibration-duration" className="text-sm">Measurement duration</label><select id="calibration-duration" value={duration} onChange={(event) => setDuration(Number(event.target.value))} disabled={busy} className="h-10 rounded-lg border border-input bg-background px-3 text-sm"><option value={5}>5 seconds</option><option value={8}>8 seconds</option><option value={12}>12 seconds</option></select></div>
          <Button className="h-11 w-full rounded-xl" disabled={!device || busy} onClick={() => void start()}><SlidersHorizontalIcon />{busy ? "Starting…" : "Start quiet measurement"}</Button>
        </div>}

        {isQuietCapture && <div className="space-y-3" aria-live="polite">
          <div className="rounded-xl bg-white/[0.035] p-3"><p className="text-sm font-medium">2. Quiet measurement</p><p className="mt-1 text-sm text-muted-foreground">{sessionDevice ?? "Selected input"}: keep the room quiet while the noise floor is measured.</p></div>
          <div className="flex justify-between gap-3 text-sm"><span>Measuring room noise…</span><span>{Math.ceil(session.remaining_seconds)}s remaining</span></div>
          <Progress role="progressbar" aria-label="Quiet measurement progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)} value={progress} />
          {session.result && <p className="text-xs text-muted-foreground">Measured noise floor: {session.result.measured_floor.toFixed(3)}</p>}
        </div>}

        {session?.status === "waiting" && <div className="space-y-3">
          <div className="rounded-xl bg-white/[0.035] p-3"><p className="text-sm font-medium">3. Prepare the music measurement</p><p className="mt-1 text-sm text-muted-foreground">Input: {sessionDevice ?? "Selected input"}. Play music through your usual setup at normal listening volume. Keep the microphone and capture gain in the same position.</p></div>
          <Button disabled={busy} onClick={() => void measureMusic()}>{busy ? "Starting measurement…" : "Measure music"}</Button>
        </div>}

        {isMusicCapture && <div className="space-y-3" aria-live="polite">
          <div className="rounded-xl bg-white/[0.035] p-3"><p className="text-sm font-medium">3. Music measurement</p><p className="mt-1 text-sm text-muted-foreground">Input: {sessionDevice ?? "Selected input"}. Keep music playing at your normal listening volume.</p></div>
          <div className="flex justify-between gap-3 text-sm"><span>Measuring music level…</span><span>{Math.ceil(session.remaining_seconds)}s remaining</span></div>
          <Progress role="progressbar" aria-label="Music measurement progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)} value={progress} />
          {session.result && <p className="text-xs text-muted-foreground">Measured music level: {session.result.measured_music_level.toFixed(3)}</p>}
        </div>}

        {session?.status === "complete" && result && <div className="space-y-4">
          <div className="rounded-xl bg-white/[0.035] p-3"><p className="text-sm font-medium">4. Review recommendations</p><p className="mt-1 text-sm text-muted-foreground">Nothing is applied until you choose Apply.</p></div>
          <div className="grid grid-cols-2 gap-2"><Metric label="Measured noise floor" value={result.measured_floor.toFixed(3)} /><Metric label="Measured peak" value={result.measured_peak.toFixed(3)} />{microphone && <><Metric label="Recommended input threshold" value={result.recommended_input_noise_floor.toFixed(4)} /><Metric label="Measured music level" value={result.measured_music_level.toFixed(3)} /></>}<Metric label="Recommended target" value={result.recommended_target_level.toFixed(3)} /><Metric label="Recommended noise floor" value={result.recommended_noise_floor.toFixed(3)} /></div>
          <div className="grid grid-cols-2 gap-2"><Button variant="outline" disabled={busy} onClick={() => void finish(false)}><RotateCcwIcon />Discard</Button><Button disabled={busy} onClick={() => void finish(true)}><CheckCircle2Icon />{busy ? "Applying…" : "Apply"}</Button></div>
        </div>}

        {session && session.status !== "complete" && <Button variant="outline" disabled={busy} onClick={() => void finish(false)}>{busy ? "Cancelling…" : session.error ? "Discard and retry" : "Cancel calibration"}</Button>}
        {(session?.error || actionError) && <p role="alert" className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="size-4 shrink-0" />{session?.error ?? actionError}</p>}
      </CardContent>
    </Card>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl bg-white/[0.035] p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-mono text-lg">{value}</p></div>
}
