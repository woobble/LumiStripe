import { CircleAlertIcon, LightbulbIcon } from "lucide-react"
import { useEffect, useState, type ReactNode } from "react"

import { PairingScreen } from "@/components/auth/pairing-screen"
import { ConnectionBadge } from "@/components/dashboard/connection-badge"
import { DashboardNavigation } from "@/components/dashboard/dashboard-navigation"
import { DashboardRoutes } from "@/components/dashboard/dashboard-routes"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Toaster } from "@/components/ui/sonner"
import { useAccess, type AccessController } from "@/hooks/use-access"
import { useDashboard } from "@/hooks/use-dashboard"
import { UnsavedChangesProvider } from "@/hooks/use-unsaved-changes"
import { AppProviders } from "@/app/providers"

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

function Dashboard({ access }: { access: AccessController }) {
  if (access.loading) return <DashboardAccessFrame><DashboardSkeleton /></DashboardAccessFrame>
  if (access.required && !access.authenticated) return <DashboardAccessFrame><PairingScreen access={access} /></DashboardAccessFrame>
  return <LiveDashboard access={access} />
}

function DashboardAccessFrame({ children }: { children: ReactNode }) {
  return (
    <div className="relative min-h-svh overflow-x-hidden bg-background">
      <main className="relative mx-auto flex min-h-svh w-full max-w-lg flex-col justify-center px-4 pt-[max(1rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))]">
        {children}
      </main>
      <Toaster position="top-center" richColors />
    </div>
  )
}

function LiveDashboard({ access }: { access: AccessController }) {
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
            <DashboardRoutes access={access} controller={controller} fallback={<DashboardSkeleton />} />
            <DashboardNavigation />
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
  return (
    <AppProviders>
      <DashboardApp />
    </AppProviders>
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
