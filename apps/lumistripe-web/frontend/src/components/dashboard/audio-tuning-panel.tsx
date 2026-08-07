import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ActivityIcon,
  AudioLinesIcon,
  GaugeIcon,
  MicIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SaveIcon,
  SlidersHorizontalIcon,
  WavesIcon,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Slider } from "@/components/ui/slider"
import {
  ACCESS_REVOKED_EVENT,
  audioWebsocketUrl,
  dashboardApi,
  type AudioSettingsResponse,
  type AudioTelemetry,
  type AudioTuningValues,
} from "@/lib/api"

const bandLabels = ["20–60", "60–120", "120–250", "250–500", "500–1k", "1–2.5k", "2.5–6k", "6–16k"]

const emptyTelemetry: AudioTelemetry = {
  sequence: 0,
  fresh: false,
  input_level: 0,
  processed_level: 0,
  bands: [0, 0, 0, 0, 0, 0, 0, 0],
  beat: false,
  beat_strength: 0,
  bpm: 0,
  estimated_noise_floor: 0,
  configured_noise_floor: 0.015,
  normalization_gain: 1,
  program_loudness: 0,
  musical_impact: 0,
  gate: "idle",
  gate_preview: true,
  gate_energy: 0,
  gate_onset: 0,
  gate_beat_density: 0,
  gate_brightness: 0,
  health: "inactive",
}

type SliderKey = keyof AudioTuningValues

function firstValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] : value
}

function percent(value: number) {
  return Math.round(Math.min(1, Math.max(0, value)) * 100)
}

function TuningSlider({
  label,
  description,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string
  description: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
}) {
  return (
    <div className="space-y-2.5 rounded-xl bg-white/[0.035] p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        <span className="min-w-12 rounded-lg bg-black/20 px-2 py-1 text-right font-mono text-xs">{value.toFixed(3)}</span>
      </div>
      <Slider
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={[value]}
        onValueChange={(next) => onChange(firstValue(next))}
        className="py-2 [&_[data-slot=slider-track]]:h-2 [&_[data-slot=slider-thumb]]:size-5"
      />
    </div>
  )
}

function GateMetric({ label, value, threshold }: { label: string; value: number; threshold: number }) {
  const width = percent(value)
  const marker = percent(threshold)
  return (
    <div>
      <div className="mb-1.5 flex justify-between text-xs"><span>{label}</span><span className="text-muted-foreground">{value.toFixed(3)} / {threshold.toFixed(3)}</span></div>
      <div className="relative h-2 overflow-hidden rounded-full bg-white/5">
        <div className="h-full rounded-full bg-cyan-400 transition-[width] duration-75" style={{ width: `${width}%` }} />
        <span className="absolute inset-y-0 w-px bg-white" style={{ left: `${marker}%` }} />
      </div>
    </div>
  )
}

export function AudioTuningPanel() {
  const [response, setResponse] = useState<AudioSettingsResponse | null>(null)
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null)
  const [draft, setDraft] = useState<AudioTuningValues | null>(null)
  const [telemetry, setTelemetry] = useState(emptyTelemetry)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await dashboardApi.getAudioSettings()
      setResponse(next)
      const selector = next.active_device ?? next.devices[0]?.selector ?? null
      setSelectedDevice(selector)
      const device = next.devices.find((item) => item.selector === selector)
      setDraft(device?.settings ?? next.settings)
      setDirty(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load audio settings.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    let disposed = false
    let socket: WebSocket | null = null
    let timer: number | undefined
    let attempts = 0
    const connect = () => {
      if (disposed) return
      socket = new WebSocket(audioWebsocketUrl())
      socket.onopen = () => { attempts = 0 }
      socket.onmessage = (event) => {
        try { setTelemetry(JSON.parse(String(event.data)) as AudioTelemetry) } catch { socket?.close() }
      }
      socket.onerror = () => socket?.close()
      socket.onclose = (event) => {
        if (disposed) return
        if (event.code === 4401) {
          window.dispatchEvent(new Event(ACCESS_REVOKED_EVENT))
          return
        }
        attempts += 1
        timer = window.setTimeout(connect, Math.min(1000 * 2 ** (attempts - 1), 10_000))
      }
    }
    connect()
    return () => {
      disposed = true
      if (timer !== undefined) window.clearTimeout(timer)
      socket?.close()
    }
  }, [])

  const selectDevice = (selector: string) => {
    const device = response?.devices.find((item) => item.selector === selector)
    if (!device) return
    setSelectedDevice(selector)
    setDraft(device.settings)
    setDirty(selector !== response?.active_device)
  }

  const change = (key: SliderKey, value: number) => {
    setDraft((current) => current ? { ...current, [key]: value } : current)
    setDirty(true)
  }

  const apply = async () => {
    if (!selectedDevice || !draft || saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.updateAudioSettings(selectedDevice, draft)
      setResponse(next)
      setDraft(next.settings)
      setDirty(false)
      toast.success("Audio profile applied and saved.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Audio settings could not be applied.")
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    if (!selectedDevice || saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.resetAudioSettings(selectedDevice)
      setResponse(next)
      setDraft(next.settings)
      setDirty(false)
      toast.success("Default audio profile restored.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Defaults could not be restored.")
    } finally {
      setSaving(false)
    }
  }

  const activeDeviceName = useMemo(
    () => response?.devices.find((item) => item.selector === selectedDevice)?.name ?? response?.active_device_name,
    [response, selectedDevice],
  )

  if (loading && !response) {
    return <div className="space-y-4"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-64 rounded-xl" /><Skeleton className="h-72 rounded-xl" /></div>
  }
  if (!response || !draft) {
    return <Card><CardHeader><CardTitle>Audio tuning unavailable</CardTitle><CardDescription>Could not load microphone settings.</CardDescription></CardHeader><CardContent><Button onClick={() => void load()}>Try again</Button></CardContent></Card>
  }

  const thresholds = [
    ["Energy", telemetry.gate_energy, draft.energy_threshold],
    ["Onset", telemetry.gate_onset, draft.onset_threshold],
    ["Beat density", telemetry.gate_beat_density, draft.beat_density_threshold],
    ["Brightness", telemetry.gate_brightness, draft.brightness_threshold],
  ] as const

  return (
    <div className="space-y-4 pb-4">
      <div>
        <h2 className="text-lg font-semibold">Audio tuning</h2>
        <p className="text-sm text-muted-foreground">See what the microphone hears and tune Music mode in real time.</p>
      </div>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader><CardTitle className="flex items-center gap-2"><MicIcon className="size-4 text-violet-300" />Input device</CardTitle><CardDescription>{response.monitoring ? `Monitoring ${response.active_device_name}` : `Audio source: ${response.source}`}</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Select value={selectedDevice} onValueChange={(value) => value && selectDevice(value)} disabled={!response.devices.length || saving}>
              <SelectTrigger className="h-11 min-w-0 flex-1 rounded-xl"><SelectValue>{activeDeviceName ?? "Select microphone"}</SelectValue></SelectTrigger>
              <SelectContent>{response.devices.map((device) => <SelectItem key={device.selector} value={device.selector}>{device.name}</SelectItem>)}</SelectContent>
            </Select>
            <Button variant="outline" size="icon" className="size-11 rounded-xl" onClick={() => void load()} aria-label="Refresh audio devices"><RefreshCwIcon /></Button>
          </div>
          {response.error && <p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-200">{response.error}</p>}
        </CardContent>
      </Card>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader className="grid-cols-[1fr_auto]"><div><CardTitle className="flex items-center gap-2"><GaugeIcon className="size-4 text-cyan-300" />Live input</CardTitle><CardDescription>Pre-cutoff level with ambient and configured floor markers.</CardDescription></div><Badge variant={telemetry.fresh ? "default" : "outline"}>{telemetry.health}</Badge></CardHeader>
        <CardContent className="space-y-4">
          <div>
            <div className="mb-2 flex items-end justify-between"><span className="text-3xl font-semibold tabular-nums">{percent(telemetry.input_level)}%</span><span className="text-xs text-muted-foreground">Processed {percent(telemetry.processed_level)}%</span></div>
            <div className="relative h-4 overflow-hidden rounded-full bg-white/5">
              <div className="h-full rounded-full bg-gradient-to-r from-violet-500 via-cyan-400 to-emerald-300 transition-[width] duration-75" style={{ width: `${percent(telemetry.input_level)}%` }} />
              <span className="absolute inset-y-0 w-0.5 bg-amber-300" title="Estimated floor" style={{ left: `${percent(telemetry.estimated_noise_floor)}%` }} />
              <span className="absolute inset-y-0 w-0.5 bg-white" title="Configured cutoff" style={{ left: `${percent(telemetry.configured_noise_floor)}%` }} />
            </div>
            <div className="mt-2 flex justify-between text-[11px] text-muted-foreground"><span>Ambient {telemetry.estimated_noise_floor.toFixed(3)}</span><span>Cutoff {telemetry.configured_noise_floor.toFixed(3)}</span></div>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className={`rounded-xl p-3 text-center transition-colors ${telemetry.beat ? "bg-fuchsia-400/20 text-fuchsia-100" : "bg-white/[0.035]"}`}><AudioLinesIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{telemetry.beat ? "Beat" : "Waiting"}</p><p className="text-[11px] text-muted-foreground">{percent(telemetry.beat_strength)}% strength</p></div>
            <div className="rounded-xl bg-white/[0.035] p-3 text-center"><WavesIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{telemetry.bpm > 0 ? telemetry.bpm.toFixed(0) : "—"} BPM</p><p className="text-[11px] text-muted-foreground">Detected tempo</p></div>
            <div className="rounded-xl bg-white/[0.035] p-3 text-center"><ActivityIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{telemetry.normalization_gain.toFixed(2)}×</p><p className="text-[11px] text-muted-foreground">Auto gain</p></div>
            <div className="rounded-xl bg-white/[0.035] p-3 text-center"><GaugeIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{percent(telemetry.musical_impact)}%</p><p className="text-[11px] text-muted-foreground">Musical impact</p></div>
          </div>
          <div>
            <div className="mb-1.5 flex justify-between text-xs"><span>Program reference</span><span className="text-muted-foreground">{telemetry.program_loudness.toFixed(3)}</span></div>
            <div className="h-2 overflow-hidden rounded-full bg-white/5"><div className="h-full rounded-full bg-violet-400 transition-[width] duration-150" style={{ width: `${percent(telemetry.program_loudness)}%` }} /></div>
          </div>
        </CardContent>
      </Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle className="flex items-center gap-2"><AudioLinesIcon className="size-4 text-fuchsia-300" />Frequency bands</CardTitle><CardDescription>Energy by frequency range in Hz.</CardDescription></CardHeader><CardContent><div className="grid h-44 grid-cols-8 items-end gap-1.5">{telemetry.bands.map((value, index) => <div key={bandLabels[index]} className="flex h-full min-w-0 flex-col justify-end gap-2"><div className="relative flex-1 overflow-hidden rounded-md bg-white/5"><div className="absolute inset-x-0 bottom-0 rounded-md bg-gradient-to-t from-violet-500 to-cyan-300 transition-[height] duration-75" style={{ height: `${Math.max(2, percent(value))}%` }} /></div><span className="truncate text-center text-[8px] text-muted-foreground">{bandLabels[index]}</span></div>)}</div></CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader className="grid-cols-[1fr_auto]"><div><CardTitle>Music gate</CardTitle><CardDescription>{telemetry.gate_preview ? "Preview outside Music mode" : "Currently controlling Music mode"}</CardDescription></div><Badge variant={telemetry.gate === "music" ? "default" : "outline"}>{telemetry.gate}</Badge></CardHeader><CardContent className="space-y-3">{thresholds.map(([label, value, threshold]) => <GateMetric key={label} label={label} value={value} threshold={threshold} />)}</CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle className="flex items-center gap-2"><SlidersHorizontalIcon className="size-4 text-violet-300" />Normalization</CardTitle><CardDescription>The target used by automatic software gain.</CardDescription></CardHeader><CardContent><TuningSlider label="Target level" description="Higher values make quiet microphones more prominent." value={draft.target_level} min={0.1} max={0.8} step={0.005} onChange={(value) => change("target_level", value)} /></CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Musical response</CardTitle><CardDescription>Choose how much contrast the lights keep between quiet passages and loud highlights.</CardDescription></CardHeader><CardContent><TuningSlider label="Calm ↔ Dramatic" description="Higher values make quiet parts gentler and reserve stronger effects for peaks." value={draft.dynamic_response} min={0} max={1} step={0.01} onChange={(value) => change("dynamic_response", value)} /></CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Activation thresholds</CardTitle><CardDescription>Lower values make Music mode easier to activate.</CardDescription></CardHeader><CardContent className="space-y-2">{([
        ["energy_threshold", "Energy", "Minimum overall signal energy."],
        ["onset_threshold", "Onset", "Minimum transient strength for rhythmic input."],
        ["beat_density_threshold", "Beat density", "Required density of detected beats."],
        ["brightness_threshold", "Brightness", "Required treble energy for broadband music."],
        ["spectral_balance_ratio", "Spectral balance", "How balanced bass and treble must be."],
      ] as const).map(([key, label, description]) => <TuningSlider key={key} label={label} description={description} value={draft[key]} min={0} max={1} step={0.005} onChange={(value) => change(key, value)} />)}</CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Smoothing</CardTitle><CardDescription>Attack follows rising energy; release controls how slowly it fades.</CardDescription></CardHeader><CardContent className="space-y-2">{([
        ["rms_attack", "Level attack", "Response speed when volume rises."],
        ["rms_release", "Level release", "Decay speed when volume falls."],
        ["band_attack", "Band attack", "Response speed for frequency bands."],
        ["band_release", "Band release", "Decay speed for frequency bands."],
        ["beat_release", "Beat release", "How long beat strength remains visible."],
      ] as const).map(([key, label, description]) => <TuningSlider key={key} label={label} description={description} value={draft[key]} min={0.01} max={1} step={0.005} onChange={(value) => change(key, value)} />)}</CardContent></Card>

      <div className="sticky bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-20 grid grid-cols-[auto_1fr] gap-2 rounded-2xl border border-white/10 bg-background/90 p-2 shadow-2xl backdrop-blur-xl">
        <Button variant="outline" className="h-12 rounded-xl" disabled={saving || !selectedDevice} onClick={() => void reset()}><RotateCcwIcon />Reset</Button>
        <Button className="h-12 rounded-xl" disabled={saving || !dirty || !selectedDevice} onClick={() => void apply()}><SaveIcon />{saving ? "Applying…" : "Apply & save"}</Button>
      </div>
    </div>
  )
}
