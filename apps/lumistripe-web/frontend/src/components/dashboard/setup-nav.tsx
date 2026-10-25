import { useCallback, useMemo, useRef, type ComponentProps, type ReactNode } from "react"
import { GuardedNavLink } from "@/hooks/use-unsaved-changes"
import { AudioSetupCalibrationContext, type AudioSetupCalibrationStop } from "@/hooks/use-audio-setup-calibration-stop"

import { cn } from "@/lib/utils"

export function SetupPage({ children, ...props }: ComponentProps<"div">) {
  return (
    <div {...props} className={cn("space-y-4 pb-4", props.className)}>
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
    <nav className="grid grid-cols-2 gap-1 rounded-xl border border-white/5 bg-white/[0.03] p-1 sm:grid-cols-4" aria-label="Audio setup sections">
      <GuardedNavLink to="/setup/audio" end className={({ isActive }) => cn("grid min-h-11 place-items-center rounded-lg px-2 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", isActive && "bg-background text-foreground shadow-sm")}>Sources</GuardedNavLink>
      <GuardedNavLink to="/setup/audio/devices" className={({ isActive }) => cn("grid min-h-11 place-items-center rounded-lg px-2 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", isActive && "bg-background text-foreground shadow-sm")}>Devices</GuardedNavLink>
      <GuardedNavLink to="/setup/audio/tuning" className={({ isActive }) => cn("grid min-h-11 place-items-center rounded-lg px-2 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", isActive && "bg-background text-foreground shadow-sm")}>Tuning</GuardedNavLink>
      <GuardedNavLink to="/setup/audio/sync" className={({ isActive }) => cn("grid min-h-11 place-items-center rounded-lg px-2 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", isActive && "bg-background text-foreground shadow-sm")}>Sync</GuardedNavLink>
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
      <SetupPage>
        <AudioSetupTabs />
        {children}
      </SetupPage>
    </AudioSetupCalibrationContext.Provider>
  )
}
