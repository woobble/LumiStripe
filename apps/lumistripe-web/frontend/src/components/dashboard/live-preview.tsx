import { useEffect, useMemo, useRef, useState } from "react"
import { ActivityIcon, CircleAlertIcon, EyeOffIcon, RadioIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { usePreviewStream } from "@/hooks/use-preview-stream"
import { emptyPreviewFrame } from "@/lib/preview"
import type { DashboardState } from "@/lib/api"
import {
  CanvasPreviewRenderer,
  previewLabels,
  WebGpuPreviewRenderer,
  webGpuFailureMessage,
  type PreviewRenderer,
} from "@/components/dashboard/preview-renderer"

type RendererStatus = "initializing" | "webgpu" | "canvas" | "unsupported"

export function LivePreview({ state, onDisable }: { state: DashboardState; onDisable?: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rendererRef = useRef<PreviewRenderer | null>(null)
  const [rendererStatus, setRendererStatus] = useState<RendererStatus>("initializing")
  const [rendererDetail, setRendererDetail] = useState<string | null>(null)
  const { frame, connection } = usePreviewStream(state.running)
  const labels = useMemo(() => previewLabels(state.stripe_topology), [state.stripe_topology])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let disposed = false

    const initialize = async () => {
      const webgpu = new WebGpuPreviewRenderer(canvas)
      if (await webgpu.initialize()) {
        if (disposed) {
          webgpu.dispose()
          return
        }
        rendererRef.current = webgpu
        setRendererStatus("webgpu")
        setRendererDetail(null)
        return
      }

      const webgpuDetail = webGpuFailureMessage(webgpu.failureReason)
      const canvasRenderer = new CanvasPreviewRenderer(canvas)
      if (!canvasRenderer.initialize()) {
        if (disposed) return
        setRendererStatus("unsupported")
        setRendererDetail(null)
        return
      }
      if (disposed) {
        canvasRenderer.dispose()
        return
      }
      rendererRef.current = canvasRenderer
      setRendererStatus("canvas")
      setRendererDetail(webgpuDetail)
    }

    void initialize()
    return () => {
      disposed = true
      rendererRef.current?.dispose()
      rendererRef.current = null
    }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const resize = () => rendererRef.current?.resize()
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    window.addEventListener("resize", resize)
    resize()
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", resize)
    }
  }, [])

  useEffect(() => {
    const activeFrame = frame ?? emptyPreviewFrame(state.stripe_topology)
    rendererRef.current?.render(activeFrame, state.stripe_topology)
  }, [frame, rendererStatus, state.stripe_topology])

  const frameStatus = connection === "connected" && frame
    ? "Live"
    : connection === "reconnecting" ? "Reconnecting" : "Connecting"
  const rendererLabel = rendererStatus === "webgpu"
    ? "WebGPU"
    : rendererStatus === "canvas" ? "Canvas fallback" : rendererStatus === "unsupported" ? "Unavailable" : "Starting"

  return (
    <Card className="border-white/5 bg-card/80 shadow-xl shadow-black/10 backdrop-blur-xl" data-testid="live-preview">
      <CardHeader>
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
        <CardDescription>See the latest output frame while you adjust the lights.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="relative overflow-hidden rounded-xl border border-white/10 bg-black/40" data-testid="preview-stage">
          <canvas ref={canvasRef} className="block aspect-[2.2/1] h-auto w-full" aria-label="LED output preview" />
          {labels.length > 1 && (
            <div className="pointer-events-none absolute inset-0" aria-hidden="true">
              {labels.map((label) => (
                <span
                  key={label.id}
                  className="absolute max-w-[45%] truncate rounded bg-black/50 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] text-white/55"
                  style={{ left: label.left, top: label.top }}
                >
                  {label.name}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline"><RadioIcon aria-hidden="true" />{rendererLabel}</Badge>
          {state.stripe_topology.outputs.map((output) => (
            <Badge key={output.id} variant="outline">{output.name} · {output.pixels}px</Badge>
          ))}
          {state.stripe_topology.outputs.length === 0 && <span>No outputs configured.</span>}
        </div>

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
