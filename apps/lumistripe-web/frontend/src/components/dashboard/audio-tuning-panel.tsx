import { memo, useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Link } from "react-router"
import {
  MicIcon,
  RotateCcwIcon,
  SaveIcon,
  SlidersHorizontalIcon,
} from "lucide-react"
import { toast } from "sonner"

import { AudioSetupPage } from "@/components/dashboard/setup-nav"
import { AudioCalibrationWizard } from "@/components/dashboard/audio-calibration-wizard"
import { AudioHistoryChart, AudioTelemetryCards, useAudioTelemetry } from "@/components/dashboard/audio-telemetry"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Slider } from "@/components/ui/slider"
import {
  dashboardApi,
  type AudioSettingsResponse,
  type AudioTuningValues,
} from "@/lib/api"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"

type SliderKey = Exclude<keyof AudioTuningValues, "noise_floor">

const audioTuningSchema = z.object({
  target_level: z.number().min(0.1).max(0.8),
  hardware_gain_target: z.number().min(0).max(1).nullable().optional(),
  dynamic_response: z.number().min(0).max(1),
  rms_attack: z.number().min(0.01).max(1), rms_release: z.number().min(0.01).max(1),
  band_attack: z.number().min(0.01).max(1), band_release: z.number().min(0.01).max(1), beat_release: z.number().min(0.01).max(1),
  energy_threshold: z.number().min(0).max(1), onset_threshold: z.number().min(0).max(1), beat_density_threshold: z.number().min(0).max(1), brightness_threshold: z.number().min(0).max(1), spectral_balance_ratio: z.number().min(0).max(1),
})
type AudioTuningFormValues = z.infer<typeof audioTuningSchema>

function firstValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] : value
}

function TuningSlider({
  label,
  description,
  value,
  min,
  max,
  step,
  onChange,
  error,
}: {
  label: string
  description: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
  error?: string
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
      {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
    </div>
  )
}

function AudioTuningPanelView() {
  const [response, setResponse] = useState<AudioSettingsResponse | null>(null)
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const form = useForm<AudioTuningFormValues>({ resolver: zodResolver(audioTuningSchema), defaultValues: {
    target_level: 0.36, dynamic_response: 0.65, rms_attack: 0.45, rms_release: 0.12, band_attack: 0.4, band_release: 0.1, beat_release: 0.18,
    hardware_gain_target: null,
    energy_threshold: 0.03, onset_threshold: 0.025, beat_density_threshold: 0.05, brightness_threshold: 0.08, spectral_balance_ratio: 0.35,
  } })
  const { watch, reset: formReset, setValue, handleSubmit, formState: { isDirty, errors } } = form
  const draft = watch()
  useUnsavedChangesGuard(isDirty)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await dashboardApi.getAudioSettings()
      setResponse(next)
      const selector = next.active_device
      setSelectedDevice(selector)
      formReset(next.settings)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load audio settings.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  const { telemetry, history } = useAudioTelemetry()

  const change = (key: SliderKey, value: number) => setValue(key, value, { shouldDirty: true, shouldValidate: true })

  const apply = async (values: AudioTuningFormValues) => {
    if (!selectedDevice || saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.updateAudioSettings(selectedDevice, values)
      setResponse(next)
      formReset(next.settings)
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
      formReset(next.settings)
      toast.success("Default audio profile restored.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Defaults could not be restored.")
    } finally {
      setSaving(false)
    }
  }

  if (loading && !response) {
    return <AudioSetupPage><div className="space-y-4"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-64 rounded-xl" /><Skeleton className="h-72 rounded-xl" /></div></AudioSetupPage>
  }
  if (!response) {
    return <AudioSetupPage><Card><CardHeader><CardTitle>Audio tuning unavailable</CardTitle><CardDescription>Could not load microphone settings.</CardDescription></CardHeader><CardContent><Button onClick={() => void load()}>Try again</Button></CardContent></Card></AudioSetupPage>
  }
  const activeDeviceName = response.active_device_name
    ?? response.devices.find((item) => item.selector === selectedDevice)?.name

  const thresholds = [
    ["Energy", telemetry.gate_energy, draft.energy_threshold],
    ["Onset", telemetry.gate_onset, draft.onset_threshold],
    ["Beat density", telemetry.gate_beat_density, draft.beat_density_threshold],
    ["Brightness", telemetry.gate_brightness, draft.brightness_threshold],
  ] as const

  return (
    <AudioSetupPage>
      <div className="space-y-4 pb-4">
      <div>
        <h2 className="text-lg font-semibold">Audio tuning</h2>
        <p className="text-sm text-muted-foreground">See what the active audio input hears and tune Music mode in real time.</p>
      </div>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader className="grid-cols-[1fr_auto]">
          <div><CardTitle className="flex items-center gap-2"><MicIcon className="size-4 text-violet-300" />{activeDeviceName ?? "No input selected"}</CardTitle><CardDescription>{response.monitoring ? (response.active_source === "bluetooth" ? "Active Bluetooth music stream" : "Active microphone") : `Audio source: ${response.source}`}</CardDescription></div>
          <Link to="/setup/audio" className="inline-flex h-9 items-center justify-center rounded-lg border border-border bg-background px-3 text-sm font-medium hover:bg-muted">Change</Link>
        </CardHeader>
        {response.error && <CardContent><p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-200">{response.error}</p></CardContent>}
      </Card>

      <AudioTelemetryCards telemetry={telemetry} thresholds={thresholds} />
      <AudioHistoryChart history={history} />
      <AudioCalibrationWizard device={selectedDevice} onApplied={() => void load()} />

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle className="flex items-center gap-2"><SlidersHorizontalIcon className="size-4 text-violet-300" />Normalization</CardTitle><CardDescription>The target used by automatic software gain.</CardDescription></CardHeader><CardContent><TuningSlider label="Target level" description="Higher values make quiet microphones more prominent." value={draft.target_level} min={0.1} max={0.8} step={0.005} onChange={(value) => change("target_level", value)} />{errors.target_level && <p role="alert" className="mt-2 text-xs text-red-300">{errors.target_level.message}</p>}</CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Hardware gain</CardTitle><CardDescription>{response.hardware_gain_supported ? `ALSA ${response.hardware_gain_backend ?? "mixer"} · ${response.hardware_gain_control ?? "capture control"}` : "This input does not expose a writable hardware gain control."}</CardDescription></CardHeader><CardContent className="space-y-3">{response.hardware_gain_supported && response.hardware_gain_writable ? <TuningSlider label="Hardware trim" description="Applied when you save; software normalization handles live changes." value={draft.hardware_gain_target ?? response.hardware_gain_value ?? 0.5} min={0} max={1} step={0.01} onChange={(value) => change("hardware_gain_target", value)} /> : <p className="text-sm text-muted-foreground">Software-only normalization remains active.{response.hardware_gain_error ? ` ${response.hardware_gain_error}` : ""}</p>}</CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Musical response</CardTitle><CardDescription>Choose how much contrast the lights keep between quiet passages and loud highlights.</CardDescription></CardHeader><CardContent><TuningSlider label="Calm ↔ Dramatic" description="Higher values make quiet parts gentler and reserve stronger effects for peaks." value={draft.dynamic_response} min={0} max={1} step={0.01} onChange={(value) => change("dynamic_response", value)} error={errors.dynamic_response?.message} /></CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Activation thresholds</CardTitle><CardDescription>Lower values make Music mode easier to activate.</CardDescription></CardHeader><CardContent className="space-y-2">{([
        ["energy_threshold", "Energy", "Minimum overall signal energy."],
        ["onset_threshold", "Onset", "Minimum transient strength for rhythmic input."],
        ["beat_density_threshold", "Beat density", "Required density of detected beats."],
        ["brightness_threshold", "Brightness", "Required treble energy for broadband music."],
        ["spectral_balance_ratio", "Spectral balance", "How balanced bass and treble must be."],
      ] as const).map(([key, label, description]) => <TuningSlider key={key} label={label} description={description} value={draft[key]} min={0} max={1} step={0.005} onChange={(value) => change(key, value)} error={errors[key]?.message} />)}</CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Smoothing</CardTitle><CardDescription>Attack follows rising energy; release controls how slowly it fades.</CardDescription></CardHeader><CardContent className="space-y-2">{([
        ["rms_attack", "Level attack", "Response speed when volume rises."],
        ["rms_release", "Level release", "Decay speed when volume falls."],
        ["band_attack", "Band attack", "Response speed for frequency bands."],
        ["band_release", "Band release", "Decay speed for frequency bands."],
        ["beat_release", "Beat release", "How long beat strength remains visible."],
      ] as const).map(([key, label, description]) => <TuningSlider key={key} label={label} description={description} value={draft[key]} min={0.01} max={1} step={0.005} onChange={(value) => change(key, value)} error={errors[key]?.message} />)}</CardContent></Card>

      <div className="sticky bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-20 grid grid-cols-[auto_1fr] gap-2 rounded-2xl border border-white/10 bg-background/90 p-2 shadow-2xl backdrop-blur-xl">
        <Button variant="outline" className="h-12 rounded-xl" disabled={saving || !selectedDevice} onClick={() => void reset()}><RotateCcwIcon />Reset</Button>
        <Button className="h-12 rounded-xl" disabled={saving || !isDirty || !selectedDevice} onClick={() => void handleSubmit(apply)()}><SaveIcon />{saving ? "Applying…" : "Apply & save"}</Button>
      </div>
      </div>
    </AudioSetupPage>
  )
}

export const AudioTuningPanel = memo(AudioTuningPanelView)
