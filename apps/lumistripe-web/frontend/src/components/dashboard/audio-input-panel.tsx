import { memo, useCallback, useEffect, useState } from "react"
import { Controller, useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { AudioLinesIcon, BluetoothIcon, CircleAlertIcon, MicIcon, RefreshCwIcon, SaveIcon, SearchIcon, Trash2Icon, Volume2Icon, VolumeXIcon } from "lucide-react"
import { z } from "zod"
import { toast } from "sonner"

import { AudioSetupPage } from "@/components/dashboard/setup-nav"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { dashboardApi, type AudioSettingsResponse, type BluetoothDeviceRole, type BluetoothStatusResponse } from "@/lib/api"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"

const inputSelectionSchema = z.object({ selected: z.string().trim().min(1, "Choose an input device.") })
type InputFormValues = z.infer<typeof inputSelectionSchema>
const audioSources = ["auto", "bluetooth", "mic", "demo", "off"] as const
type AudioSourceValue = typeof audioSources[number]
const audioSourceLabels: Record<AudioSourceValue, string> = {
  auto: "Automatic — Bluetooth first",
  bluetooth: "Bluetooth input",
  mic: "Microphone",
  demo: "Demo beat",
  off: "Off",
}

function isAudioSource(value: string): value is AudioSourceValue {
  return (audioSources as readonly string[]).includes(value)
}

function audioSourceLabel(value: string | null | undefined) {
  return value && isAudioSource(value) ? audioSourceLabels[value] : "Select audio source"
}

function bluetoothLabel(status: BluetoothStatusResponse | undefined) {
  if (!status?.available) return "Unavailable"
  if (status.operation?.startsWith("power:")) return status.operation.endsWith(":on") ? "Enabling…" : "Disabling…"
  if (status.operation === "renaming") return "Renaming…"
  if (!status.powered) return "Off"
  if (status.operation?.startsWith("pairing:")) return "Pairing…"
  if (status.operation?.startsWith("connecting:")) return "Connecting…"
  if (status.operation === "scanning" || status.scanning) return "Scanning…"
  if (status.streaming) return "Input streaming"
  if (status.connected_inputs.length || status.connected_outputs.length) return "Connected"
  return "Ready to pair"
}

function firstSliderValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] ?? 0 : value
}

function AudioInputPanelView() {
  const [response, setResponse] = useState<AudioSettingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [source, setSource] = useState<AudioSourceValue>("auto")
  const [sourceSaving, setSourceSaving] = useState(false)
  const [bluetoothBusy, setBluetoothBusy] = useState<string | null>(null)
  const [bluetoothAlias, setBluetoothAlias] = useState("")
  const [outputBusy, setOutputBusy] = useState<string | null>(null)
  const [outputVolumeDraft, setOutputVolumeDraft] = useState<number | null>(null)
  const form = useForm<InputFormValues>({ resolver: zodResolver(inputSelectionSchema), defaultValues: { selected: "" } })
  const { control, handleSubmit, reset, formState: { isDirty, errors } } = form
  useUnsavedChangesGuard(isDirty)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await dashboardApi.getAudioSettings()
      setResponse(next)
      if (next.bluetooth?.adapter_alias) setBluetoothAlias(next.bluetooth.adapter_alias)
      setOutputVolumeDraft(next.bluetooth?.output_volume ?? null)
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
        setOutputVolumeDraft(status.output_volume)
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
      setOutputVolumeDraft(next.bluetooth?.output_volume ?? null)
      if (isAudioSource(next.source)) setSource(next.source)
      toast.success("Audio input applied.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Audio input could not be changed.")
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
      setOutputVolumeDraft(status.output_volume)
      if (action === "scan") toast.success("Bluetooth scan started.")
      else if (action === "power:on") toast.success("Bluetooth enabled.")
      else if (action === "power:off") toast.success("Bluetooth disabled.")
      else if (action.startsWith("pair")) toast.success("Pairing started. Keep the device's Bluetooth settings open.")
      else if (action.startsWith("forget")) toast.success("Bluetooth device removed.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Bluetooth operation failed.")
    } finally {
      setBluetoothBusy(null)
    }
  }

  const saveBluetoothAlias = async () => {
    const alias = bluetoothAlias.trim()
    if (!alias || bluetoothBusy) return
    setBluetoothBusy("alias")
    try {
      const status = await dashboardApi.setBluetoothAlias(alias)
      setResponse((current) => current ? { ...current, bluetooth: status } : current)
      setOutputVolumeDraft(status.output_volume)
      setBluetoothAlias(alias)
      toast.success("Bluetooth device name saved.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Bluetooth device name could not be changed.")
    } finally {
      setBluetoothBusy(null)
    }
  }

  const runOutputAction = async (
    action: string,
    operation: () => Promise<BluetoothStatusResponse>,
  ) => {
    if (outputBusy || bluetoothBusy) return
    setOutputBusy(action)
    try {
      const status = await operation()
      setResponse((current) => current ? { ...current, bluetooth: status } : current)
      setOutputVolumeDraft(status.output_volume)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Audio output could not be changed.")
    } finally {
      setOutputBusy(null)
    }
  }

  const save = async ({ selected }: InputFormValues) => {
    if (saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.selectAudioDevice(selected)
      setResponse(next)
      setOutputVolumeDraft(next.bluetooth?.output_volume ?? null)
      reset({ selected: next.fallback_device ?? next.active_device ?? "" })
      toast.success("Microphone input applied and saved.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Microphone input could not be changed.")
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
  const outputSelector = bluetooth?.default_sink ?? bluetooth?.output_devices[0]?.selector ?? ""
  const outputVolume = outputVolumeDraft ?? bluetooth?.output_volume ?? 0
  const selectedOutput = bluetooth?.output_devices.find((device) => device.selector === outputSelector)
  const outputBusyState = Boolean(outputBusy || bluetoothOperationBusy)

  const connectRole = (roles: BluetoothDeviceRole[]): BluetoothDeviceRole | undefined =>
    roles.includes("output") ? "output" : roles.includes("input") ? "input" : undefined

  return (
    <AudioSetupPage>
      {loading && !response ? <Skeleton className="h-56 rounded-xl" /> : (
        <div className="space-y-4">
          <Card className="border-white/5 bg-card/80">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2"><AudioLinesIcon className="size-4 text-cyan-300" />Audio input</CardTitle>
                  <CardDescription>Choose what drives the Stripe animation. Bluetooth is preferred automatically on hardware, with the microphone as fallback.</CardDescription>
                </div>
                <Badge variant={response?.monitoring ? "default" : "outline"}>{audioSourceLabel(response?.monitoring ? response?.active_source : response?.source)}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <Select value={source} onValueChange={(value) => { if (value && isAudioSource(value)) setSource(value) }} disabled={sourceSaving}>
                <SelectTrigger aria-label="Audio input source" className="h-11 rounded-xl"><SelectValue>{(value) => audioSourceLabel(value)}</SelectValue></SelectTrigger>
                <SelectContent>
                  {audioSources.map((value) => <SelectItem key={value} value={value}>{audioSourceLabels[value]}</SelectItem>)}
                </SelectContent>
              </Select>
              <Button className="h-12 w-full rounded-xl" disabled={sourceSaving || source === response?.source} onClick={() => void saveSource()}><SaveIcon />{sourceSaving ? "Applying…" : "Save audio input"}</Button>
            </CardContent>
          </Card>

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
              <Select value={outputSelector} onValueChange={(value) => value && void runOutputAction("select-output", () => dashboardApi.setAudioOutput(value))} disabled={!bluetooth?.output_devices.length || outputBusyState}>
                <SelectTrigger aria-label="Audio output device" className="h-11 rounded-xl"><SelectValue>{selectedOutput?.name ?? "Select audio output"}</SelectValue></SelectTrigger>
                <SelectContent>
                  {bluetooth?.output_devices.map((device) => <SelectItem key={device.selector} value={device.selector}>{device.name}{device.bluetooth ? " · Bluetooth" : ""}</SelectItem>)}
                </SelectContent>
              </Select>
              {selectedOutput ? (
                <div className="space-y-4 rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{selectedOutput.name}</p>
                      <p className="text-xs text-muted-foreground">{selectedOutput.bluetooth ? "Bluetooth speaker" : "Local PipeWire output"}</p>
                    </div>
                    <Badge variant="outline">{bluetooth?.default_sink === selectedOutput.selector ? "Active" : "Available"}</Badge>
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-3 text-sm"><span>Output volume</span><span className="font-mono text-muted-foreground">{Math.round(outputVolume * 100)}%</span></div>
                    <Slider aria-label="Audio output volume" min={0} max={1} step={0.01} value={[outputVolume]} disabled={outputBusyState || (outputVolumeDraft === null && selectedOutput.volume === null)} onValueChange={(value) => setOutputVolumeDraft(firstSliderValue(value))} onValueCommitted={(value) => { const next = firstSliderValue(value); void runOutputAction("output-volume", () => dashboardApi.setAudioOutputVolume(outputSelector, next)) }} className="py-3 [&_[data-slot=slider-track]]:h-2 [&_[data-slot=slider-thumb]]:size-5" />
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-lg bg-black/20 p-2.5">
                    <div className="flex items-center gap-2 text-sm">{bluetooth?.output_muted ? <VolumeXIcon className="size-4 text-amber-300" /> : <Volume2Icon className="size-4 text-emerald-300" />}<span>{bluetooth?.output_muted ? "Muted" : "Output enabled"}</span></div>
                    <Switch checked={!bluetooth?.output_muted} disabled={outputBusyState} onCheckedChange={(checked) => void runOutputAction("output-mute", () => dashboardApi.setAudioOutputMute(outputSelector, !checked))} aria-label="Enable audio output" />
                  </div>
                </div>
              ) : <p className="text-sm text-muted-foreground">No PipeWire output devices were found.</p>}
              <p className="text-xs text-muted-foreground">The output volume is the Pi’s software level. The connected speaker may have a separate hardware volume.</p>
            </CardContent>
          </Card>

          <Card className="border-white/5 bg-card/80">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2"><BluetoothIcon className="size-4 text-violet-300" />Bluetooth devices</CardTitle>
                  <CardDescription>Pair phones, speakers, and other Bluetooth audio devices. Role badges describe the direction relative to the Pi.</CardDescription>
                </div>
                <Badge variant={bluetooth?.streaming ? "default" : "outline"}>{bluetoothLabel(bluetooth)}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between gap-3 rounded-xl border border-white/5 bg-white/[0.03] p-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium">Bluetooth radio</div>
                  <p className="text-xs text-muted-foreground">Turn Bluetooth off to disconnect devices and stop new connections.</p>
                </div>
                <Switch checked={Boolean(bluetooth?.powered)} disabled={!bluetooth?.available || bluetoothOperationBusy} onCheckedChange={(checked) => void runBluetoothAction(`power:${checked ? "on" : "off"}`, () => dashboardApi.setBluetoothPower(checked))} aria-label="Enable Bluetooth" />
              </div>
              <div className="space-y-2">
                <label htmlFor="bluetooth-alias" className="text-sm font-medium">Visible Bluetooth name</label>
                <div className="flex gap-2">
                  <Input id="bluetooth-alias" value={bluetoothAlias} maxLength={64} disabled={!bluetooth?.available || bluetoothOperationBusy} onChange={(event) => setBluetoothAlias(event.target.value)} placeholder="LumiStripe" className="h-11 min-w-0 rounded-xl" />
                  <Button variant="outline" className="h-11 shrink-0 rounded-xl" disabled={!bluetooth?.available || bluetoothOperationBusy || !bluetoothAlias.trim() || bluetoothAlias.trim() === bluetooth?.adapter_alias} onClick={() => void saveBluetoothAlias()}><SaveIcon />Save name</Button>
                </div>
                <p className="text-xs text-muted-foreground">This is the name phones, speakers, and other Bluetooth devices see when connecting to the Pi.</p>
              </div>
              <div className="flex gap-2">
                <Button className="h-11 flex-1 rounded-xl" disabled={bluetoothOperationBusy || !bluetooth?.available || !bluetooth?.powered} onClick={() => void runBluetoothAction("scan", dashboardApi.scanBluetooth)}><SearchIcon />{bluetooth?.scanning ? "Scanning…" : "Scan for devices"}</Button>
                <Button variant="outline" size="icon" className="size-11 rounded-xl" disabled={bluetoothOperationBusy} onClick={() => void load()} aria-label="Refresh Bluetooth status"><RefreshCwIcon /></Button>
              </div>
              {(bluetooth?.connected_inputs.length || bluetooth?.connected_outputs.length) ? <div className="grid gap-2 sm:grid-cols-2">
                {bluetooth.connected_inputs.map((device) => <div key={`input-${device.address}`} className="rounded-xl border border-cyan-300/15 bg-cyan-300/5 p-3"><div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium">{device.name}</span><Badge variant="outline">Input</Badge></div><p className="mt-1 text-xs text-muted-foreground">{bluetooth.streaming ? "Music stream detected" : "Connected; waiting for audio"}</p></div>)}
                {bluetooth.connected_outputs.map((device) => <div key={`output-${device.address}`} className="rounded-xl border border-emerald-300/15 bg-emerald-300/5 p-3"><div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium">{device.name}</span><Badge variant="outline">Output</Badge></div><p className="mt-1 text-xs text-muted-foreground">Connected speaker output</p></div>)}
              </div> : null}
              <div className="space-y-2">
                {bluetooth?.devices.map((device) => <div key={device.address} className="flex items-center gap-3 rounded-xl border border-white/5 bg-white/[0.03] p-3"><div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{device.name}</div><div className="mt-1 flex flex-wrap items-center gap-1.5">{device.roles.map((role) => <Badge key={role} variant="outline">{role === "input" ? "Input" : "Output"}</Badge>)}<span className="text-xs text-muted-foreground">{device.address}{device.connected ? " · Connected" : device.paired ? " · Paired" : " · New"}</span></div></div>{device.connected ? <Badge>Connected</Badge> : <Button variant="outline" className="h-9 rounded-lg px-3 text-xs" disabled={bluetoothOperationBusy || !bluetooth?.powered} onClick={() => void runBluetoothAction(`connect:${device.address}`, device.paired ? () => dashboardApi.connectBluetooth(device.address, connectRole(device.roles)) : () => dashboardApi.pairBluetooth(device.address))}>{device.paired ? "Connect" : "Pair"}</Button>}{device.paired && <Button variant="ghost" size="icon" className="size-9 shrink-0 rounded-lg" disabled={bluetoothOperationBusy} onClick={() => void runBluetoothAction(`forget:${device.address}`, () => dashboardApi.forgetBluetooth(device.address))} aria-label={`Forget ${device.name}`}><Trash2Icon /></Button>}</div>)}
              </div>
              {!bluetooth?.available && <div className="flex gap-2 rounded-xl bg-amber-500/10 p-3 text-sm text-amber-100"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>Bluetooth tools are unavailable. Install the Pi Bluetooth/PipeWire setup from the deployment guide.</span></div>}
              {bluetooth?.error && <div className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{bluetooth.error}</span></div>}
              {bluetooth?.available && !bluetooth.powered && !bluetooth.operation && <p className="text-sm text-muted-foreground">Bluetooth is off. Turn it on to scan, pair, or connect a device.</p>}
              {bluetooth?.available && bluetooth.powered && !bluetooth.devices.length && !bluetooth.operation && <p className="text-sm text-muted-foreground">No devices found yet. Put a phone, speaker, or other Bluetooth audio device in pairing mode, then scan.</p>}
              <p className="text-xs text-muted-foreground">Input devices send music to the Pi for animation analysis. Output devices receive the Pi’s audio stream.</p>
            </CardContent>
          </Card>

          <Card className="border-white/5 bg-card/80">
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2"><MicIcon className="size-4 text-violet-300" />Microphone input</CardTitle>
                  <CardDescription>Choose the microphone used when Bluetooth is not connected or is disabled.</CardDescription>
                </div>
                <Badge variant={response?.monitoring && response.active_source === "mic" ? "default" : "outline"}>{response?.monitoring && response.active_source === "mic" ? "Active" : "Fallback"}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-2">
                <Controller control={control} name="selected" render={({ field }) => (
                  <Select value={field.value} onValueChange={(value) => value && field.onChange(value)} disabled={!response?.devices.length || saving}>
                    <SelectTrigger aria-label="Microphone input device" aria-invalid={errors.selected ? true : undefined} className="h-11 min-w-0 flex-1 rounded-xl"><SelectValue>{selectedName}</SelectValue></SelectTrigger>
                    <SelectContent>{response?.devices.filter((device) => device.selector !== "bluetooth").map((device) => <SelectItem key={device.selector} value={device.selector}>{device.name}</SelectItem>)}</SelectContent>
                  </Select>
                )} />
                <Button variant="outline" size="icon" className="size-11 rounded-xl" disabled={saving} onClick={() => void load()} aria-label="Refresh microphone devices"><RefreshCwIcon /></Button>
              </div>
              {response?.error && <div className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{response.error}</span></div>}
              {!response?.devices.filter((device) => device.selector !== "bluetooth").length && !response?.error && <p className="text-sm text-muted-foreground">No microphone input devices were found.</p>}
              {errors.selected && <p role="alert" className="text-sm text-red-300">{errors.selected.message}</p>}
              <Button className="h-12 w-full rounded-xl" disabled={!dirty || saving} onClick={() => void handleSubmit(save)()}><SaveIcon />{saving ? "Applying…" : "Save microphone input"}</Button>
              <p className="text-xs text-muted-foreground">Each microphone keeps its own tuning profile. Fine-tune the active input from the Audio page.</p>
            </CardContent>
          </Card>
        </div>
      )}
    </AudioSetupPage>
  )
}

export const AudioInputPanel = memo(AudioInputPanelView)
