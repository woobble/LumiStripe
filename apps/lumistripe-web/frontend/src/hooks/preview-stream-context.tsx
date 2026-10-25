import { createContext, useContext } from "react"

import type { PreviewConnectionStatus } from "@/hooks/use-preview-stream"
import type { PreviewFrame } from "@/lib/preview"

export type PreviewStreamValue = {
  frame: PreviewFrame | null
  connection: PreviewConnectionStatus
}

export const PreviewStreamContext = createContext<PreviewStreamValue | null>(null)

export function usePreviewStreamContext() {
  const stream = useContext(PreviewStreamContext)
  if (stream === null) throw new Error("Preview stream consumer is missing its provider")
  return stream
}
