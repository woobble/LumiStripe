import { ActivityIcon, AudioLinesIcon, Settings2Icon, SlidersHorizontalIcon } from "lucide-react"

import { GuardedNavLink } from "@/hooks/use-unsaved-changes"
import { cn } from "@/lib/utils"

export function DashboardNavigation() {
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t border-white/5 bg-background/90 backdrop-blur-2xl">
      <div className="mx-auto w-full max-w-lg px-4 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        <nav aria-label="Dashboard" className="grid h-14 w-full grid-cols-4 items-stretch rounded-2xl border border-white/5 bg-white/[0.04] p-1">
          <GuardedNavLink
            to="/"
            end
            className={({ isActive }) => cn("flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] font-medium text-foreground/60 transition-colors", isActive && "bg-background text-foreground shadow-sm")}
          >
            <SlidersHorizontalIcon aria-hidden="true" />
            Control
          </GuardedNavLink>
          <GuardedNavLink
            to="/audio"
            className={({ isActive }) => cn("flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] font-medium text-foreground/60 transition-colors", isActive && "bg-background text-foreground shadow-sm")}
          >
            <AudioLinesIcon aria-hidden="true" />
            Audio
          </GuardedNavLink>
          <GuardedNavLink
            to="/setup"
            className={({ isActive }) => cn("flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] font-medium text-foreground/60 transition-colors", isActive && "bg-background text-foreground shadow-sm")}
          >
            <Settings2Icon aria-hidden="true" />
            Setup
          </GuardedNavLink>
          <GuardedNavLink
            to="/diagnostics"
            className={({ isActive }) => cn("flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] font-medium text-foreground/60 transition-colors", isActive && "bg-background text-foreground shadow-sm")}
          >
            <ActivityIcon aria-hidden="true" />
            Status
          </GuardedNavLink>
        </nav>
      </div>
    </div>
  )
}
