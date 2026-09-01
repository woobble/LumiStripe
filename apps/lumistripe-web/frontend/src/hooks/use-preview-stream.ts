import { useEffect, useRef, useState } from "react"

import { ACCESS_REVOKED_EVENT, previewWebsocketUrl } from "@/lib/api"
import { decodePreviewFrame, type PreviewFrame } from "@/lib/preview"

export type PreviewConnectionStatus = "connecting" | "connected" | "reconnecting"

export function usePreviewStream(enabled = true) {
  const [frame, setFrame] = useState<PreviewFrame | null>(null)
  const [connection, setConnection] = useState<PreviewConnectionStatus>("connecting")
  const lastSequence = useRef(-1)

  useEffect(() => {
    lastSequence.current = -1
    if (!enabled) return

    let disposed = false
    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined
    let attempts = 0

    const connect = () => {
      if (disposed) return
      setConnection(attempts === 0 ? "connecting" : "reconnecting")
      socket = new WebSocket(previewWebsocketUrl())
      socket.binaryType = "arraybuffer"

      socket.onopen = () => {
        attempts = 0
        setConnection("connected")
      }
      socket.onmessage = (event) => {
        void decodePreviewFrame(event.data).then((incoming) => {
          if (disposed || incoming === null || incoming.sequence < lastSequence.current) return
          lastSequence.current = incoming.sequence
          setFrame(incoming)
        })
      }
      socket.onerror = () => socket?.close()
      socket.onclose = (event) => {
        if (disposed) return
        if (event.code === 4401) {
          window.dispatchEvent(new Event(ACCESS_REVOKED_EVENT))
          return
        }
        attempts += 1
        setConnection("reconnecting")
        const delay = Math.min(1000 * 2 ** (attempts - 1), 10_000)
        reconnectTimer = window.setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      disposed = true
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [enabled])

  return {
    frame: enabled ? frame : null,
    connection: enabled ? connection : "reconnecting" as const,
  }
}
