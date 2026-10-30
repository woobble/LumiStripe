import { AudioLinesIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { audioSourceLabel } from "@/components/dashboard/audio-source"
import { useAudioStreamStatus } from "@/hooks/use-audio-stream-status"
import type { AudioSettingsResponse } from "@/lib/api"
import type { WebSocketStreamStatus } from "@/hooks/use-websocket-stream"

export function LiveAudioSourceStatus({
  settings,
  configuredAudioSource,
  activeAudioSource,
  audioMonitoring,
}: {
  settings: AudioSettingsResponse
  configuredAudioSource?: string | null
  activeAudioSource?: string | null
  audioMonitoring?: boolean | null
}) {
  const { fresh, connection } = useAudioStreamStatus()
  return (
    <AudioSourceStatus
      configuredSource={configuredAudioSource ?? settings.source}
      activeSource={activeAudioSource ?? settings.active_source}
      monitoring={audioMonitoring ?? settings.monitoring}
      connection={connection}
      fresh={fresh}
    />
  )
}

export function AudioSourceStatus({
  configuredSource,
  activeSource,
  monitoring,
  connection,
  fresh,
  statusMessage,
  compact = false,
}: {
  configuredSource: string | null | undefined
  activeSource: string | null | undefined
  monitoring: boolean
  connection: WebSocketStreamStatus
  fresh: boolean
  statusMessage?: string
  compact?: boolean
}) {
  const connectionLabel = connection === "connected"
    ? "Audio stream connected"
    : connection === "connecting"
      ? "Connecting to audio stream"
      : "Audio stream reconnecting"
  const hasFreshSamples = connection === "connected" && fresh

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader className="pb-3">
        <CardTitle className="flex min-w-0 items-center gap-2 text-base"><AudioLinesIcon className="size-4 shrink-0 text-cyan-300" />{compact ? "Input source" : "Audio source status"}</CardTitle>
        {statusMessage && <p className={`break-words text-muted-foreground [overflow-wrap:anywhere] ${compact ? "text-xs" : "text-sm"}`} aria-live="polite">{statusMessage}</p>}
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <StatusItem label="Configured source" value={audioSourceLabel(configuredSource)} />
        <StatusItem label="Active source" value={activeSource && activeSource !== "off" ? audioSourceLabel(activeSource) : "None"} />
        <StatusItem label="Audio monitor" value={monitoring ? "Running" : "Inactive"} />
        {!compact && <StatusItem label="Stream connection" value={connectionLabel} />}
        {!compact && <div className="flex items-center justify-between gap-2 rounded-lg bg-white/[0.03] px-3 py-2">
          <span className="text-muted-foreground">Stream freshness</span>
          <Badge variant={hasFreshSamples ? "default" : "outline"}>{hasFreshSamples ? "Fresh samples" : "Waiting for samples"}</Badge>
        </div>}
      </CardContent>
    </Card>
  )
}

function StatusItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg bg-white/[0.03] px-3 py-2">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-words text-right font-medium [overflow-wrap:anywhere]">{value}</span>
    </div>
  )
}
