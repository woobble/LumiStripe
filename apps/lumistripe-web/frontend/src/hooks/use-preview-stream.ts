import { useEffect, useMemo, useRef, useState } from "react"

import { previewWebsocketUrl } from "@/lib/api"
import { decodePreviewFrame, type PreviewFrame } from "@/lib/preview"
import { useWebSocketStream } from "@/hooks/use-websocket-stream"

export type PreviewConnectionStatus = "connecting" | "connected" | "reconnecting"

export function usePreviewStream(enabled = true) {
  const [pageVisible, setPageVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState === "visible",
  )
  const [latestFrame, setLatestFrame] = useState<{
    session: object
    frame: PreviewFrame
  } | null>(null)
  const lastSequence = useRef(-1)
  const streamEnabled = enabled && pageVisible
  const streamSession = useMemo(() => ({ enabled: streamEnabled }), [streamEnabled])

  useEffect(() => {
    const updateVisibility = () => setPageVisible(document.visibilityState === "visible")
    document.addEventListener("visibilitychange", updateVisibility)
    return () => document.removeEventListener("visibilitychange", updateVisibility)
  }, [])

  const { data: incoming, status } = useWebSocketStream<PreviewFrame>({
    url: previewWebsocketUrl(),
    parse: decodePreviewFrame,
    enabled: streamEnabled,
    binaryType: "arraybuffer",
  })

  useEffect(() => {
    lastSequence.current = -1
  }, [streamSession])

  useEffect(() => {
    if (
      incoming === null
      || status !== "connected"
      || incoming.sequence < lastSequence.current
    ) return
    lastSequence.current = incoming.sequence
    setLatestFrame({ session: streamSession, frame: incoming })
  }, [incoming, status, streamSession])

  return {
    frame: streamEnabled && latestFrame?.session === streamSession
      ? latestFrame.frame
      : null,
    connection: (streamEnabled ? status : "reconnecting") as PreviewConnectionStatus,
  }
}
