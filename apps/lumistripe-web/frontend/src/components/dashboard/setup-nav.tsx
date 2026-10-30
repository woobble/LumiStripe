import { useCallback, useMemo, useRef, type ComponentProps, type ReactNode } from "react"
import { GuardedNavLink } from "@/hooks/use-unsaved-changes"
import { AudioSetupCalibrationContext, type AudioSetupCalibrationStop } from "@/hooks/use-audio-setup-calibration-stop"

import { cn } from "@/lib/utils"

export function SetupPage({
  children,
  showNavigation = true,
  title = "Device setup",
  ...props
}: ComponentProps<"div"> & { showNavigation?: boolean; title?: string }) {
  return (
    <div {...props} className={cn("space-y-5 pb-5", props.className)}>
      <div>
        <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">Devices</p>
        <h2 className="mt-1 text-2xl font-semibold tracking-tight">{title}</h2>
      </div>
      {showNavigation && (
        <nav className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5" aria-label="Setup sections">
          {[
            ["/setup/stripes", "Stripes"],
            ["/setup/color", "Color"],
            ["/setup/audio", "Audio"],
            ["/setup/startup", "Startup"],
            ["/setup/network", "Network"],
          ].map(([to, label]) => (
            <GuardedNavLink
              key={to}
              to={to}
              className={({ isActive }) => cn(
                "flex min-h-11 items-center justify-center rounded-xl border border-border/70 px-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isActive && "border-primary/30 bg-primary/10 text-foreground",
              )}
            >
              {label}
            </GuardedNavLink>
          ))}
        </nav>
      )}
      {children}
    </div>
  )
}
export function AudioSetupTabs() {
  return (
    <nav className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Audio setup sections">
      <GuardedNavLink to="/setup/audio" end className={({ isActive }) => cn("grid min-h-11 place-items-center rounded-xl border border-border/70 px-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", isActive && "border-primary/30 bg-primary/10 text-foreground")}>Sources</GuardedNavLink>
      <GuardedNavLink to="/setup/audio/devices" className={({ isActive }) => cn("grid min-h-11 place-items-center rounded-xl border border-border/70 px-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", isActive && "border-primary/30 bg-primary/10 text-foreground")}>Devices</GuardedNavLink>
      <GuardedNavLink to="/setup/audio/tuning" className={({ isActive }) => cn("grid min-h-11 place-items-center rounded-xl border border-border/70 px-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", isActive && "border-primary/30 bg-primary/10 text-foreground")}>Tuning</GuardedNavLink>
      <GuardedNavLink to="/setup/audio/sync" className={({ isActive }) => cn("grid min-h-11 place-items-center rounded-xl border border-border/70 px-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", isActive && "border-primary/30 bg-primary/10 text-foreground")}>Sync</GuardedNavLink>
    </nav>
  )
}

export function AudioSetupPage({ children }: { children: ReactNode }) {
  const stopRef = useRef<AudioSetupCalibrationStop | null>(null)
  const pendingStop = useRef<Promise<void> | null>(null)
  const registerStop = useCallback((stop: AudioSetupCalibrationStop | null) => {
    stopRef.current = stop
  }, [])
  const stopCalibration = useCallback(() => {
    if (pendingStop.current) return pendingStop.current
    const stop = stopRef.current
    if (!stop) return Promise.resolve()
    let request: Promise<void>
    request = stop().finally(() => {
      if (pendingStop.current === request) pendingStop.current = null
    })
    pendingStop.current = request
    return request
  }, [])
  const calibrationContext = useMemo(() => ({ registerStop, stopCalibration }), [registerStop, stopCalibration])

  return (
    <AudioSetupCalibrationContext.Provider value={calibrationContext}>
      <SetupPage showNavigation={false} title="Audio setup">
        <AudioSetupTabs />
        {children}
      </SetupPage>
    </AudioSetupCalibrationContext.Provider>
  )
}
