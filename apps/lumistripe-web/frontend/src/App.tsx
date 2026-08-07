import { ActivityIcon, AudioLinesIcon, CircleAlertIcon, LightbulbIcon, PaletteIcon, SlidersHorizontalIcon } from "lucide-react"
import { lazy, Suspense, type MouseEvent } from "react"
import { Navigate, NavLink, Route, Routes } from "react-router"
import { toast } from "sonner"

import { PairingScreen } from "@/components/auth/pairing-screen"
import { ConnectionBadge } from "@/components/dashboard/connection-badge"
import { CalibrationPanel } from "@/components/dashboard/calibration-panel"
import { ControlPanel } from "@/components/dashboard/control-panel"
import { StatusPanel } from "@/components/dashboard/status-panel"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Toaster } from "@/components/ui/sonner"
import { useAccess, type AccessController } from "@/hooks/use-access"
import { useDashboard } from "@/hooks/use-dashboard"
import { cn } from "@/lib/utils"

const AudioTuningPanel = lazy(async () => {
  const module = await import("@/components/dashboard/audio-tuning-panel")
  return { default: module.AudioTuningPanel }
})

function DashboardSkeleton() {
  return (
    <div className="space-y-4" aria-label="Loading dashboard">
      <Skeleton className="h-14 rounded-2xl" />
      <Skeleton className="h-48 rounded-2xl" />
      <Skeleton className="h-56 rounded-2xl" />
    </div>
  )
}

function Unavailable({ message, retry }: { message: string; retry: () => void }) {
  return (
    <div className="grid min-h-[60svh] place-items-center px-4 text-center">
      <div>
        <div className="mx-auto mb-4 grid size-14 place-items-center rounded-2xl bg-red-500/10 text-red-300">
          <CircleAlertIcon className="size-7" aria-hidden="true" />
        </div>
        <h2 className="text-xl font-semibold">LumiStripe is unavailable</h2>
        <p className="mx-auto mt-2 max-w-xs text-sm text-muted-foreground">{message}</p>
        <Button className="mt-5 h-11 rounded-xl px-5" onClick={retry}>Try again</Button>
      </div>
    </div>
  )
}

function Dashboard({ access }: { access: AccessController }) {
  const controller = useDashboard()
  const { state, connection, loading, loadError } = controller
  const blockCalibrationExit = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!state?.calibration.active) return
    event.preventDefault()
    toast.info("Save or cancel the active calibration before leaving this page.")
  }

  return (
    <div className="relative min-h-svh overflow-x-hidden bg-background">
      <div className="ambient-glow ambient-glow-one" aria-hidden="true" />
      <div className="ambient-glow ambient-glow-two" aria-hidden="true" />

      <main className="relative mx-auto flex min-h-svh w-full max-w-lg flex-col px-4 pt-[max(1rem,env(safe-area-inset-top))]">
        <header className="mb-5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="grid size-11 place-items-center rounded-2xl border border-violet-300/15 bg-violet-400/10 text-violet-200 shadow-lg shadow-violet-950/30">
              <LightbulbIcon className="size-5" aria-hidden="true" />
            </div>
            <div>
              <h1 className="text-xl font-semibold tracking-tight">LumiStripe</h1>
              <p className="text-xs capitalize text-muted-foreground">
                {state ? `${state.runtime} controller` : "Lighting controller"}
              </p>
            </div>
          </div>
          <ConnectionBadge status={connection} />
        </header>

        {loading && !state ? (
          <DashboardSkeleton />
        ) : !state ? (
          <Unavailable message={loadError ?? "The controller did not return a state."} retry={() => void controller.refresh()} />
        ) : (
          <div className="min-h-0 flex-1 pb-[calc(5.25rem+env(safe-area-inset-bottom))]">
            <Routes>
              <Route path="/" element={<ControlPanel controller={controller} />} />
              <Route path="/audio" element={<Suspense fallback={<DashboardSkeleton />}><AudioTuningPanel /></Suspense>} />
              <Route path="/calibration" element={<CalibrationPanel controller={controller} />} />
              <Route path="/diagnostics" element={<StatusPanel controller={controller} onLogout={access.required ? access.logout : undefined} />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
            <div className="fixed inset-x-0 bottom-0 z-40 border-t border-white/5 bg-background/90 backdrop-blur-2xl">
              <div className="mx-auto w-full max-w-lg px-4 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
                <nav aria-label="Dashboard" className="grid h-14 w-full grid-cols-4 items-stretch rounded-2xl border border-white/5 bg-white/[0.04] p-1">
                  <NavLink
                    to="/"
                    end
                    onClick={blockCalibrationExit}
                    className={({ isActive }) => cn("flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] font-medium text-foreground/60 transition-colors", isActive && "bg-background text-foreground shadow-sm")}
                  >
                    <SlidersHorizontalIcon aria-hidden="true" />
                    Control
                  </NavLink>
                  <NavLink
                    to="/audio"
                    onClick={blockCalibrationExit}
                    className={({ isActive }) => cn("flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] font-medium text-foreground/60 transition-colors", isActive && "bg-background text-foreground shadow-sm")}
                  >
                    <AudioLinesIcon aria-hidden="true" />
                    Audio
                  </NavLink>
                  <NavLink
                    to="/calibration"
                    className={({ isActive }) => cn("flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] font-medium text-foreground/60 transition-colors", isActive && "bg-background text-foreground shadow-sm")}
                  >
                    <PaletteIcon aria-hidden="true" />
                    Color
                  </NavLink>
                  <NavLink
                    to="/diagnostics"
                    onClick={blockCalibrationExit}
                    className={({ isActive }) => cn("flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] font-medium text-foreground/60 transition-colors", isActive && "bg-background text-foreground shadow-sm")}
                  >
                    <ActivityIcon aria-hidden="true" />
                    Status
                  </NavLink>
                </nav>
              </div>
            </div>
          </div>
        )}
      </main>
      <Toaster position="top-center" richColors />
    </div>
  )
}

export default function App() {
  const access = useAccess()

  if (access.loading) {
    return (
      <div className="relative grid min-h-svh place-items-center overflow-hidden bg-background px-5">
        <div className="ambient-glow ambient-glow-one" aria-hidden="true" />
        <div className="w-full max-w-sm space-y-4" aria-label="Checking dashboard access">
          <Skeleton className="mx-auto size-14 rounded-2xl" />
          <Skeleton className="mx-auto h-6 w-48 rounded-lg" />
          <Skeleton className="h-14 rounded-xl" />
        </div>
      </div>
    )
  }

  if (access.required && !access.authenticated) {
    return (
      <div className="relative min-h-svh overflow-hidden bg-background">
        <div className="ambient-glow ambient-glow-one" aria-hidden="true" />
        <div className="ambient-glow ambient-glow-two" aria-hidden="true" />
        <PairingScreen access={access} />
        <Toaster position="top-center" richColors />
      </div>
    )
  }

  return <Dashboard access={access} />
}
