import { useState } from "react"
import { Volume2Icon, VolumeXIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import type { BluetoothAction } from "@/hooks/use-bluetooth"
import { dashboardApi, type BluetoothOperation, type BluetoothStatusResponse } from "@/lib/api"

function firstSliderValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] ?? 0 : value
}

function supports(status: BluetoothStatusResponse | null, operation: BluetoothOperation) {
  const operations = status?.capabilities?.operations
  return operations?.length ? operations.includes(operation) : true
}

export function AudioOutputControls({
  status,
  busy,
  onAction,
}: {
  status: BluetoothStatusResponse | null
  busy: boolean
  onAction: (action: BluetoothAction, operation: () => Promise<BluetoothStatusResponse>) => Promise<BluetoothStatusResponse | null>
}) {
  const [volumeDraft, setVolumeDraft] = useState<number | null>(null)
  const selector = status?.default_sink ?? status?.output_devices[0]?.selector ?? ""
  const selectedOutput = status?.output_devices.find((device) => device.selector === selector)
  const volume = volumeDraft ?? status?.output_volume ?? 0

  const run = async (action: BluetoothAction, operation: () => Promise<BluetoothStatusResponse>) => {
    const next = await onAction(action, operation)
    if (next) setVolumeDraft(next.output_volume ?? null)
  }

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><Volume2Icon className="size-4 text-emerald-300" />Audio output</CardTitle>
            <CardDescription>Choose where the Pi sends music from Bluetooth inputs and other local audio.</CardDescription>
          </div>
          <Badge variant={selectedOutput ? "default" : "outline"}>{selectedOutput?.name ?? "Unavailable"}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <Select value={selector} onValueChange={(value) => { if (value) void run("select-output", () => dashboardApi.setAudioOutput(value)) }} disabled={!status?.output_devices.length || busy || !supports(status, "output_select")}>
          <SelectTrigger aria-label="Audio output device" className="h-11 rounded-xl"><SelectValue>{selectedOutput?.name ?? "Select audio output"}</SelectValue></SelectTrigger>
          <SelectContent>{status?.output_devices.map((device) => <SelectItem key={device.selector} value={device.selector}>{device.name}{device.bluetooth ? " · Bluetooth" : ""}</SelectItem>)}</SelectContent>
        </Select>
        {selectedOutput ? (
          <div className="space-y-4 rounded-xl border border-white/5 bg-white/[0.03] p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{selectedOutput.name}</p>
                <p className="text-xs text-muted-foreground">{selectedOutput.bluetooth ? "Bluetooth speaker" : "Local PipeWire output"}</p>
              </div>
              <Badge variant="outline">{status?.default_sink === selectedOutput.selector ? "Active" : "Available"}</Badge>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3 text-sm"><span>Output volume</span><span className="font-mono text-muted-foreground">{Math.round(volume * 100)}%</span></div>
              <Slider aria-label="Audio output volume" min={0} max={1} step={0.01} value={[volume]} disabled={busy || !supports(status, "output_volume") || (volumeDraft === null && selectedOutput.volume === null)} onValueChange={(value) => setVolumeDraft(firstSliderValue(value))} onValueCommitted={(value) => { const next = firstSliderValue(value); setVolumeDraft(next); void run("output-volume", () => dashboardApi.setAudioOutputVolume(selector, next)) }} className="py-3 [&_[data-slot=slider-track]]:h-2 [&_[data-slot=slider-thumb]]:size-5" />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lg bg-black/20 p-2.5">
              <div className="flex items-center gap-2 text-sm">{status?.output_muted ? <VolumeXIcon className="size-4 text-amber-300" /> : <Volume2Icon className="size-4 text-emerald-300" />}<span>{status?.output_muted ? "Muted" : "Output enabled"}</span></div>
              <Switch checked={!status?.output_muted} disabled={busy || !supports(status, "output_mute")} onCheckedChange={(checked) => void run("output-mute", () => dashboardApi.setAudioOutputMute(selector, !checked))} aria-label="Enable audio output" />
            </div>
          </div>
        ) : <p className="text-sm text-muted-foreground">No PipeWire output devices were found.</p>}
        <p className="text-xs text-muted-foreground">The output volume is the Pi’s software level. The connected speaker may have a separate hardware volume.</p>
      </CardContent>
    </Card>
  )
}
