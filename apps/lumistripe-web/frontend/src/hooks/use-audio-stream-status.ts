import { audioWebsocketUrl, parseAudioTelemetry } from "@/lib/api"
import type { AudioTelemetry } from "@/lib/api/contracts"
import { useWebSocketStream } from "@/hooks/use-websocket-stream"

function parseAudioMessage(value: unknown): AudioTelemetry {
  return parseAudioTelemetry(JSON.parse(String(value)))
}

export function useAudioStreamStatus() {
  const { data, status } = useWebSocketStream<AudioTelemetry>({
    url: audioWebsocketUrl(),
    parse: parseAudioMessage,
  })

  return { connection: status, fresh: data?.fresh ?? false }
}
