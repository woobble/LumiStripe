import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { CheckIcon, PaletteIcon, RotateCcwIcon, SaveIcon, XIcon } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { DashboardController } from "@/hooks/use-dashboard"
import type { CalibrationPattern, ColorCorrectionProfile } from "@/lib/api"

type Correction = Pick<ColorCorrectionProfile, "red" | "green" | "blue">
type PendingUpdate = { correction: Correction; pattern: CalibrationPattern }

const patterns: Array<{ value: CalibrationPattern; label: string; swatch: string }> = [
  { value: "white", label: "White", swatch: "bg-white" },
  { value: "red", label: "Red", swatch: "bg-red-500" },
  { value: "green", label: "Green", swatch: "bg-green-500" },
  { value: "blue", label: "Blue", swatch: "bg-blue-500" },
]

const channels: Array<{ key: keyof Correction; label: string; color: string }> = [
  { key: "red", label: "Red", color: "[&_[data-slot=slider-range]]:bg-red-400 [&_[data-slot=slider-thumb]]:border-red-300" },
  { key: "green", label: "Green", color: "[&_[data-slot=slider-range]]:bg-green-400 [&_[data-slot=slider-thumb]]:border-green-300" },
  { key: "blue", label: "Blue", color: "[&_[data-slot=slider-range]]:bg-blue-400 [&_[data-slot=slider-thumb]]:border-blue-300" },
]

function sliderValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] : value
}

export function CalibrationPanel({ controller }: { controller: DashboardController }) {
  const {
    state,
    pendingCommand,
    startCalibration,
    updateCalibration,
    finishCalibration,
  } = controller
  const [selectedOutput, setSelectedOutput] = useState(0)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Correction | null>(null)
  const [pattern, setPattern] = useState<CalibrationPattern>("white")
  const [finishing, setFinishing] = useState(false)
  const sessionRef = useRef<string | null>(null)
  const pendingRef = useRef<PendingUpdate | null>(null)
  const timerRef = useRef<number | null>(null)
  const inFlightRef = useRef<Promise<boolean> | null>(null)

  const profile = useMemo(
    () => state?.color_corrections.find((item) => item.output_index === selectedOutput),
    [selectedOutput, state?.color_corrections],
  )

  const drainUpdates = useCallback((): Promise<boolean> => {
    if (inFlightRef.current) return inFlightRef.current
    const activeSession = sessionRef.current
    if (!activeSession) return Promise.resolve(false)
    const operation = (async () => {
      while (pendingRef.current) {
        const update = pendingRef.current
        pendingRef.current = null
        const applied = await updateCalibration(
          activeSession,
          update.correction,
          update.pattern,
        )
        if (!applied) {
          pendingRef.current = null
          return false
        }
      }
      return true
    })()
    inFlightRef.current = operation
    void operation.finally(() => {
      inFlightRef.current = null
      if (pendingRef.current) void drainUpdates()
    })
    return operation
  }, [updateCalibration])

  const queueUpdate = useCallback((correction: Correction, nextPattern: CalibrationPattern, immediate = false) => {
    pendingRef.current = { correction, pattern: nextPattern }
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    if (immediate) {
      timerRef.current = null
      void drainUpdates()
      return
    }
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      void drainUpdates()
    }, 75)
  }, [drainUpdates])

  useEffect(() => {
    sessionRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    if (sessionId && state && !state.calibration.active) {
      sessionRef.current = null
      setSessionId(null)
      setDraft(null)
      toast.info("The calibration session ended.")
    }
  }, [sessionId, state])

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!sessionRef.current) return
      event.preventDefault()
    }
    window.addEventListener("beforeunload", warnBeforeUnload)
    return () => window.removeEventListener("beforeunload", warnBeforeUnload)
  }, [])

  useEffect(() => () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    const activeSession = sessionRef.current
    if (activeSession) void finishCalibration(activeSession, false)
  }, [finishCalibration])

  if (!state) return null
  const activeElsewhere = state.calibration.active && sessionId === null
  const busy = finishing || (pendingCommand !== null && sessionId === null)

  const start = async () => {
    if (!profile) return
    setDraft({ red: profile.red, green: profile.green, blue: profile.blue })
    setPattern("white")
    const nextSession = await startCalibration(selectedOutput)
    if (!nextSession) {
      setDraft(null)
      return
    }
    sessionRef.current = nextSession
    setSessionId(nextSession)
  }

  const changeChannel = (channel: keyof Correction, value: number | readonly number[]) => {
    if (!draft) return
    const next = { ...draft, [channel]: sliderValue(value) }
    setDraft(next)
    queueUpdate(next, pattern)
  }

  const changePattern = (values: string[]) => {
    const next = values[0] as CalibrationPattern | undefined
    if (!next || !draft) return
    setPattern(next)
    queueUpdate(draft, next, true)
  }

  const reset = () => {
    const neutral = { red: 255, green: 255, blue: 255 }
    setDraft(neutral)
    queueUpdate(neutral, pattern, true)
  }

  const finish = async (save: boolean) => {
    const activeSession = sessionRef.current
    if (!activeSession || finishing) return
    setFinishing(true)
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    if (save && !(await drainUpdates())) {
      setFinishing(false)
      return
    }
    if (!save) {
      pendingRef.current = null
      if (inFlightRef.current) await inFlightRef.current
    }
    const completed = await finishCalibration(activeSession, save)
    if (!completed) {
      setFinishing(false)
      return
    }
    sessionRef.current = null
    setSessionId(null)
    setDraft(null)
    setFinishing(false)
    toast.success(save ? "Color correction saved." : "Calibration cancelled.")
  }

  return (
    <div className="space-y-4 pb-4">
      <div>
        <h2 className="text-lg font-semibold">Color calibration</h2>
        <p className="text-sm text-muted-foreground">Balance each strip using full-brightness test patterns.</p>
      </div>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><PaletteIcon className="size-4 text-fuchsia-300" aria-hidden="true" />Output</CardTitle>
          <CardDescription>Select the strip whose white balance you want to correct.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2">
          {state.color_corrections.map((output) => (
            <button
              type="button"
              key={output.output_index}
              disabled={sessionId !== null || activeElsewhere}
              onClick={() => setSelectedOutput(output.output_index)}
              className={`flex min-h-14 items-center justify-between rounded-xl border px-4 text-left transition-colors ${selectedOutput === output.output_index ? "border-violet-300/30 bg-violet-400/10" : "border-white/5 bg-white/[0.03]"}`}
            >
              <span><span className="block font-medium">{output.name}</span><span className="block text-xs text-muted-foreground">{output.device}</span></span>
              {selectedOutput === output.output_index && <CheckIcon className="size-4 text-violet-200" aria-hidden="true" />}
            </button>
          ))}
        </CardContent>
      </Card>

      {!sessionId ? (
        <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
          <CardHeader>
            <CardTitle>Guided calibration</CardTitle>
            <CardDescription>{activeElsewhere ? "Calibration is active in another browser. It will restore automatically if abandoned." : "Normal playback will pause. The selected strip starts white while every other output turns off."}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <ol className="space-y-2 text-sm text-muted-foreground">
              <li>1. Start with white and reduce channels that look too strong.</li>
              <li>2. Check the red, green, and blue patterns for unwanted tint.</li>
              <li>3. Save to apply this profile to every playback mode.</li>
            </ol>
            <Button className="h-12 w-full rounded-xl" disabled={!profile || busy || activeElsewhere} onClick={() => void start()}>
              <PaletteIcon aria-hidden="true" />Start calibration
            </Button>
          </CardContent>
        </Card>
      ) : draft && (
        <>
          <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
            <CardHeader><CardTitle>Test pattern</CardTitle><CardDescription>Patterns bypass normal brightness while calibration is active.</CardDescription></CardHeader>
            <CardContent>
              <ToggleGroup value={[pattern]} onValueChange={changePattern} disabled={busy} variant="outline" spacing={1} className="grid w-full grid-cols-4 rounded-xl bg-black/20 p-1" aria-label="Calibration test pattern">
                {patterns.map((item) => (
                  <ToggleGroupItem key={item.value} value={item.value} className="h-12 min-w-0 flex-col gap-1 rounded-lg border-0 px-1 text-xs data-pressed:bg-violet-400/15">
                    <span className={`size-3 rounded-full border border-white/20 ${item.swatch}`} aria-hidden="true" />{item.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </CardContent>
          </Card>

          <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
            <CardHeader><CardTitle>Channel gains</CardTitle><CardDescription>Lower dominant channels until white looks neutral.</CardDescription></CardHeader>
            <CardContent className="space-y-6">
              {channels.map((channel) => (
                <div key={channel.key}>
                  <div className="mb-2 flex items-center justify-between"><span className="text-sm font-medium">{channel.label}</span><span className="text-sm tabular-nums text-muted-foreground">{Math.round(draft[channel.key] / 255 * 100)}% · {draft[channel.key]}</span></div>
                  <Slider aria-label={`${channel.label} correction`} min={0} max={255} step={1} value={[draft[channel.key]]} disabled={busy} onValueChange={(value) => changeChannel(channel.key, value)} className={`py-3 [&_[data-slot=slider-track]]:h-2 [&_[data-slot=slider-thumb]]:size-5 [&_[data-slot=slider-thumb]]:border-2 ${channel.color}`} />
                </div>
              ))}
              <Button variant="outline" className="h-11 w-full rounded-xl" disabled={busy} onClick={reset}><RotateCcwIcon aria-hidden="true" />Reset to neutral</Button>
            </CardContent>
          </Card>

          <div className="grid grid-cols-2 gap-3">
            <Button variant="outline" className="h-12 rounded-xl" disabled={busy} onClick={() => void finish(false)}><XIcon aria-hidden="true" />Cancel</Button>
            <Button className="h-12 rounded-xl" disabled={busy} onClick={() => void finish(true)}><SaveIcon aria-hidden="true" />Save profile</Button>
          </div>
        </>
      )}
    </div>
  )
}
