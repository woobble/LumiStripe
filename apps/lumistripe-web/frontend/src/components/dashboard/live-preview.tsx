import { useCallback, useMemo, useState } from "react"
import { ActivityIcon, CircleAlertIcon, EyeOffIcon, RadioIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import type { PreviewFrame } from "@/lib/preview"
import type { DashboardState } from "@/lib/api"
import { previewLabels, previewZoneBoundaries } from "@/components/dashboard/preview-renderer"
import { PreviewCanvas, type PreviewRendererStatus } from "@/components/dashboard/preview-canvas"
import type { PreviewConnectionStatus } from "@/hooks/use-preview-stream"

export function LivePreview({
  state,
  frame,
  connection,
  onDisable,
  compact = false,
}: {
  state: DashboardState
  frame: PreviewFrame | null
  connection: PreviewConnectionStatus
  onDisable?: () => void
  compact?: boolean
}) {
  const [rendererStatus, setRendererStatus] = useState<PreviewRendererStatus>("initializing")
  const [rendererDetail, setRendererDetail] = useState<string | null>(null)
  const labels = useMemo(() => previewLabels(state.stripe_topology), [state.stripe_topology])
  const zoneBoundaries = useMemo(() => previewZoneBoundaries(state.stripe_topology), [state.stripe_topology])
  const handleRendererStatus = useCallback((status: PreviewRendererStatus, detail: string | null) => {
    setRendererStatus(status)
    setRendererDetail(detail)
  }, [])

  const frameStatus = !state.running
    ? "Stopped"
    : connection === "connected" && frame
      ? "Live"
      : connection === "reconnecting" ? "Reconnecting" : "Connecting"
  const rendererLabel = rendererStatus === "webgpu"
    ? "WebGPU"
    : rendererStatus === "canvas" ? "Canvas fallback" : rendererStatus === "unsupported" ? "Unavailable" : "Starting"

  return (
    <Card className={compact ? "gap-2 border-border/70 bg-card p-3 shadow-none" : "border-border/70 bg-card shadow-none"} data-testid="live-preview">
      <CardHeader className={compact ? "px-0" : undefined}>
        <CardTitle className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2">
            <ActivityIcon className="size-4 text-cyan-300" aria-hidden="true" />
            Live preview
          </span>
          <span className="flex items-center gap-2">
            <Badge variant={frameStatus === "Live" ? "default" : "secondary"}>{frameStatus}</Badge>
            {onDisable && (
              <Button
                variant="ghost"
                size="icon-sm"
                className="text-muted-foreground hover:text-foreground"
                onClick={onDisable}
                aria-label="Hide live preview"
                title="Hide live preview"
              >
                <EyeOffIcon aria-hidden="true" />
              </Button>
            )}
          </span>
        </CardTitle>
        {!compact && <CardDescription>Preview the currently applied LED output.</CardDescription>}
      </CardHeader>
      <CardContent className={compact ? "space-y-2 px-0" : "space-y-3"}>
        <div className="relative overflow-hidden rounded-xl border border-white/10 bg-black/40" data-testid="preview-stage">
          <PreviewCanvas
            frame={frame}
            topology={state.stripe_topology}
            onRendererStatus={handleRendererStatus}
            className={compact ? "block aspect-[3.2/1] min-h-28 w-full" : undefined}
          />
          {(labels.length > 1 || zoneBoundaries.length > 0) && (
            <div className="pointer-events-none absolute inset-0" aria-hidden="true">
              {labels.length > 1 && labels.map((label) => (
                <span
                  key={label.id}
                  className="absolute max-w-[45%] truncate rounded bg-black/50 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] text-white/55"
                  style={{ left: label.left, top: label.top }}
                >
                  {label.name}
                </span>
              ))}
              {zoneBoundaries.map((boundary) => (
                <span
                  key={boundary.id}
                  className="absolute -translate-x-1/2 border-l border-dashed border-white/75 shadow-[0_0_3px_rgba(0,0,0,0.9)]"
                  data-testid="zone-boundary"
                  style={{ left: boundary.left, top: boundary.top, height: boundary.height }}
                />
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline"><RadioIcon aria-hidden="true" />{rendererLabel}</Badge>
          {!compact && state.stripe_topology.outputs.map((output) => (
            <Badge key={output.id} variant="outline">{output.name} · {output.pixels}px</Badge>
          ))}
          {!compact && state.stripe_topology.outputs.length === 0 && <span>No outputs configured.</span>}
        </div>

        {!state.running && <p className="text-xs text-muted-foreground" role="status">The controller is stopped. Start it to see live LED output.</p>}

        {rendererStatus === "canvas" && rendererDetail && (
          <p className="text-xs text-muted-foreground" role="status">{rendererDetail}</p>
        )}

        {rendererStatus === "unsupported" && (
          <p className="flex items-start gap-2 rounded-lg border border-amber-300/20 bg-amber-400/10 p-3 text-xs text-amber-100" role="status">
            <CircleAlertIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            This browser cannot render the preview. The lighting controls remain available.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
