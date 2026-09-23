import { useEffect, useState } from "react"

import { ACCESS_REVOKED_EVENT } from "@/lib/api"

export type WebSocketStreamStatus = "connecting" | "connected" | "reconnecting"

type ParseMessage<T> = (data: unknown) => T | null | Promise<T | null>

type WebSocketStreamOptions<T> = {
  url: string
  parse: ParseMessage<T>
  enabled?: boolean
  binaryType?: BinaryType
}

export function useWebSocketStream<T>({
  url,
  parse,
  enabled = true,
  binaryType = "blob",
}: WebSocketStreamOptions<T>) {
  const [data, setData] = useState<T | null>(null)
  const [status, setStatus] = useState<WebSocketStreamStatus>("connecting")

  useEffect(() => {
    if (!enabled) {
      setStatus("reconnecting")
      setData(null)
      return
    }

    let disposed = false
    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined
    let attempts = 0

    const connect = () => {
      if (disposed) return
      setStatus(attempts === 0 ? "connecting" : "reconnecting")
      socket = new WebSocket(url)
      socket.binaryType = binaryType
      socket.onopen = () => {
        attempts = 0
        setStatus("connected")
      }
      socket.onmessage = (event) => {
        try {
          const next = parse(event.data)
          if (next instanceof Promise) {
            next.then((parsed) => {
              if (!disposed && parsed !== null) setData(parsed)
            }).catch(() => socket?.close())
          } else if (!disposed && next !== null) {
            setData(next)
          }
        } catch {
          socket?.close()
        }
      }
      socket.onerror = () => socket?.close()
      socket.onclose = (event) => {
        if (disposed) return
        if (event.code === 4401) {
          window.dispatchEvent(new Event(ACCESS_REVOKED_EVENT))
          return
        }
        attempts += 1
        setStatus("reconnecting")
        reconnectTimer = window.setTimeout(connect, Math.min(1000 * 2 ** (attempts - 1), 10_000))
      }
    }

    connect()
    return () => {
      disposed = true
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [binaryType, enabled, parse, url])

  return { data, status }
}
