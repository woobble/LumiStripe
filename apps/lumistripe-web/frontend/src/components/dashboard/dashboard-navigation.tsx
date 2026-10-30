import { ActivityIcon, AudioLinesIcon, LightbulbIcon, Settings2Icon } from "lucide-react"
import { useLocation } from "react-router"

import { GuardedNavLink } from "@/hooks/use-unsaved-changes"
import { cn } from "@/lib/utils"

const items = [
  { to: "/", label: "Lights", icon: LightbulbIcon, end: true },
  { to: "/audio", label: "Audio", icon: AudioLinesIcon },
  { to: "/setup", label: "Devices", icon: Settings2Icon },
  { to: "/diagnostics", label: "System", icon: ActivityIcon },
]

export function DashboardNavigation() {
  const location = useLocation()
  return (
    <aside className="fixed inset-x-0 bottom-0 z-40 border-t border-border/80 bg-background/95 pb-[max(0.5rem,env(safe-area-inset-bottom))] backdrop-blur-xl lg:inset-y-0 lg:right-auto lg:w-64 lg:border-r lg:border-t-0 lg:pb-0">
      <div className="mx-auto max-w-lg px-2 pt-2 lg:flex lg:h-full lg:max-w-none lg:flex-col lg:px-4 lg:py-6">
        <div className="mb-8 hidden items-center gap-3 px-3 lg:flex">
          <div className="grid size-10 place-items-center rounded-xl bg-primary/15 text-primary">
            <LightbulbIcon className="size-5" aria-hidden="true" />
          </div>
          <div>
            <p className="font-semibold tracking-tight">LumiStripe</p>
            <p className="text-xs text-muted-foreground">Lighting control</p>
          </div>
        </div>
        <nav aria-label="Primary navigation" className="grid grid-cols-4 items-stretch gap-1 lg:flex lg:flex-col lg:gap-1">
          {items.map(({ to, label, icon: Icon, end }) => (
            <GuardedNavLink
              key={label}
              to={to}
              end={end}
              className={({ isActive }) => cn(
                "flex min-h-14 flex-col items-center justify-center gap-1 rounded-xl px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:min-h-11 lg:flex-row lg:justify-start lg:gap-3 lg:px-3 lg:text-sm",
                (isActive || (to === "/" && location.pathname === "/playback")) && "bg-primary/10 text-foreground lg:bg-muted",
              )}
            >
              <Icon className="size-5" aria-hidden="true" />
              <span>{label}</span>
            </GuardedNavLink>
          ))}
        </nav>
      </div>
    </aside>
  )
}
