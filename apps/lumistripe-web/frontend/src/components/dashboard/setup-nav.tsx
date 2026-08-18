import type { ReactNode } from "react"
import { GuardedNavLink } from "@/hooks/use-unsaved-changes"

import { cn } from "@/lib/utils"

export function SetupPage({ children }: { children: ReactNode }) {
  return (
    <div className="space-y-4 pb-4">
      <div>
        <h2 className="text-lg font-semibold">Setup</h2>
        <p className="text-sm text-muted-foreground">Configure hardware, audio, color, and startup behavior.</p>
      </div>
      <nav className="grid grid-cols-4 rounded-xl border border-white/5 bg-white/[0.03] p-1" aria-label="Setup sections">
        <GuardedNavLink to="/setup/stripes" className={({ isActive }) => cn("grid h-11 place-items-center rounded-lg text-sm text-muted-foreground", isActive && "bg-background text-foreground shadow-sm")}>Stripes</GuardedNavLink>
        <GuardedNavLink to="/setup/color" className={({ isActive }) => cn("grid h-11 place-items-center rounded-lg text-sm text-muted-foreground", isActive && "bg-background text-foreground shadow-sm")}>Color</GuardedNavLink>
        <GuardedNavLink to="/setup/audio" className={({ isActive }) => cn("grid h-11 place-items-center rounded-lg text-sm text-muted-foreground", isActive && "bg-background text-foreground shadow-sm")}>Audio</GuardedNavLink>
        <GuardedNavLink to="/setup/startup" className={({ isActive }) => cn("grid h-11 place-items-center rounded-lg text-sm text-muted-foreground", isActive && "bg-background text-foreground shadow-sm")}>Startup</GuardedNavLink>
      </nav>
      {children}
    </div>
  )
}

export function AudioSetupTabs() {
  return (
    <nav className="grid grid-cols-2 rounded-xl border border-white/5 bg-white/[0.03] p-1" aria-label="Audio setup sections">
      <GuardedNavLink to="/setup/audio" end className={({ isActive }) => cn("grid h-11 place-items-center rounded-lg text-sm text-muted-foreground", isActive && "bg-background text-foreground shadow-sm")}>Input device</GuardedNavLink>
      <GuardedNavLink to="/setup/audio/tuning" className={({ isActive }) => cn("grid h-11 place-items-center rounded-lg text-sm text-muted-foreground", isActive && "bg-background text-foreground shadow-sm")}>Tuning</GuardedNavLink>
    </nav>
  )
}

export function AudioSetupPage({ children }: { children: ReactNode }) {
  return (
    <SetupPage>
      <AudioSetupTabs />
      {children}
    </SetupPage>
  )
}
