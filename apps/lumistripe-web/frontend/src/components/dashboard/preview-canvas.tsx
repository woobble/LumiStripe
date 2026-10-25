import { useEffect, useRef } from "react"

import {
  CanvasPreviewRenderer,
  WebGpuPreviewRenderer,
  webGpuFailureMessage,
  type PreviewRenderer,
  type WebGpuFailureReason,
} from "@/components/dashboard/preview-renderer"
import type { StripeTopology } from "@/lib/api"
import { emptyPreviewFrame, type PreviewFrame } from "@/lib/preview"

export type PreviewRendererStatus = "initializing" | "webgpu" | "canvas" | "unsupported"

export function PreviewCanvas({
  frame,
  topology,
  onRendererStatus,
  className = "block aspect-[2.2/1] h-auto w-full",
}: {
  frame: PreviewFrame | null
  topology: StripeTopology
  onRendererStatus?: (status: PreviewRendererStatus, detail: string | null) => void
  className?: string
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rendererRef = useRef<PreviewRenderer | null>(null)

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
        onRendererStatus?.("webgpu", null)
        return
      }

      const webgpuDetail = webGpuFailureMessage(webgpu.failureReason as WebGpuFailureReason | null)
      const canvasRenderer = new CanvasPreviewRenderer(canvas)
      if (!canvasRenderer.initialize()) {
        if (disposed) return
        onRendererStatus?.("unsupported", null)
        return
      }
      if (disposed) {
        canvasRenderer.dispose()
        return
      }
      rendererRef.current = canvasRenderer
      onRendererStatus?.("canvas", webgpuDetail)
    }

    void initialize()
    return () => {
      disposed = true
      rendererRef.current?.dispose()
      rendererRef.current = null
    }
  }, [onRendererStatus])

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
    rendererRef.current?.render(frame ?? emptyPreviewFrame(topology), topology)
  }, [frame, topology])

  return <canvas ref={canvasRef} className={className} aria-label="LED output preview" />
}
