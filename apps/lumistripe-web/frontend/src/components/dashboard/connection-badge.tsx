import { WifiIcon, WifiOffIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import type { ConnectionStatus } from "@/hooks/use-dashboard"

const connectionLabels: Record<ConnectionStatus, string> = {
  connected: "Live",
  connecting: "Connecting",
  reconnecting: "Reconnecting",
}

export function ConnectionBadge({ status }: { status: ConnectionStatus }) {
  const connected = status === "connected"
  const Icon = connected ? WifiIcon : WifiOffIcon

  return (
    <Badge
      variant="outline"
      className={connected ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300" : "border-amber-400/20 bg-amber-400/10 text-amber-200"}
    >
      <Icon aria-hidden="true" />
      {connectionLabels[status]}
    </Badge>
  )
}
