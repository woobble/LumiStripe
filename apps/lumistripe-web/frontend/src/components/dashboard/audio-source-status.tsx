import { AudioLinesIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { audioSourceLabel } from "@/components/dashboard/audio-source"
import { useAudioStreamStatus } from "@/hooks/use-audio-stream-status"
import type { AudioSettingsResponse } from "@/lib/api"
import type { WebSocketStreamStatus } from "@/hooks/use-websocket-stream"

export function LiveAudioSourceStatus({ settings }: { settings: AudioSettingsResponse }) {
  const { fresh, connection } = useAudioStreamStatus()
  return (
    <AudioSourceStatus
      configuredSource={settings.source}
      activeSource={settings.active_source}
      monitoring={settings.monitoring}
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
}: {
  configuredSource: string | null | undefined
  activeSource: string | null | undefined
  monitoring: boolean
  connection: WebSocketStreamStatus
  fresh: boolean
  statusMessage?: string
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
        <CardTitle className="flex items-center gap-2 text-base"><AudioLinesIcon className="size-4 text-cyan-300" />Audio source status</CardTitle>
        {statusMessage && <p className="text-sm text-muted-foreground" aria-live="polite">{statusMessage}</p>}
      </CardHeader>
      <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
        <StatusItem label="Configured source" value={audioSourceLabel(configuredSource)} />
        <StatusItem label="Active source" value={monitoring ? audioSourceLabel(activeSource) : "None"} />
        <StatusItem label="Stream connection" value={connectionLabel} />
        <div className="flex items-center justify-between gap-2 rounded-lg bg-white/[0.03] px-3 py-2">
          <span className="text-muted-foreground">Stream freshness</span>
          <Badge variant={hasFreshSamples ? "default" : "outline"}>{hasFreshSamples ? "Fresh samples" : "Waiting for samples"}</Badge>
        </div>
      </CardContent>
    </Card>
  )
}

function StatusItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg bg-white/[0.03] px-3 py-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate font-medium" title={value}>{value}</span>
    </div>
  )
}
