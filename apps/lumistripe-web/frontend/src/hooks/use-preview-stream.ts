import { useEffect, useRef, useState } from "react"

import { previewWebsocketUrl } from "@/lib/api"
import { decodePreviewFrame, type PreviewFrame } from "@/lib/preview"
import { useWebSocketStream } from "@/hooks/use-websocket-stream"

export type PreviewConnectionStatus = "connecting" | "connected" | "reconnecting"

export function usePreviewStream(enabled = true) {
  const [frame, setFrame] = useState<PreviewFrame | null>(null)
  const lastSequence = useRef(-1)

  const { data: incoming, status } = useWebSocketStream<PreviewFrame>({
    url: previewWebsocketUrl(),
    parse: decodePreviewFrame,
    enabled,
    binaryType: "arraybuffer",
  })

  useEffect(() => {
    lastSequence.current = -1
    setFrame(null)
  }, [enabled])

  useEffect(() => {
    if (incoming === null || incoming.sequence < lastSequence.current) return
    lastSequence.current = incoming.sequence
    setFrame(incoming)
  }, [incoming])

  return {
    frame: enabled ? frame : null,
    connection: (enabled ? status : "reconnecting") as PreviewConnectionStatus,
  }
}
