import { useState } from "react"
import { AudioLinesIcon, PauseIcon, PlayIcon, SaveIcon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import type { AudioSettingsResponse } from "@/lib/api"

function firstSliderValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] ?? 0 : value
}

export function AudioSyncControls({
  settings,
  calibrationRunning,
  onStartCalibration,
  onStopCalibration,
  onSave,
}: {
  settings: AudioSettingsResponse
  calibrationRunning: boolean
  onStartCalibration: () => void
  onStopCalibration: () => void
  onSave: (offsetMs: number) => Promise<AudioSettingsResponse>
}) {
  const [offsetDraft, setOffsetDraft] = useState(settings.sync_offset_ms)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (saving) return
    setSaving(true)
    try {
      const next = await onSave(offsetDraft)
      setOffsetDraft(next.sync_offset_ms)
      toast.success("Sync offset saved for this audio route.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Sync offset could not be saved.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><AudioLinesIcon className="size-4 text-fuchsia-300" />Audio-to-LED sync</CardTitle>
            <CardDescription>Compare the strip flash with the cue playing through the selected speaker, then save an offset for this input and output route.</CardDescription>
          </div>
          <Badge variant={calibrationRunning ? "default" : "outline"}>{calibrationRunning ? "Calibrating" : "Ready"}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="truncate text-xs text-muted-foreground" title={settings.sync_route}>Route: {settings.sync_route}</p>
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3 text-sm"><span>Sync offset</span><span className="font-mono tabular-nums">{offsetDraft > 0 ? "+" : ""}{Math.round(offsetDraft)} ms</span></div>
          <Slider aria-label="Audio to LED sync offset" min={-500} max={500} step={5} value={[offsetDraft]} onValueChange={(value) => setOffsetDraft(firstSliderValue(value))} />
          <div className="flex justify-between text-[11px] text-muted-foreground"><span>LED earlier</span><span>LED later</span></div>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          {calibrationRunning
            ? <Button variant="outline" className="flex-1" onClick={onStopCalibration}><PauseIcon />Stop calibration</Button>
            : <Button variant="outline" className="flex-1" disabled={!settings.monitoring} onClick={onStartCalibration}><PlayIcon />Play sync cue</Button>}
          <Button className="flex-1" disabled={saving || Math.round(offsetDraft) === Math.round(settings.sync_offset_ms)} onClick={() => void save()}><SaveIcon />{saving ? "Saving…" : "Save offset"}</Button>
        </div>
        <p className="text-xs text-muted-foreground">The Pi plays a distinctive cue through its selected PipeWire output. Positive values delay the LEDs. Sync settings are saved separately for each active input and output route.</p>
      </CardContent>
    </Card>
  )
}
