import type { ReactNode } from "react"

import { PreviewStreamContext } from "@/hooks/preview-stream-context"
import { usePreviewStream } from "@/hooks/use-preview-stream"

export function PreviewStreamProvider({
  enabled,
  children,
}: {
  enabled: boolean
  children: ReactNode
}) {
  const stream = usePreviewStream(enabled)
  return (
    <PreviewStreamContext.Provider value={stream}>
      {children}
    </PreviewStreamContext.Provider>
  )
}
