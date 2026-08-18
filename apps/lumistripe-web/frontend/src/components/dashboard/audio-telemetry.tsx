import { useEffect, useRef, useState } from "react"
import {
  ActivityIcon,
  AudioLinesIcon,
  GaugeIcon,
  WavesIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { audioWebsocketUrl, type AudioTelemetry } from "@/lib/api"
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import type { TooltipContentProps } from "recharts"

export const bandLabels = ["20-60", "60-120", "120-250", "250-500", "500-1k", "1-2.5k", "2.5-6k", "6-16k"]

export const emptyTelemetry: AudioTelemetry = {
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
  hardware_gain_value: null,
  program_loudness: 0,
  musical_impact: 0,
  gate: "idle",
  gate_preview: true,
  gate_energy: 0,
  gate_onset: 0,
  gate_beat_density: 0,
  gate_brightness: 0,
  gate_spectral_balance: 0,
  gate_reason: "",
  gate_checks: [],
  health: "inactive",
}

export type AudioHistoryPoint = {
  time: number
  input: number
  processed: number
  impact: number
  gate: number
}

export function useAudioTelemetry() {
  const [telemetry, setTelemetry] = useState<AudioTelemetry>(emptyTelemetry)
  const [history, setHistory] = useState<AudioHistoryPoint[]>([])
  const lastSample = useRef(0)

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
        try {
          const next = JSON.parse(String(event.data)) as AudioTelemetry
          setTelemetry(next)
          const now = Date.now()
          if (now - lastSample.current >= 200) {
            lastSample.current = now
            setHistory((current) => [...current.slice(-149), {
              time: now,
              input: next.input_level,
              processed: next.processed_level,
              impact: next.musical_impact,
              gate: next.gate === "music" ? 1 : 0,
            }])
          }
        } catch {
          socket?.close()
        }
      }
      socket.onerror = () => socket?.close()
      socket.onclose = () => {
        if (disposed) return
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

  return { telemetry, history }
}

export function AudioHistoryChart({ history }: { history: AudioHistoryPoint[] }) {
  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader><CardTitle>Signal history</CardTitle><CardDescription>Recent input and musical impact (last 30 seconds).</CardDescription></CardHeader>
      <CardContent className="h-48 px-2">
        {history.length < 2 ? <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Waiting for live samples…</div> : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
              <XAxis dataKey="time" hide />
              <YAxis domain={[0, 1]} hide />
              <Tooltip content={(props) => <SignalHistoryTooltip {...props} />} cursor={{ stroke: "#ffffff", strokeOpacity: 0.28, strokeWidth: 1 }} />
              <Line type="monotone" dataKey="input" stroke="#22d3ee" strokeWidth={2} dot={false} isAnimationActive={false} name="Input" />
              <Line type="monotone" dataKey="processed" stroke="#a78bfa" strokeWidth={2} dot={false} isAnimationActive={false} name="Processed" />
              <Line type="monotone" dataKey="impact" stroke="#f0abfc" strokeWidth={2} dot={false} isAnimationActive={false} name="Impact" />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

const signalColors: Record<string, string> = {
  input: "#22d3ee",
  processed: "#a78bfa",
  impact: "#f0abfc",
}

const signalLabels: Record<string, string> = {
  input: "Input level",
  processed: "Processed",
  impact: "Musical impact",
}

function SignalHistoryTooltip({ active, label, payload }: TooltipContentProps) {
  if (!active || !payload?.length) return null
  const timestamp = Number(label)
  const time = Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })
    : "—"

  return (
    <div className="min-w-40 rounded-xl border border-white/15 bg-[#171522]/95 px-3 py-2.5 text-foreground shadow-xl backdrop-blur-md">
      <div className="mb-2 border-b border-white/10 pb-2 text-[11px] font-medium tracking-wide text-muted-foreground">{time}</div>
      <div className="space-y-1.5">
        {payload.map((entry) => {
          const key = String(entry.dataKey ?? entry.name ?? "")
          const value = typeof entry.value === "number" ? entry.value.toFixed(2) : "—"
          return (
            <div className="flex items-center justify-between gap-5 text-xs" key={key}>
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <span className="size-1.5 rounded-full" style={{ backgroundColor: signalColors[key] ?? "#cbd5e1" }} />
                {signalLabels[key] ?? key}
              </span>
              <span className="font-semibold tabular-nums" style={{ color: signalColors[key] ?? "#f8fafc" }}>{value}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

type GateMetricConfig = {
  label: string
  value: number
  threshold?: number
}

function percent(value: number) {
  return Math.round(Math.min(1, Math.max(0, value)) * 100)
}

function GateMetric({ label, value, threshold }: GateMetricConfig) {
  const width = percent(value)
  const marker = threshold === undefined ? null : percent(threshold)
  return (
    <div>
      <div className="mb-1.5 flex justify-between text-xs">
        <span>{label}</span>
        <span className="text-muted-foreground">
          {value.toFixed(3)}{threshold === undefined ? "" : ` / ${threshold.toFixed(3)}`}
        </span>
      </div>
      <div className="relative h-2 overflow-hidden rounded-full bg-white/5">
        <div className="h-full rounded-full bg-cyan-400 transition-[width] duration-75" style={{ width: `${width}%` }} />
        {marker !== null && <span className="absolute inset-y-0 w-px bg-white" style={{ left: `${marker}%` }} />}
      </div>
    </div>
  )
}

export function AudioTelemetryCards({
  telemetry,
  thresholds,
}: {
  telemetry: AudioTelemetry
  thresholds?: ReadonlyArray<readonly [string, number, number]>
}) {
  const gateMetrics: GateMetricConfig[] = thresholds
    ? thresholds.map(([label, value, threshold]) => ({ label, value, threshold }))
    : [
        { label: "Energy", value: telemetry.gate_energy },
        { label: "Onset", value: telemetry.gate_onset },
        { label: "Beat density", value: telemetry.gate_beat_density },
        { label: "Brightness", value: telemetry.gate_brightness },
      ]

  return (
    <>
      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader className="grid-cols-[1fr_auto]">
          <div>
            <CardTitle className="flex items-center gap-2"><GaugeIcon className="size-4 text-cyan-300" />Live input</CardTitle>
            <CardDescription>Input level, noise floor, and the processed signal sent to Music mode.</CardDescription>
          </div>
          <Badge variant={telemetry.fresh ? "default" : "outline"}>{telemetry.health}</Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <div className="mb-2 flex items-end justify-between"><span className="text-3xl font-semibold tabular-nums">{percent(telemetry.input_level)}%</span><span className="text-xs text-muted-foreground">Processed {percent(telemetry.processed_level)}%</span></div>
            <div className="relative h-4 overflow-hidden rounded-full bg-white/5">
              <div className="h-full rounded-full bg-gradient-to-r from-violet-500 via-cyan-400 to-emerald-300 transition-[width] duration-75" style={{ width: `${percent(telemetry.input_level)}%` }} />
              <span className="absolute inset-y-0 w-0.5 bg-amber-300" title="Estimated floor" style={{ left: `${percent(telemetry.estimated_noise_floor)}%` }} />
              <span className="absolute inset-y-0 w-0.5 bg-white" title="Configured cutoff" style={{ left: `${percent(telemetry.configured_noise_floor)}%` }} />
            </div>
            <div className="mt-2 flex justify-between text-[11px] text-muted-foreground"><span>Noise floor {telemetry.estimated_noise_floor.toFixed(3)}</span><span>Cutoff {telemetry.configured_noise_floor.toFixed(3)}</span></div>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <div className={`rounded-xl p-3 text-center transition-colors ${telemetry.beat ? "bg-fuchsia-400/20 text-fuchsia-100" : "bg-white/[0.035]"}`}><AudioLinesIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{telemetry.beat ? "Beat" : "Waiting"}</p><p className="text-[11px] text-muted-foreground">{percent(telemetry.beat_strength)}% strength</p></div>
            <div className="rounded-xl bg-white/[0.035] p-3 text-center"><WavesIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{telemetry.bpm > 0 ? telemetry.bpm.toFixed(0) : "—"} BPM</p><p className="text-[11px] text-muted-foreground">Detected tempo</p></div>
            <div className="rounded-xl bg-white/[0.035] p-3 text-center"><ActivityIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{telemetry.normalization_gain.toFixed(2)}×</p><p className="text-[11px] text-muted-foreground">Auto gain</p></div>
            <div className="rounded-xl bg-white/[0.035] p-3 text-center"><GaugeIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{telemetry.hardware_gain_value == null ? "—" : `${Math.round(telemetry.hardware_gain_value * 100)}%`}</p><p className="text-[11px] text-muted-foreground">Hardware gain</p></div>
            <div className="rounded-xl bg-white/[0.035] p-3 text-center"><GaugeIcon className="mx-auto mb-1 size-4" /><p className="font-semibold">{percent(telemetry.musical_impact)}%</p><p className="text-[11px] text-muted-foreground">Musical impact</p></div>
          </div>
          <div>
            <div className="mb-1.5 flex justify-between text-xs"><span>Program reference</span><span className="text-muted-foreground">{telemetry.program_loudness.toFixed(3)}</span></div>
            <div className="h-2 overflow-hidden rounded-full bg-white/5"><div className="h-full rounded-full bg-violet-400 transition-[width] duration-150" style={{ width: `${percent(telemetry.program_loudness)}%` }} /></div>
          </div>
        </CardContent>
      </Card>

      <Card className="border-white/5 bg-card/80">
        <CardHeader><CardTitle className="flex items-center gap-2"><AudioLinesIcon className="size-4 text-fuchsia-300" />Frequency bands</CardTitle><CardDescription>Energy by frequency range in Hz.</CardDescription></CardHeader>
        <CardContent><div className="grid h-44 grid-cols-8 items-end gap-1.5">{telemetry.bands.map((value, index) => <div key={bandLabels[index] ?? index} className="flex h-full min-w-0 flex-col justify-end gap-2"><div className="relative flex-1 overflow-hidden rounded-md bg-white/5"><div className="absolute inset-x-0 bottom-0 rounded-md bg-gradient-to-t from-violet-500 to-cyan-300 transition-[height] duration-75" style={{ height: `${Math.max(2, percent(value))}%` }} /></div><span className="truncate text-center text-[8px] text-muted-foreground">{bandLabels[index] ?? `Band ${index + 1}`}</span></div>)}</div></CardContent>
      </Card>

      <Card className="border-white/5 bg-card/80">
        <CardHeader className="grid-cols-[1fr_auto]"><div><CardTitle>Music gate</CardTitle><CardDescription>{telemetry.gate_preview ? "Preview outside Music mode" : "Currently controlling Music mode"}</CardDescription></div><Badge variant={telemetry.gate === "music" ? "default" : "outline"}>{telemetry.gate}</Badge></CardHeader>
        <CardContent className="space-y-3">{gateMetrics.map((metric) => <GateMetric key={metric.label} {...metric} />)}</CardContent>
      </Card>

      <Card className="border-white/5 bg-card/80">
        <CardHeader className="grid-cols-[1fr_auto]"><div><CardTitle>Test Gate</CardTitle><CardDescription>Passive explanation of why Music mode is or is not activating.</CardDescription></div><Badge variant="outline">Diagnostics</Badge></CardHeader>
        <CardContent className="space-y-3"><p className="rounded-xl bg-white/[0.035] p-3 text-sm text-muted-foreground">{telemetry.gate_reason || "Waiting for gate diagnostics…"}</p>{(telemetry.gate_checks ?? []).map((check) => <div key={check.id} className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.025] p-2 text-sm"><span className="flex items-center gap-2">{check.passed ? <span className="size-2 rounded-full bg-emerald-300" /> : <span className="size-2 rounded-full bg-amber-300" />}{check.label}</span><span className="font-mono text-xs text-muted-foreground">{check.value.toFixed(3)} / {check.threshold.toFixed(3)}</span></div>)}</CardContent>
      </Card>
    </>
  )
}
