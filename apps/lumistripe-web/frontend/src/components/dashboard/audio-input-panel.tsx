import { memo, useCallback, useEffect, useState } from "react"
import { Controller, useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { AudioLinesIcon, CircleAlertIcon, MicIcon, Music2Icon, PauseIcon, PlayIcon, RefreshCwIcon, Repeat2Icon, SaveIcon, ShuffleIcon, SkipBackIcon, SkipForwardIcon, Volume2Icon, VolumeXIcon } from "lucide-react"
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
import { dashboardApi, type AudioSettingsResponse, type BluetoothOperation, type BluetoothStatusResponse, type SpotifyControlAction, type SpotifyStatusResponse } from "@/lib/api"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"

const inputSelectionSchema = z.object({ selected: z.string().trim().min(1, "Choose an input device.") })
type InputFormValues = z.infer<typeof inputSelectionSchema>
const audioSources = ["auto", "spotify", "bluetooth", "mic", "demo", "off"] as const
type AudioSourceValue = typeof audioSources[number]
const audioSourceLabels: Record<AudioSourceValue, string> = {
  auto: "Automatic — Bluetooth first",
  spotify: "Spotify Connect",
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

function formatMilliseconds(value: number | null | undefined) {
  const totalSeconds = Math.max(0, Math.floor((value ?? 0) / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = String(totalSeconds % 60).padStart(2, "0")
  return `${minutes}:${seconds}`
}

function AudioInputPanelView() {
  const [response, setResponse] = useState<AudioSettingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [source, setSource] = useState<AudioSourceValue>("auto")
  const [sourceSaving, setSourceSaving] = useState(false)
  const [spotify, setSpotify] = useState<SpotifyStatusResponse | null>(null)
  const [spotifyBusy, setSpotifyBusy] = useState(false)
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
      setSpotify(next.spotify)
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

  useEffect(() => {
    let mounted = true
    const refresh = async () => {
      try {
        const next = await dashboardApi.getSpotifyStatus()
        if (mounted) setSpotify(next)
      } catch {
        // The audio settings response remains the source of truth while the
        // optional Soloist service is starting or unavailable.
      }
    }
    const interval = window.setInterval(() => void refresh(), 2000)
    return () => {
      mounted = false
      window.clearInterval(interval)
    }
  }, [])

  const saveSource = async () => {
    if (sourceSaving || source === response?.source) return
    setSourceSaving(true)
    try {
      const next = await dashboardApi.setAudioSource(source)
      setResponse(next)
      setSpotify(next.spotify)
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

  const runSpotifyControl = async (action: SpotifyControlAction, value?: number | boolean | string) => {
    if (spotifyBusy) return
    setSpotifyBusy(true)
    try {
      setSpotify(await dashboardApi.controlSpotify(action, value))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Spotify control failed.")
    } finally {
      setSpotifyBusy(false)
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
      setOutputVolumeDraft(status.output_volume ?? null)
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
      setSpotify(next.spotify)
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
  const microphoneDevices = response?.devices.filter((device) => device.selector !== "bluetooth" && device.selector !== "spotify") ?? []
  const selectedName = response?.devices.find((device) => device.selector === selected)?.name
    ?? response?.active_device_name
    ?? "Select microphone"
  const dirty = isDirty
  const bluetoothOperationBusy = bluetoothBusy
  const outputSelector = bluetooth?.default_sink ?? bluetooth?.output_devices[0]?.selector ?? ""
  const outputVolume = outputVolumeDraft ?? bluetooth?.output_volume ?? 0
  const selectedOutput = bluetooth?.output_devices.find((device) => device.selector === outputSelector)
  const outputBusyState = Boolean(bluetoothOperationBusy)
  const spotifyReady = Boolean(spotify?.connected && spotify.logged_in)
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
                  <CardDescription>Choose what drives the Stripe animation. Bluetooth is preferred automatically on hardware, with the microphone as fallback. Spotify Connect is available as a dedicated source.</CardDescription>
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
                  <CardTitle className="flex items-center gap-2"><Music2Icon className="size-4 text-emerald-300" />Spotify Connect</CardTitle>
                  <CardDescription>Play Spotify through Soloist. Its dedicated PipeWire sink is analysed for the Stripe animation and routed to the selected output.</CardDescription>
                </div>
                <Badge variant={spotify?.connected && spotify.logged_in ? "default" : "outline"}>{spotify?.connected && spotify.logged_in ? "Ready" : "Unavailable"}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {!spotify?.configured ? <p className="rounded-xl bg-amber-500/10 p-3 text-sm text-amber-100">Spotify is not configured. Set <span className="font-mono">LUMI_SPOTIFY_API_KEY</span> during deployment and restart the Spotify service.</p> : null}
              {spotify?.configured && !spotify.connected ? <p className="rounded-xl bg-white/[0.03] p-3 text-sm text-muted-foreground">Waiting for the local Soloist WebSocket service.</p> : null}
              {spotify?.configured && spotify.connected && !spotify.logged_in ? <p className="rounded-xl bg-amber-500/10 p-3 text-sm text-amber-100">Open Spotify on a phone or desktop and choose this device, <span className="font-medium">{spotify.device_name ?? "LumiStripe"}</span>, to sign in and start playback.</p> : null}
              {spotify?.error ? <div className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{spotify.error}</span></div> : null}
              {spotify?.track ? (
                <div className="flex items-center gap-3 rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-emerald-400/15 text-emerald-200"><Music2Icon className="size-5" /></div>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{spotify.track.name || "Unknown track"}</p>
                    <p className="truncate text-xs text-muted-foreground">{spotify.track.artists.join(", ") || "Unknown artist"}{spotify.track.album ? ` · ${spotify.track.album}` : ""}</p>
                  </div>
                </div>
              ) : null}
              <div className="flex items-center justify-center gap-2">
                <Button variant="outline" size="icon" className="size-10 rounded-full" disabled={!spotifyReady || spotifyBusy} onClick={() => void runSpotifyControl("skip_prev")} aria-label="Previous Spotify track"><SkipBackIcon /></Button>
                <Button size="icon-lg" className="rounded-full" disabled={!spotifyReady || spotifyBusy} onClick={() => void runSpotifyControl(spotify?.status === "playing" || spotify?.status === "buffering" ? "pause" : "play")} aria-label={spotify?.status === "playing" || spotify?.status === "buffering" ? "Pause Spotify" : "Play Spotify"}>{spotify?.status === "playing" || spotify?.status === "buffering" ? <PauseIcon /> : <PlayIcon />}</Button>
                <Button variant="outline" size="icon" className="size-10 rounded-full" disabled={!spotifyReady || spotifyBusy} onClick={() => void runSpotifyControl("skip_next")} aria-label="Next Spotify track"><SkipForwardIcon /></Button>
              </div>
              <div className="space-y-1">
                <Slider aria-label="Spotify track position" min={0} max={Math.max(1, spotify?.duration_ms ?? spotify?.track?.duration_ms ?? 1)} step={1000} value={[Math.min(spotify?.position_ms ?? 0, Math.max(1, spotify?.duration_ms ?? spotify?.track?.duration_ms ?? 1))]} disabled={!spotifyReady || !spotify?.track || spotifyBusy} onValueCommitted={(value) => void runSpotifyControl("seek", firstSliderValue(value))} />
                <div className="flex justify-between text-xs text-muted-foreground"><span>{formatMilliseconds(spotify?.position_ms)}</span><span>{formatMilliseconds(spotify?.duration_ms ?? spotify?.track?.duration_ms)}</span></div>
              </div>
              <div className="grid gap-4 rounded-xl border border-white/5 bg-white/[0.03] p-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3 text-sm"><span className="flex items-center gap-2"><Volume2Icon className="size-4" />Volume</span><span className="font-mono text-muted-foreground">{spotify?.volume ?? 0}%</span></div>
                  <Slider aria-label="Spotify volume" min={0} max={100} step={1} value={[spotify?.volume ?? 0]} disabled={!spotifyReady || spotifyBusy} onValueCommitted={(value) => void runSpotifyControl("set_volume", firstSliderValue(value))} />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <Button variant={spotify?.shuffle ? "default" : "outline"} size="sm" disabled={!spotifyReady || spotifyBusy} onClick={() => void runSpotifyControl("set_shuffle", !spotify?.shuffle)}><ShuffleIcon />Shuffle</Button>
                  <Button variant="outline" size="sm" disabled={!spotifyReady || spotifyBusy} onClick={() => void runSpotifyControl("set_repeat", spotify?.repeat === "off" ? "context" : spotify?.repeat === "context" ? "track" : "off")}><Repeat2Icon />{spotify?.repeat === "track" ? "Track" : spotify?.repeat === "context" ? "Context" : "Repeat"}</Button>
                </div>
              </div>
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
                  <Select value={field.value} onValueChange={(value) => value && field.onChange(value)} disabled={!microphoneDevices.length || saving}>
                    <SelectTrigger aria-label="Microphone input device" aria-invalid={errors.selected ? true : undefined} className="h-11 min-w-0 flex-1 rounded-xl"><SelectValue>{selectedName}</SelectValue></SelectTrigger>
                    <SelectContent>{microphoneDevices.map((device) => <SelectItem key={device.selector} value={device.selector}>{device.name}</SelectItem>)}</SelectContent>
                  </Select>
                )} />
                <Button variant="outline" size="icon" className="size-11 rounded-xl" disabled={saving} onClick={() => void load()} aria-label="Refresh microphone devices"><RefreshCwIcon /></Button>
              </div>
              {response?.error && <div className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{response.error}</span></div>}
              {!microphoneDevices.length && !response?.error && <p className="text-sm text-muted-foreground">No microphone input devices were found.</p>}
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
