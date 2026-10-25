import { useState } from "react"
import { Link } from "react-router"
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
  variant = "setup",
  statusError,
}: {
  status: BluetoothStatusResponse | null
  busy: boolean
  onAction: (action: BluetoothAction, operation: () => Promise<BluetoothStatusResponse>) => Promise<BluetoothStatusResponse | null>
  variant?: "setup" | "compact"
  statusError?: string | null
}) {
  const selector = status?.default_sink ?? status?.output_devices[0]?.selector ?? ""
  return <AudioOutputControlsForSelector key={selector} status={status} busy={busy} onAction={onAction} selector={selector} variant={variant} statusError={statusError} />
}

function AudioOutputControlsForSelector({
  status,
  busy,
  onAction,
  selector,
  variant,
  statusError,
}: {
  status: BluetoothStatusResponse | null
  busy: boolean
  onAction: (action: BluetoothAction, operation: () => Promise<BluetoothStatusResponse>) => Promise<BluetoothStatusResponse | null>
  selector: string
  variant: "setup" | "compact"
  statusError?: string | null
}) {
  const [volumeDraft, setVolumeDraft] = useState<number | null>(null)
  const selectedOutput = status?.output_devices.find((device) => device.selector === selector)
  const confirmedVolume = selectedOutput?.volume ?? status?.output_volume ?? null
  const volume = volumeDraft ?? confirmedVolume ?? 0
  const isDefaultOutput = selectedOutput !== undefined && status?.default_sink === selectedOutput.selector
  const outputConnection = !selectedOutput
    ? "Unavailable"
    : !selectedOutput.connected
      ? "Disconnected"
      : isDefaultOutput && !status?.output_ready
        ? "Unavailable"
        : isDefaultOutput
          ? "Connected"
          : "Available"
  const errors = variant === "compact"
    ? [...new Set([status?.error, statusError].filter((message): message is string => Boolean(message)))]
    : []

  const run = async (action: BluetoothAction, operation: () => Promise<BluetoothStatusResponse>) => {
    try {
      const next = await onAction(action, operation)
      setVolumeDraft(next ? null : confirmedVolume)
    } catch {
      setVolumeDraft(confirmedVolume)
    }
  }

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
          <div className="min-w-0 flex-1">
            <CardTitle className="flex min-w-0 items-center gap-2"><Volume2Icon className="size-4 shrink-0 text-emerald-300" /><span>Audio output</span></CardTitle>
            <CardDescription className="mt-1">{variant === "setup" ? "Choose where the Pi sends music from Bluetooth inputs and other local audio." : "Control the Pi’s audio output."}</CardDescription>
          </div>
          <Badge className="shrink-0" variant={outputConnection === "Connected" ? "default" : "outline"}>{outputConnection}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {errors.map((message) => <p key={message} role="alert" className="break-words rounded-lg border border-red-300/15 bg-red-500/10 p-2.5 text-sm text-red-200 [overflow-wrap:anywhere]">{message}</p>)}
        {variant === "setup" && (
          <Select value={selector} onValueChange={(value) => { if (value) void run("select-output", () => dashboardApi.setAudioOutput(value)) }} disabled={!status?.output_devices.length || busy || !supports(status, "output_select")}>
            <SelectTrigger aria-label="Audio output device" className="h-auto min-h-11 w-full rounded-xl whitespace-normal [&_[data-slot=select-value]]:line-clamp-none [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:break-words"><SelectValue className="min-w-0 break-words [overflow-wrap:anywhere]">{selectedOutput?.name ?? "Select audio output"}</SelectValue></SelectTrigger>
            <SelectContent>{status?.output_devices.map((device) => <SelectItem key={device.selector} value={device.selector} className="[&>span:first-child]:min-w-0 [&>span:first-child]:shrink [&>span:first-child]:whitespace-normal [&>span:first-child]:[overflow-wrap:anywhere]">{device.name}{device.bluetooth ? " · Bluetooth" : ""}</SelectItem>)}</SelectContent>
          </Select>
        )}
        {selectedOutput ? (
          <div className="space-y-4 rounded-xl border border-white/5 bg-white/[0.03] p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="break-words text-sm font-medium [overflow-wrap:anywhere]">{selectedOutput.name}</p>
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
        ) : (
          <div className="space-y-2 rounded-xl border border-white/5 bg-white/[0.03] p-3">
            <p className="text-sm font-medium">{status?.output_devices.length ? "The selected PipeWire output is unavailable." : status ? "No PipeWire output devices are available." : "Audio output status is unavailable."}</p>
            {variant === "setup" && status && !status.output_devices.length && <p className="text-sm text-muted-foreground">Connect or enable a PipeWire output, then refresh device status below.</p>}
            {variant === "compact" && <Link to="/setup/audio/devices" className="inline-flex min-h-10 items-center text-sm font-medium text-cyan-200 underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Open audio device setup</Link>}
          </div>
        )}
        {variant === "setup" && <p className="text-xs text-muted-foreground">The output volume is the Pi’s software level. The connected speaker may have a separate hardware volume.</p>}
      </CardContent>
    </Card>
  )
}
