import { useEffect, useRef, useState } from "react"
import { AudioLinesIcon, CheckCircle2Icon, PauseIcon, PlayIcon, SaveIcon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"
import type { AudioSettingsResponse } from "@/lib/api"

function firstSliderValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] ?? 0 : value
}

function offsetLabel(value: number) {
  if (value > 0) return `Delay LEDs by ${Math.round(value)} ms`
  if (value < 0) return `Advance LEDs by ${Math.abs(Math.round(value))} ms`
  return "No offset"
}

export function AudioSyncControls({
  settings,
  calibrationRunning,
  calibrationBusy = false,
  calibrationStarting = false,
  calibrationStopping = false,
  onStartCalibration,
  onStopCalibration,
  onSave,
}: {
  settings: AudioSettingsResponse
  calibrationRunning: boolean
  calibrationBusy?: boolean
  calibrationStarting?: boolean
  calibrationStopping?: boolean
  onStartCalibration: () => void
  onStopCalibration: () => void
  onSave: (offsetMs: number) => Promise<AudioSettingsResponse>
}) {
  const [offsetDraft, setOffsetDraft] = useState(settings.sync_offset_ms)
  const [saving, setSaving] = useState(false)
  const [savedOffset, setSavedOffset] = useState(settings.sync_offset_ms)
  const route = useRef(settings.sync_route)
  const dirty = Math.round(offsetDraft) !== Math.round(settings.sync_offset_ms)
  useUnsavedChangesGuard(dirty)

  useEffect(() => {
    const routeChanged = route.current !== settings.sync_route
    route.current = settings.sync_route
    if (routeChanged || !dirty) {
      setOffsetDraft(settings.sync_offset_ms)
      setSavedOffset(settings.sync_offset_ms)
    }
  }, [dirty, settings.sync_offset_ms, settings.sync_route])

  const save = async () => {
    if (saving || calibrationRunning || calibrationBusy) return
    setSaving(true)
    try {
      const next = await onSave(offsetDraft)
      setOffsetDraft(next.sync_offset_ms)
      setSavedOffset(next.sync_offset_ms)
      toast.success("Sync offset saved for this audio route.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Sync offset could not be saved.")
    } finally {
      setSaving(false)
    }
  }

  const routeParts = settings.sync_route.split("|")
  const input = settings.active_device_name ?? routeParts[1] ?? "Unknown input"
  const outputSelector = routeParts[2] ?? "Unknown output"
  const output = settings.bluetooth?.output_devices?.find((item) => item.selector === outputSelector)?.name ?? outputSelector
  const inputSource = settings.active_source || routeParts[0] || settings.source
  const offsetIsDirty = Math.round(offsetDraft) !== Math.round(savedOffset)

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><AudioLinesIcon className="size-4 text-fuchsia-300" />Guided audio-to-LED sync</CardTitle>
            <CardDescription>Compare the speaker cue with the LEDs, adjust timing by ear and sight, then save the offset for this route.</CardDescription>
          </div>
          <Badge variant={calibrationRunning ? "default" : "outline"}>{calibrationStarting ? "Starting" : calibrationStopping ? "Stopping" : calibrationRunning ? "Cue playing" : "Ready"}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <section className="space-y-2 rounded-xl bg-white/[0.035] p-3" aria-labelledby="sync-route-title">
          <h3 id="sync-route-title" className="text-sm font-semibold">1. Confirm the active route</h3>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <div className="min-w-0"><dt className="text-xs text-muted-foreground">Input</dt><dd className="truncate" title={`${inputSource} · ${input}`}>{inputSource} · {input}</dd></div>
            <div className="min-w-0"><dt className="text-xs text-muted-foreground">Output</dt><dd className="truncate" title={output}>{output}</dd></div>
          </dl>
          <p className="truncate text-[11px] text-muted-foreground" title={settings.sync_route}>Route: {settings.sync_route}</p>
          {!settings.monitoring && <p role="status" className="text-xs text-amber-200">No active audio input. Start or select an available source before playing the cue.</p>}
        </section>

        <section className="space-y-3 rounded-xl bg-white/[0.035] p-3" aria-labelledby="sync-cue-title">
          <div><h3 id="sync-cue-title" className="text-sm font-semibold">2. Play the comparison cue</h3><p className="mt-1 text-xs text-muted-foreground">The cue plays through the selected output while the strip flashes.</p></div>
          {calibrationRunning || calibrationStopping
            ? <Button variant="outline" className="w-full" disabled={calibrationBusy} onClick={onStopCalibration}><PauseIcon />{calibrationStopping ? "Stopping cue…" : "Stop sync cue"}</Button>
            : <Button variant="outline" className="w-full" disabled={!settings.monitoring || calibrationBusy} onClick={onStartCalibration}><PlayIcon />{calibrationStarting ? "Starting cue…" : "Play sync cue"}</Button>}
        </section>

        <section className="space-y-3 rounded-xl bg-white/[0.035] p-3" aria-labelledby="sync-adjust-title">
          <div><h3 id="sync-adjust-title" className="text-sm font-semibold">3. Adjust the timing</h3><p className="mt-1 text-xs text-muted-foreground">If the LEDs flash before the sound, move toward Delay. If they flash after the sound, move toward Advance.</p></div>
          <div className="flex items-center justify-between gap-3 text-sm"><span>Offset</span><span className="flex flex-wrap justify-end gap-x-1 text-right font-mono tabular-nums"><span>{offsetDraft > 0 ? "+" : ""}{Math.round(offsetDraft)} ms</span><span className="font-sans text-xs text-muted-foreground">· {offsetLabel(offsetDraft)}</span></span></div>
          <Slider aria-label="Audio to LED sync offset" min={-500} max={500} step={5} value={[offsetDraft]} onValueChange={(value) => setOffsetDraft(firstSliderValue(value))} />
          <div className="flex justify-between gap-3 text-[11px] text-muted-foreground"><span>Advance LEDs (−500 ms)</span><span className="text-right">Delay LEDs (+500 ms)</span></div>
          <p className="text-xs text-muted-foreground">Positive values delay the LEDs; negative values advance them. Adjustments are in 5 ms steps.</p>
        </section>

        <section className="space-y-3 rounded-xl border border-white/5 p-3" aria-labelledby="sync-save-title">
          <div><h3 id="sync-save-title" className="text-sm font-semibold">4. Stop, save and verify</h3><p className="mt-1 text-xs text-muted-foreground">Stop the cue, save this route’s offset, then play music to confirm the timing.</p></div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span className="text-muted-foreground">Saved for this route</span><span className="inline-flex items-center gap-1.5 font-mono tabular-nums" aria-label={`Saved offset ${savedOffset > 0 ? "+" : ""}${Math.round(savedOffset)} milliseconds`}><CheckCircle2Icon className="size-4 text-emerald-300" />{savedOffset > 0 ? "+" : ""}{Math.round(savedOffset)} ms saved</span></div>
          <Button className="w-full" disabled={saving || !offsetIsDirty || calibrationRunning || calibrationBusy} onClick={() => void save()}><SaveIcon />{saving ? "Saving…" : "Save route offset"}</Button>
        </section>
      </CardContent>
    </Card>
  )
}
