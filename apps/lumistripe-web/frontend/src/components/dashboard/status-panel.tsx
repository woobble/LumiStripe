import {
  ActivityIcon,
  AudioLinesIcon,
  CheckCircle2Icon,
  CircleAlertIcon,
  Clock3Icon,
  CpuIcon,
  GaugeIcon,
  LogOutIcon,
  MusicIcon,
  PackageIcon,
  RadioIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
  ShieldCheckIcon,
  WrenchIcon,
} from "lucide-react"

import { ConnectionBadge } from "@/components/dashboard/connection-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import type { DashboardController } from "@/hooks/use-dashboard"
import type { DiagnosticIssue } from "@/lib/api"

function displayName(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ")
}

function formatDuration(totalSeconds: number) {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3_600)
  const minutes = Math.floor((seconds % 3_600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds % 60}s`
  return `${seconds}s`
}

function formatAge(age: number | null) {
  if (age === null) return "Not yet"
  if (age < 1) return "Just now"
  return `${formatDuration(age)} ago`
}

function formatFrequency(hertz: number | null) {
  if (hertz === null) return "—"
  const megahertz = hertz / 1_000_000
  return `${Number.isInteger(megahertz) ? megahertz.toFixed(0) : megahertz.toFixed(2)} MHz`
}

function Metric({ icon: Icon, label, value }: { icon: typeof GaugeIcon; label: string; value: string }) {
  return (
    <div className="rounded-xl bg-white/[0.035] p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className="size-3.5" aria-hidden="true" />
        {label}
      </div>
      <p className="truncate font-medium">{value}</p>
    </div>
  )
}

function Issue({ issue }: { issue: DiagnosticIssue }) {
  const critical = issue.severity === "critical"
  const Icon = critical ? CircleAlertIcon : TriangleAlertIcon
  return (
    <div className={critical ? "rounded-xl border border-red-400/20 bg-red-500/10 p-4" : "rounded-xl border border-amber-400/20 bg-amber-500/10 p-4"}>
      <div className="flex gap-3">
        <Icon className={critical ? "mt-0.5 size-5 shrink-0 text-red-300" : "mt-0.5 size-5 shrink-0 text-amber-300"} aria-hidden="true" />
        <div className="min-w-0">
          <p className="font-medium">{issue.title}</p>
          <p className="mt-1 text-sm text-muted-foreground">{issue.message}</p>
          <div className="mt-3 flex gap-2 text-xs">
            <WrenchIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>{issue.action}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export function StatusPanel({ controller, onLogout }: { controller: DashboardController; onLogout?: () => Promise<void> }) {
  const { state, connection } = controller
  if (!state) return null

  const outputValue = state.runtime === "simulation"
    ? "Simulation only"
    : formatAge(state.last_output_age_seconds)

  return (
    <div className="space-y-4 pb-4">
      <div>
        <h2 className="text-lg font-semibold">Diagnostics</h2>
        <p className="text-sm text-muted-foreground">Runtime health and troubleshooting details.</p>
      </div>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {state.diagnostic_issues.length === 0 ? (
              <CheckCircle2Icon className="size-4 text-emerald-300" aria-hidden="true" />
            ) : (
              <TriangleAlertIcon className="size-4 text-amber-300" aria-hidden="true" />
            )}
            System health
          </CardTitle>
          <CardDescription>
            {state.diagnostic_issues.length === 0
              ? "No problems detected."
              : `${state.diagnostic_issues.length} item${state.diagnostic_issues.length === 1 ? "" : "s"} need attention.`}
          </CardDescription>
        </CardHeader>
        {state.diagnostic_issues.length > 0 && (
          <CardContent className="space-y-3">
            {state.diagnostic_issues.map((issue, index) => (
              <Issue key={`${issue.title}-${index}`} issue={issue} />
            ))}
          </CardContent>
        )}
      </Card>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><CpuIcon className="size-4 text-violet-300" aria-hidden="true" />Runtime</CardTitle>
          <CardDescription>Backend, renderer, and output status.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2">
          <Metric icon={Clock3Icon} label="Uptime" value={formatDuration(state.uptime_seconds)} />
          <Metric icon={GaugeIcon} label="Frame rate" value={state.frame_rate > 0 ? `${state.frame_rate.toFixed(1)} FPS` : "Measuring…"} />
          <Metric icon={RefreshCwIcon} label="Last hardware update" value={outputValue} />
          <Metric icon={PackageIcon} label="Version" value={state.application_version} />
          <Metric icon={CpuIcon} label="Runtime" value={displayName(state.runtime)} />
          <Metric icon={ActivityIcon} label="Output backend" value={displayName(state.output_backend)} />
          <Metric icon={GaugeIcon} label="Requested SPI clock" value={formatFrequency(state.spi_speed_hz)} />
          <div className="col-span-2 rounded-xl bg-white/[0.035] p-3">
            <p className="mb-2 text-xs text-muted-foreground">Output devices</p>
            <p className="break-all font-medium">
              {state.output_devices.length > 0 ? state.output_devices.join(", ") : "In-memory controller"}
            </p>
          </div>
          <div className="col-span-2 flex items-center justify-between rounded-xl bg-white/[0.035] p-3">
            <span className="text-xs text-muted-foreground">Live connection</span>
            <ConnectionBadge status={connection} />
          </div>
        </CardContent>
      </Card>

      <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><AudioLinesIcon className="size-4 text-fuchsia-300" aria-hidden="true" />Audio input</CardTitle>
          <CardDescription>{state.audio_status}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Metric icon={RadioIcon} label="Input health" value={displayName(state.audio_health)} />
            <Metric icon={GaugeIcon} label="Tempo" value={state.bpm > 0 ? `${Math.round(state.bpm)} BPM` : "—"} />
            <Metric icon={ActivityIcon} label="Last callback" value={formatAge(state.audio_callback_age_seconds)} />
            <Metric icon={MusicIcon} label="Music gate" value={displayName(state.music_gate)} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={state.music_active ? "default" : "secondary"}>
              {state.music_active ? "Music active" : "Music inactive"}
            </Badge>
            {state.active_effects.map((effect) => (
              <Badge key={effect} variant="outline" className="capitalize">{displayName(effect)}</Badge>
            ))}
            {state.active_effects.length === 0 && <span className="text-xs text-muted-foreground">No active effects</span>}
          </div>
        </CardContent>
      </Card>

      {onLogout && (
        <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><ShieldCheckIcon className="size-4 text-emerald-300" aria-hidden="true" />Access</CardTitle>
            <CardDescription>This phone is paired using a protected session cookie.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" className="h-11 w-full rounded-xl" onClick={() => void onLogout()}>
              <LogOutIcon aria-hidden="true" />
              Forget this device
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
