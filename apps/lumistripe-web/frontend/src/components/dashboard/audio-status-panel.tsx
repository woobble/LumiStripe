import { Link } from "react-router"
import { memo } from "react"
import { AudioLinesIcon, Settings2Icon } from "lucide-react"

import { AudioHistoryChart, AudioTelemetryCards, useAudioTelemetry } from "@/components/dashboard/audio-telemetry"

function AudioStatusPanelView() {
  const { telemetry, history } = useAudioTelemetry()

  return (
    <div className="space-y-4 pb-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold"><AudioLinesIcon className="size-5 text-cyan-300" />Audio status</h2>
          <p className="mt-1 text-sm text-muted-foreground">Live microphone levels, frequency bands, beat detection, and the Music gate.</p>
        </div>
        <Link to="/setup/audio" aria-label="Open audio setup" className="inline-flex size-10 shrink-0 items-center justify-center rounded-xl border border-border bg-background text-foreground transition-colors hover:bg-muted">
          <Settings2Icon className="size-4" />
        </Link>
      </div>

      <AudioTelemetryCards telemetry={telemetry} />
      <AudioHistoryChart history={history} />
    </div>
  )
}

export const AudioStatusPanel = memo(AudioStatusPanelView)
