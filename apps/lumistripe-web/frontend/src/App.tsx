import { ActivityIcon, AudioLinesIcon, CircleAlertIcon, LightbulbIcon, Settings2Icon, SlidersHorizontalIcon } from "lucide-react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { lazy, Suspense, useEffect, useState, type ReactNode } from "react"
import { Navigate, Route, Routes } from "react-router"

import { PairingScreen } from "@/components/auth/pairing-screen"
import { ConnectionBadge } from "@/components/dashboard/connection-badge"
import { CalibrationPanel } from "@/components/dashboard/calibration-panel"
import { ControlPanel } from "@/components/dashboard/control-panel"
import { StatusPanel } from "@/components/dashboard/status-panel"
import { SetupPage } from "@/components/dashboard/setup-nav"
import { StripeManagementPanel } from "@/components/dashboard/stripe-management-panel"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Toaster } from "@/components/ui/sonner"
import { useAccess, type AccessController } from "@/hooks/use-access"
import { useDashboard } from "@/hooks/use-dashboard"
import { cn } from "@/lib/utils"
import { GuardedNavLink, UnsavedChangesProvider } from "@/hooks/use-unsaved-changes"

const AudioTuningPanel = lazy(async () => {
  const module = await import("@/components/dashboard/audio-tuning-panel")
  return { default: module.AudioTuningPanel }
})

const AudioStatusPanel = lazy(async () => {
  const module = await import("@/components/dashboard/audio-status-panel")
  return { default: module.AudioStatusPanel }
})
const AudioInputPanel = lazy(async () => {
  const module = await import("@/components/dashboard/audio-input-panel")
  return { default: module.AudioInputPanel }
})

const StartupPanel = lazy(async () => {
  const module = await import("@/components/dashboard/startup-panel")
  return { default: module.StartupPanel }
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

function PwaStatus() {
  const [offline, setOffline] = useState(() => typeof navigator !== "undefined" && !navigator.onLine)
  const [updateAvailable, setUpdateAvailable] = useState(false)

  useEffect(() => {
    const goOffline = () => setOffline(true)
    const goOnline = () => setOffline(false)
    const update = () => setUpdateAvailable(true)
    window.addEventListener("offline", goOffline)
    window.addEventListener("online", goOnline)
    window.addEventListener("lumistripe:pwa-update", update)
    return () => {
      window.removeEventListener("offline", goOffline)
      window.removeEventListener("online", goOnline)
      window.removeEventListener("lumistripe:pwa-update", update)
    }
  }, [])

  if (!offline && !updateAvailable) return null
  return (
    <div className="fixed inset-x-3 bottom-[calc(5.75rem+env(safe-area-inset-bottom))] z-50 mx-auto flex max-w-lg items-center justify-between gap-3 rounded-2xl border border-white/10 bg-card/95 px-4 py-3 text-sm shadow-2xl backdrop-blur-xl">
      <span className="text-muted-foreground">{offline ? "Offline — controls are unavailable until the Pi reconnects." : "A new LumiStripe version is ready."}</span>
      {updateAvailable && !offline && <Button className="h-9 shrink-0 rounded-xl px-3 text-xs" onClick={() => void window.__lumistripeUpdatePwa?.()}>Update</Button>}
    </div>
  )
}

function SetupGuard({ access, children }: { access: AccessController; children: ReactNode }) {
  if (access.loading) return <DashboardSkeleton />
  if (access.required && !access.authenticated) return <PairingScreen access={access} />
  return children
}

function Dashboard({ access }: { access: AccessController }) {
  const controller = useDashboard()
  const { state, connection, loading, loadError } = controller
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
          <UnsavedChangesProvider>
          <div className="min-h-0 flex-1 pb-[calc(5.25rem+env(safe-area-inset-bottom))]">
            <Routes>
              <Route path="/" element={<ControlPanel controller={controller} />} />
              <Route path="/audio" element={<Suspense fallback={<DashboardSkeleton />}><AudioStatusPanel /></Suspense>} />
              <Route path="/setup" element={<Navigate to="/setup/stripes" replace />} />
              <Route path="/setup/stripes" element={<SetupGuard access={access}><StripeManagementPanel controller={controller} /></SetupGuard>} />
              <Route path="/setup/color" element={<SetupGuard access={access}><SetupPage><CalibrationPanel controller={controller} /></SetupPage></SetupGuard>} />
              <Route path="/setup/audio" element={<SetupGuard access={access}><Suspense fallback={<DashboardSkeleton />}><AudioInputPanel /></Suspense></SetupGuard>} />
              <Route path="/setup/audio/tuning" element={<SetupGuard access={access}><Suspense fallback={<DashboardSkeleton />}><AudioTuningPanel /></Suspense></SetupGuard>} />
              <Route path="/setup/startup" element={<SetupGuard access={access}><Suspense fallback={<DashboardSkeleton />}><StartupPanel /></Suspense></SetupGuard>} />
              <Route path="/calibration" element={<Navigate to="/setup/color" replace />} />
              <Route path="/diagnostics" element={<StatusPanel controller={controller} onLogout={access.required && access.authenticated ? access.logout : undefined} />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
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
          </div>
          </UnsavedChangesProvider>
        )}
      </main>
      <Toaster position="top-center" richColors />
      <PwaStatus />
    </div>
  )
}

export default function App() {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  }))
  return (
    <QueryClientProvider client={queryClient}>
      <DashboardApp />
    </QueryClientProvider>
  )
}

function DashboardApp() {
  const access = useAccess()
  if (access.loading) {
    return (
      <div className="relative grid min-h-svh place-items-center overflow-hidden bg-background px-5">
        <div className="ambient-glow ambient-glow-one" aria-hidden="true" />
        <div className="w-full max-w-sm">
          <DashboardSkeleton />
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
