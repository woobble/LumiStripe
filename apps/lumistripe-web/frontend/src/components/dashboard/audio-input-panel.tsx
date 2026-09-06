import { memo, useCallback, useEffect, useState } from "react"
import { Controller, useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { AudioLinesIcon, CircleAlertIcon, MicIcon, RefreshCwIcon, SaveIcon, Volume2Icon, VolumeXIcon } from "lucide-react"
import { z } from "zod"
import { toast } from "sonner"

import { AudioSetupPage } from "@/components/dashboard/setup-nav"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { BluetoothDevicePanel } from "@/components/dashboard/bluetooth-device-panel"
import { useBluetooth, type BluetoothAction } from "@/hooks/use-bluetooth"
import { dashboardApi, type AudioSettingsResponse, type BluetoothOperation, type BluetoothStatusResponse } from "@/lib/api"
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

function firstSliderValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] ?? 0 : value
}

function AudioInputPanelView() {
  const [response, setResponse] = useState<AudioSettingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [source, setSource] = useState<AudioSourceValue>("auto")
  const [sourceSaving, setSourceSaving] = useState(false)
  const [bluetoothAlias, setBluetoothAlias] = useState("")
  const [outputVolumeDraft, setOutputVolumeDraft] = useState<number | null>(null)
  const form = useForm<InputFormValues>({ resolver: zodResolver(inputSelectionSchema), defaultValues: { selected: "" } })
  const { control, handleSubmit, reset, formState: { isDirty, errors } } = form
  useUnsavedChangesGuard(isDirty)
  const bluetoothController = useBluetooth(response?.bluetooth)
  const { status: bluetoothStatus, busy: bluetoothBusy, replaceStatus, refresh: refreshBluetooth, run: runBluetooth } = bluetoothController
  const bluetooth = bluetoothStatus ?? response?.bluetooth

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await dashboardApi.getAudioSettings()
      setResponse(next)
      if (next.bluetooth) replaceStatus(next.bluetooth)
      if (next.bluetooth?.adapter_alias) setBluetoothAlias(next.bluetooth.adapter_alias)
      setOutputVolumeDraft(next.bluetooth?.output_volume ?? null)
      if (isAudioSource(next.source)) setSource(next.source)
      reset({ selected: next.fallback_device ?? next.active_device ?? next.devices[0]?.selector ?? "" })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load audio devices.")
    } finally {
      setLoading(false)
    }
  }, [replaceStatus, reset])

  useEffect(() => {
    void load()
  }, [load])

  const saveSource = async () => {
    if (sourceSaving || source === response?.source) return
    setSourceSaving(true)
    try {
      const next = await dashboardApi.setAudioSource(source)
      setResponse(next)
      if (next.bluetooth) replaceStatus(next.bluetooth)
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
    action: BluetoothAction,
    operation: () => Promise<BluetoothStatusResponse>,
    message?: string,
    onSuccess?: () => void,
  ) => {
    try {
      const status = await runBluetooth(action, operation)
      if (!status) return
      setOutputVolumeDraft(status.output_volume)
      onSuccess?.()
      if (message) toast.success(message)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Bluetooth operation failed.")
    }
  }

  const saveBluetoothAlias = async () => {
    const alias = bluetoothAlias.trim()
    if (!alias) return
    void runBluetoothAction("alias", () => dashboardApi.setBluetoothAlias(alias), "Bluetooth device name saved.", () => setBluetoothAlias(alias))
  }

  const runOutputAction = async (
    action: BluetoothAction,
    operation: () => Promise<BluetoothStatusResponse>,
  ) => {
    void runBluetoothAction(action, operation)
  }

  const save = async ({ selected }: InputFormValues) => {
    if (saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.selectAudioDevice(selected)
      setResponse(next)
      if (next.bluetooth) replaceStatus(next.bluetooth)
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
  const bluetoothOperationBusy = bluetoothBusy
  const outputSelector = bluetooth?.default_sink ?? bluetooth?.output_devices[0]?.selector ?? ""
  const outputVolume = outputVolumeDraft ?? bluetooth?.output_volume ?? 0
  const selectedOutput = bluetooth?.output_devices.find((device) => device.selector === outputSelector)
  const outputBusyState = Boolean(bluetoothOperationBusy)
  const supportsBluetoothOperation = (operation: BluetoothOperation) => {
    const operations = bluetooth?.capabilities?.operations
    return operations?.length ? operations.includes(operation) : true
  }

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
              <Select value={outputSelector} onValueChange={(value) => value && void runOutputAction("select-output", () => dashboardApi.setAudioOutput(value))} disabled={!bluetooth?.output_devices.length || outputBusyState || !supportsBluetoothOperation("output_select")}>
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
                    <Slider aria-label="Audio output volume" min={0} max={1} step={0.01} value={[outputVolume]} disabled={outputBusyState || !supportsBluetoothOperation("output_volume") || (outputVolumeDraft === null && selectedOutput.volume === null)} onValueChange={(value) => setOutputVolumeDraft(firstSliderValue(value))} onValueCommitted={(value) => { const next = firstSliderValue(value); void runOutputAction("output-volume", () => dashboardApi.setAudioOutputVolume(outputSelector, next)) }} className="py-3 [&_[data-slot=slider-track]]:h-2 [&_[data-slot=slider-thumb]]:size-5" />
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-lg bg-black/20 p-2.5">
                    <div className="flex items-center gap-2 text-sm">{bluetooth?.output_muted ? <VolumeXIcon className="size-4 text-amber-300" /> : <Volume2Icon className="size-4 text-emerald-300" />}<span>{bluetooth?.output_muted ? "Muted" : "Output enabled"}</span></div>
                    <Switch checked={!bluetooth?.output_muted} disabled={outputBusyState || !supportsBluetoothOperation("output_mute")} onCheckedChange={(checked) => void runOutputAction("output-mute", () => dashboardApi.setAudioOutputMute(outputSelector, !checked))} aria-label="Enable audio output" />
                  </div>
                </div>
              ) : <p className="text-sm text-muted-foreground">No PipeWire output devices were found.</p>}
              <p className="text-xs text-muted-foreground">The output volume is the Pi’s software level. The connected speaker may have a separate hardware volume.</p>
            </CardContent>
          </Card>

          <BluetoothDevicePanel
            status={bluetooth}
            alias={bluetoothAlias}
            busy={bluetoothOperationBusy}
            onAliasChange={setBluetoothAlias}
            onAliasSave={() => void saveBluetoothAlias()}
            onAction={(action, operation, message) => void runBluetoothAction(action, operation, message)}
            onRefresh={() => void refreshBluetooth()}
          />

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
