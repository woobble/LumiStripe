import { memo, useCallback, useEffect, useState } from "react"
import { Controller, useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { BluetoothIcon, CircleAlertIcon, MicIcon, RefreshCwIcon, SaveIcon, SearchIcon, Trash2Icon } from "lucide-react"
import { z } from "zod"
import { toast } from "sonner"

import { AudioSetupPage } from "@/components/dashboard/setup-nav"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { dashboardApi, type AudioSettingsResponse, type BluetoothStatusResponse } from "@/lib/api"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"

const inputSelectionSchema = z.object({ selected: z.string().trim().min(1, "Choose an input device.") })
type InputFormValues = z.infer<typeof inputSelectionSchema>
const audioSources = ["auto", "bluetooth", "mic", "demo", "off"] as const
type AudioSourceValue = typeof audioSources[number]

function isAudioSource(value: string): value is AudioSourceValue {
  return (audioSources as readonly string[]).includes(value)
}

function bluetoothLabel(status: BluetoothStatusResponse | undefined) {
  if (!status?.available) return "Unavailable"
  if (status.operation?.startsWith("pairing:")) return "Pairing…"
  if (status.operation?.startsWith("connecting:")) return "Connecting…"
  if (status.operation === "scanning" || status.scanning) return "Scanning…"
  if (status.streaming) return "Streaming"
  if (status.connected_device) return "Connected"
  return "Ready to pair"
}

function AudioInputPanelView() {
  const [response, setResponse] = useState<AudioSettingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [source, setSource] = useState<AudioSourceValue>("auto")
  const [sourceSaving, setSourceSaving] = useState(false)
  const [bluetoothBusy, setBluetoothBusy] = useState<string | null>(null)
  const form = useForm<InputFormValues>({ resolver: zodResolver(inputSelectionSchema), defaultValues: { selected: "" } })
  const { control, handleSubmit, reset, formState: { isDirty, errors } } = form
  useUnsavedChangesGuard(isDirty)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await dashboardApi.getAudioSettings()
      setResponse(next)
      if (isAudioSource(next.source)) setSource(next.source)
      reset({ selected: next.fallback_device ?? next.active_device ?? next.devices[0]?.selector ?? "" })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load audio devices.")
    } finally {
      setLoading(false)
    }
  }, [reset])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => {
      void dashboardApi.getBluetoothStatus().then((status) => {
        setResponse((current) => current ? { ...current, bluetooth: status } : current)
      }).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [load])

  const saveSource = async () => {
    if (sourceSaving || source === response?.source) return
    setSourceSaving(true)
    try {
      const next = await dashboardApi.setAudioSource(source)
      setResponse(next)
      if (isAudioSource(next.source)) setSource(next.source)
      toast.success("Audio source applied.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Audio source could not be changed.")
    } finally {
      setSourceSaving(false)
    }
  }

  const runBluetoothAction = async (
    action: string,
    operation: () => Promise<BluetoothStatusResponse>,
  ) => {
    if (bluetoothBusy) return
    setBluetoothBusy(action)
    try {
      const status = await operation()
      setResponse((current) => current ? { ...current, bluetooth: status } : current)
      if (action === "scan") toast.success("Bluetooth scan started.")
      else if (action.startsWith("pair")) toast.success("Pairing started. Keep the iPhone Bluetooth screen open.")
      else if (action.startsWith("forget")) toast.success("Bluetooth device removed.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Bluetooth operation failed.")
    } finally {
      setBluetoothBusy(null)
    }
  }

  const save = async ({ selected }: InputFormValues) => {
    if (saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.selectAudioDevice(selected)
      setResponse(next)
      reset({ selected: next.fallback_device ?? next.active_device ?? "" })
      toast.success("Audio input applied and saved.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Audio input could not be changed.")
    } finally {
      setSaving(false)
    }
  }

  const selected = form.watch("selected")
  const selectedName = response?.devices.find((device) => device.selector === selected)?.name
    ?? response?.active_device_name
    ?? "Select microphone"
  const dirty = isDirty
  const bluetooth = response?.bluetooth
  const bluetoothOperationBusy = Boolean(bluetoothBusy || bluetooth?.operation)

  return (
    <AudioSetupPage>
      {loading && !response ? <Skeleton className="h-56 rounded-xl" /> : (
        <div className="space-y-4">
          <Card className="border-white/5 bg-card/80">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle>Audio source</CardTitle>
                  <CardDescription>Bluetooth is preferred automatically on hardware, with the microphone as fallback.</CardDescription>
                </div>
                <Badge variant={response?.monitoring ? "default" : "outline"}>{response?.active_source ?? response?.source}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <Select value={source} onValueChange={(value) => { if (value && isAudioSource(value)) setSource(value) }} disabled={sourceSaving}>
                <SelectTrigger aria-label="Audio source" className="h-11 rounded-xl"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Automatic — Bluetooth first</SelectItem>
                  <SelectItem value="bluetooth">Bluetooth phone</SelectItem>
                  <SelectItem value="mic">Microphone</SelectItem>
                  <SelectItem value="demo">Demo beat</SelectItem>
                  <SelectItem value="off">Off</SelectItem>
                </SelectContent>
              </Select>
              <Button className="h-12 w-full rounded-xl" disabled={sourceSaving || source === response?.source} onClick={() => void saveSource()}><SaveIcon />{sourceSaving ? "Applying…" : "Save audio source"}</Button>
            </CardContent>
          </Card>

          <Card className="border-white/5 bg-card/80">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2"><BluetoothIcon className="size-4 text-violet-300" />Bluetooth receiver</CardTitle>
                  <CardDescription>Pair an iPhone with the Pi. Its music continues to the configured sound-system output and drives the Stripe animation.</CardDescription>
                </div>
                <Badge variant={bluetooth?.streaming ? "default" : "outline"}>{bluetoothLabel(bluetooth)}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-2">
                <Button className="h-11 flex-1 rounded-xl" disabled={bluetoothOperationBusy || !bluetooth?.available} onClick={() => void runBluetoothAction("scan", dashboardApi.scanBluetooth)}><SearchIcon />{bluetooth?.scanning ? "Scanning…" : "Scan for phones"}</Button>
                <Button variant="outline" size="icon" className="size-11 rounded-xl" disabled={bluetoothOperationBusy} onClick={() => void load()} aria-label="Refresh Bluetooth status"><RefreshCwIcon /></Button>
              </div>
              {bluetooth?.connected_device && <div className="rounded-xl border border-emerald-300/15 bg-emerald-300/5 p-3 text-sm"><div className="font-medium">{bluetooth.connected_device.name}</div><div className="text-xs text-muted-foreground">{bluetooth.streaming ? "Music stream detected" : "Connected; waiting for audio"}</div></div>}
              <div className="space-y-2">
                {bluetooth?.devices.map((device) => <div key={device.address} className="flex items-center gap-3 rounded-xl border border-white/5 bg-white/[0.03] p-3"><div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{device.name}</div><div className="text-xs text-muted-foreground">{device.address}{device.connected ? " · Connected" : device.paired ? " · Paired" : " · New"}</div></div>{device.connected ? <Badge>Connected</Badge> : <Button variant="outline" className="h-9 rounded-lg px-3 text-xs" disabled={bluetoothOperationBusy} onClick={() => void runBluetoothAction(`connect:${device.address}`, device.paired ? () => dashboardApi.connectBluetooth(device.address) : () => dashboardApi.pairBluetooth(device.address))}>{device.paired ? "Connect" : "Pair"}</Button>}{device.paired && <Button variant="ghost" size="icon" className="size-9 shrink-0 rounded-lg" disabled={bluetoothOperationBusy} onClick={() => void runBluetoothAction(`forget:${device.address}`, () => dashboardApi.forgetBluetooth(device.address))} aria-label={`Forget ${device.name}`}><Trash2Icon /></Button>}</div>)}
              </div>
              {!bluetooth?.available && <div className="flex gap-2 rounded-xl bg-amber-500/10 p-3 text-sm text-amber-100"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>Bluetooth tools are unavailable. Install the Pi Bluetooth/PipeWire setup from the deployment guide.</span></div>}
              {bluetooth?.error && <div className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{bluetooth.error}</span></div>}
              {bluetooth?.available && !bluetooth.devices.length && !bluetooth.operation && <p className="text-sm text-muted-foreground">No phones found yet. Put the iPhone in Bluetooth settings, then scan.</p>}
              <p className="text-xs text-muted-foreground">Output: {bluetooth?.output_ready ? (bluetooth.default_sink ?? "PipeWire speaker") : "No PipeWire output detected"}. After pairing, select the Pi in the iPhone audio output picker; play, pause, tracks, and volume remain controlled from the iPhone.</p>
            </CardContent>
          </Card>

          <Card className="border-white/5 bg-card/80">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2"><MicIcon className="size-4 text-violet-300" />Microphone fallback</CardTitle>
                  <CardDescription>Choose the microphone used when Bluetooth is not connected.</CardDescription>
                </div>
                <Badge variant={response?.monitoring && response.active_source === "mic" ? "default" : "outline"}>{response?.monitoring && response.active_source === "mic" ? "Active" : "Fallback"}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-2">
                <Controller control={control} name="selected" render={({ field }) => (
                  <Select value={field.value} onValueChange={(value) => value && field.onChange(value)} disabled={!response?.devices.length || saving}>
                    <SelectTrigger aria-label="Input device" aria-invalid={errors.selected ? true : undefined} className="h-11 min-w-0 flex-1 rounded-xl"><SelectValue>{selectedName}</SelectValue></SelectTrigger>
                    <SelectContent>{response?.devices.filter((device) => device.selector !== "bluetooth").map((device) => <SelectItem key={device.selector} value={device.selector}>{device.name}</SelectItem>)}</SelectContent>
                  </Select>
                )} />
                <Button variant="outline" size="icon" className="size-11 rounded-xl" disabled={saving} onClick={() => void load()} aria-label="Refresh audio devices"><RefreshCwIcon /></Button>
              </div>
              {response?.error && <div className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{response.error}</span></div>}
              {!response?.devices.filter((device) => device.selector !== "bluetooth").length && !response?.error && <p className="text-sm text-muted-foreground">No input devices were found.</p>}
              {errors.selected && <p role="alert" className="text-sm text-red-300">{errors.selected.message}</p>}
              <Button className="h-12 w-full rounded-xl" disabled={!dirty || saving} onClick={() => void handleSubmit(save)()}><SaveIcon />{saving ? "Applying…" : "Save input device"}</Button>
              <p className="text-xs text-muted-foreground">Each microphone keeps its own tuning profile. Fine-tune the active input from the Audio page.</p>
            </CardContent>
          </Card>
        </div>
      )}
    </AudioSetupPage>
  )
}

export const AudioInputPanel = memo(AudioInputPanelView)
