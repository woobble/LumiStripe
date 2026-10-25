import { memo, useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Link } from "react-router"
import {
  ChevronDownIcon,
  MicIcon,
  RotateCcwIcon,
  SaveIcon,
  SlidersHorizontalIcon,
} from "lucide-react"
import { toast } from "sonner"

import { AudioCalibrationWizard } from "@/components/dashboard/audio-calibration-wizard"
import { AudioHistoryChart, AudioTelemetryCards, useAudioTelemetry } from "@/components/dashboard/audio-telemetry"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Slider } from "@/components/ui/slider"
import {
  dashboardApi,
} from "@/lib/api"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"
import { useAudioSettings } from "@/hooks/use-audio-settings"

type SliderKey = keyof AudioTuningFormValues

const audioTuningSchema = z.object({
  noise_floor: z.number().min(0).max(1),
  input_noise_floor: z.number().min(0).max(0.5),
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
  disabled = false,
}: {
  label: string
  description: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
  error?: string
  disabled?: boolean
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
        disabled={disabled}
        className="py-2 [&_[data-slot=slider-track]]:h-2 [&_[data-slot=slider-thumb]]:size-5"
      />
      {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
    </div>
  )
}

function AudioTuningPanelView() {
  const { settings: response, loading, loadError, refresh, applySettings } = useAudioSettings()
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [calibrating, setCalibrating] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const dirtyRef = useRef(false)
  const form = useForm<AudioTuningFormValues>({ resolver: zodResolver(audioTuningSchema), defaultValues: {
    noise_floor: 0.015, input_noise_floor: 0.003,
    target_level: 0.36, dynamic_response: 0.65, rms_attack: 0.45, rms_release: 0.12, band_attack: 0.4, band_release: 0.1, beat_release: 0.18,
    hardware_gain_target: null,
    energy_threshold: 0.03, onset_threshold: 0.025, beat_density_threshold: 0.05, brightness_threshold: 0.08, spectral_balance_ratio: 0.35,
  } })
  const { watch, reset: formReset, setValue, handleSubmit, formState: { isDirty, errors } } = form
  const draft = watch()
  useUnsavedChangesGuard(isDirty)
  useEffect(() => { dirtyRef.current = isDirty }, [isDirty])

  useEffect(() => {
    if (!response || dirtyRef.current) return
    setSelectedDevice(response.active_device ?? null)
    formReset(response.settings)
  }, [formReset, response])
  const { telemetry, history, connection } = useAudioTelemetry()

  const change = (key: SliderKey, value: number) => setValue(key, value, { shouldDirty: true, shouldValidate: true })

  const apply = async (values: AudioTuningFormValues) => {
    if (!selectedDevice || saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.updateAudioSettings(selectedDevice, values)
      applySettings(next)
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
      applySettings(next)
      formReset(next.settings)
      toast.success("Default audio profile restored.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Defaults could not be restored.")
    } finally {
      setSaving(false)
    }
  }

  if (loading && !response) {
    return <div className="space-y-4"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-64 rounded-xl" /><Skeleton className="h-72 rounded-xl" /></div>
  }
  if (!response) {
    return <Card><CardHeader><CardTitle>Audio tuning unavailable</CardTitle><CardDescription>{loadError ?? "Could not load microphone settings."}</CardDescription></CardHeader><CardContent><Button disabled={loading} onClick={() => { void refresh().catch((error: unknown) => toast.error(error instanceof Error ? error.message : "Could not load audio settings.")) }}>Try again</Button></CardContent></Card>
  }
  const activeDeviceName = response.devices.find((item) => item.selector === selectedDevice)?.name
    ?? response.active_device_name

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
        <p className="text-sm text-muted-foreground">See what the active audio input hears and tune Music mode in real time.</p>
      </div>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader className="grid-cols-[1fr_auto]">
          <div><CardTitle className="flex items-center gap-2"><MicIcon className="size-4 text-violet-300" />{activeDeviceName ?? "No input selected"}</CardTitle><CardDescription>{response.monitoring ? (response.active_source === "bluetooth" ? "Active Bluetooth music stream" : "Active microphone") : `Audio source: ${response.source}`}</CardDescription></div>
          <Link to="/setup/audio" className="inline-flex h-9 items-center justify-center rounded-lg border border-border bg-background px-3 text-sm font-medium hover:bg-muted">Change</Link>
        </CardHeader>
        {response.error && <CardContent><p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-200">{response.error}</p></CardContent>}
      </Card>

      <Card className="border-white/5 bg-card/80">
        <CardHeader className="pb-3"><CardTitle className="flex items-center justify-between gap-3 text-base"><span>Live input level</span><span role="status" aria-live="polite" className="text-xs font-medium text-muted-foreground">{telemetry.fresh ? "Live" : connection === "connecting" ? "Connecting" : connection === "reconnecting" ? "Reconnecting" : "Stale"}</span></CardTitle><CardDescription>Compact view of the active input signal.</CardDescription></CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-end justify-between gap-3"><span className="text-3xl font-semibold tabular-nums">{telemetry.fresh ? `${Math.round(Math.max(0, Math.min(1, telemetry.input_level)) * 100)}%` : "—"}</span><span className="text-xs text-muted-foreground">Processed {telemetry.fresh ? `${Math.round(Math.max(0, Math.min(1, telemetry.processed_level)) * 100)}%` : "—"}</span></div>
          <div className="h-3 overflow-hidden rounded-full bg-white/5" role="img" aria-label={`Live input level ${telemetry.fresh ? Math.round(Math.max(0, Math.min(1, telemetry.input_level)) * 100) : 0} percent`}>
            <div className="h-full rounded-full bg-gradient-to-r from-violet-500 via-cyan-400 to-emerald-300 transition-[width] duration-100" style={{ width: `${telemetry.fresh ? Math.max(0, Math.min(1, telemetry.input_level)) * 100 : 0}%` }} />
          </div>
          {telemetry.fresh && telemetry.input_peak < 0.001 && <p className="text-xs text-muted-foreground">Very little signal. Check the selected input and capture gain.</p>}
          {telemetry.fresh && telemetry.clipping_fraction > 0 && <p role="alert" className="text-xs text-amber-300">Input is clipping. Lower capture gain before calibrating.</p>}
        </CardContent>
      </Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle className="flex items-center gap-2"><SlidersHorizontalIcon className="size-4 text-violet-300" />Basic tuning</CardTitle><CardDescription>Adjust the overall response without changing advanced signal processing.</CardDescription></CardHeader><CardContent className="space-y-3">
        <TuningSlider label="Target level" description="Higher values make quiet microphones more prominent." value={draft.target_level} min={0.1} max={0.8} step={0.005} onChange={(value) => change("target_level", value)} error={errors.target_level?.message} disabled={saving} />
        {response.hardware_gain_supported && response.hardware_gain_writable && <TuningSlider label="Hardware gain" description={`${response.hardware_gain_backend ?? "Hardware"} capture trim. Applied when saved.`} value={draft.hardware_gain_target ?? response.hardware_gain_value ?? 0.5} min={0} max={1} step={0.01} onChange={(value) => change("hardware_gain_target", value)} disabled={saving} />}
        {!response.hardware_gain_supported && <p className="rounded-lg bg-white/[0.035] p-3 text-sm text-muted-foreground">Hardware gain is unavailable for this input. Software normalization remains active.{response.hardware_gain_error ? ` ${response.hardware_gain_error}` : ""}</p>}
        {response.hardware_gain_supported && !response.hardware_gain_writable && <p className="rounded-lg bg-white/[0.035] p-3 text-sm text-muted-foreground">The input reports hardware gain, but it cannot be changed here. Software normalization remains active.{response.hardware_gain_error ? ` ${response.hardware_gain_error}` : ""}</p>}
        <div><TuningSlider label="Musical response" description="Calm keeps quiet passages gentle; Dramatic gives loud highlights more contrast." value={draft.dynamic_response} min={0} max={1} step={0.01} onChange={(value) => change("dynamic_response", value)} error={errors.dynamic_response?.message} disabled={saving} /><div className="flex justify-between px-1 text-[11px] text-muted-foreground"><span>Calm</span><span>Balanced</span><span>Dramatic</span></div></div>
      </CardContent></Card>

      {loadError && <p role="alert" className="rounded-xl bg-amber-500/10 p-3 text-sm text-amber-100">Using the last loaded audio settings: {loadError}</p>}
      <AudioCalibrationWizard device={selectedDevice} microphone={response.active_source === "mic"} onBusyChange={setCalibrating} onApplied={() => { void refresh().catch(() => undefined) }} />

      <Button type="button" variant="outline" className="h-11 w-full justify-between" aria-expanded={advancedOpen} aria-controls="advanced-audio-tuning" onClick={() => setAdvancedOpen((open) => !open)}>
        <span className="flex items-center gap-2"><SlidersHorizontalIcon className="size-4" />Advanced DSP settings and diagnostics</span><ChevronDownIcon className={`size-4 transition-transform ${advancedOpen ? "rotate-180" : ""}`} />
      </Button>
      {advancedOpen && <section id="advanced-audio-tuning" aria-label="Advanced DSP settings" className="space-y-4">
        <AudioTelemetryCards telemetry={telemetry} thresholds={thresholds} />
        <AudioHistoryChart history={history} />

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Noise thresholds</CardTitle><CardDescription>Set the signal floor used to reject background noise.</CardDescription></CardHeader><CardContent className="space-y-2">{([
        ["noise_floor", "Music noise floor", "Minimum normalized signal level for Music mode."],
        ["input_noise_floor", "Microphone noise floor", "Minimum microphone signal for capture."],
      ] as const).map(([key, label, description]) => <TuningSlider key={key} label={label} description={description} value={draft[key]} min={0} max={key === "input_noise_floor" ? 0.5 : 1} step={0.005} onChange={(value) => change(key, value)} error={errors[key]?.message} disabled={saving} />)}</CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Energy and onset thresholds</CardTitle><CardDescription>Control how readily changing audio activates Music mode.</CardDescription></CardHeader><CardContent className="space-y-2">{([
        ["energy_threshold", "Energy", "Minimum overall signal energy."],
        ["onset_threshold", "Onset", "Minimum transient strength for rhythmic input."],
        ["beat_density_threshold", "Beat density", "Required density of detected beats."],
        ["brightness_threshold", "Brightness", "Required treble energy for broadband music."],
      ] as const).map(([key, label, description]) => <TuningSlider key={key} label={label} description={description} value={draft[key]} min={0} max={1} step={0.005} onChange={(value) => change(key, value)} error={errors[key]?.message} disabled={saving} />)}</CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Spectral balance</CardTitle><CardDescription>Adjust how bass and treble energy contribute to activation.</CardDescription></CardHeader><CardContent className="space-y-2">{([
        ["spectral_balance_ratio", "Spectral balance", "How balanced bass and treble must be."],
      ] as const).map(([key, label, description]) => <TuningSlider key={key} label={label} description={description} value={draft[key]} min={0} max={1} step={0.005} onChange={(value) => change(key, value)} error={errors[key]?.message} disabled={saving} />)}</CardContent></Card>

      <Card className="border-white/5 bg-card/80"><CardHeader><CardTitle>Smoothing</CardTitle><CardDescription>Attack follows rising energy; release controls how slowly it fades.</CardDescription></CardHeader><CardContent className="space-y-2">{([
        ["rms_attack", "Level attack", "Response speed when volume rises."],
        ["rms_release", "Level release", "Decay speed when volume falls."],
        ["band_attack", "Band attack", "Response speed for frequency bands."],
        ["band_release", "Band release", "Decay speed for frequency bands."],
        ["beat_release", "Beat release", "How long beat strength remains visible."],
      ] as const).map(([key, label, description]) => <TuningSlider key={key} label={label} description={description} value={draft[key]} min={0.01} max={1} step={0.005} onChange={(value) => change(key, value)} error={errors[key]?.message} disabled={saving} />)}</CardContent></Card>
      </section>}

      <div className="sticky bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-20 grid grid-cols-[auto_1fr] gap-2 rounded-2xl border border-white/10 bg-background/90 p-2 shadow-2xl backdrop-blur-xl">
        <Button variant="outline" className="h-12 rounded-xl" disabled={saving || calibrating || !selectedDevice} onClick={() => void reset()}><RotateCcwIcon />Reset</Button>
        <Button className="h-12 rounded-xl" disabled={saving || calibrating || !isDirty || !selectedDevice} onClick={() => void handleSubmit(apply)()}><SaveIcon />{saving ? "Applying…" : "Apply & save"}</Button>
      </div>
      {calibrating && <p role="status" className="text-center text-xs text-muted-foreground">Finish or cancel calibration before applying or resetting the audio profile.</p>}
    </div>
  )
}

export const AudioTuningPanel = memo(AudioTuningPanelView)
