import { CircleAlertIcon, LightbulbIcon } from "lucide-react"
import { useEffect, useState, type ReactNode } from "react"

import { PairingScreen } from "@/components/auth/pairing-screen"
import { ConnectionBadge } from "@/components/dashboard/connection-badge"
import { DashboardNavigation } from "@/components/dashboard/dashboard-navigation"
import { DashboardRoutes } from "@/components/dashboard/dashboard-routes"
import { DashboardSkeleton } from "@/components/dashboard/dashboard-skeleton"
import { SystemUpdateNotice } from "@/components/dashboard/system-update-notice"
import { Button } from "@/components/ui/button"
import { Toaster } from "@/components/ui/sonner"
import { useAccess, type AccessController } from "@/hooks/use-access"
import { useDashboard } from "@/hooks/use-dashboard"
import { UnsavedChangesProvider } from "@/hooks/use-unsaved-changes"
import { AppProviders } from "@/app/providers"

function Unavailable({ message, retry }: { message: string; retry: () => void }) {
  return (
    <div className="grid min-h-[60svh] place-items-center px-4 text-center">
      <div>
        <div className="mx-auto mb-4 grid size-14 place-items-center rounded-2xl bg-red-500/10 text-red-300">
          <CircleAlertIcon className="size-7" aria-hidden="true" />
        </div>
        <h2 className="text-xl font-semibold">LumiStripe is unavailable</h2>
        <p className="mx-auto mt-2 max-w-xs text-sm text-muted-foreground">{message}</p>
        <div className="mt-5 flex flex-wrap justify-center gap-3">
          <Button className="h-11 rounded-xl px-5" onClick={retry}>Try again</Button>
          <Button
            className="h-11 rounded-xl px-5"
            variant="outline"
            onClick={() => {
              if (window.__lumistripeReloadLatestPwa) {
                void window.__lumistripeReloadLatestPwa()
              } else {
                window.location.reload()
              }
            }}
          >
            Reload Page
          </Button>
        </div>
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
    <div className="fixed inset-x-3 bottom-[calc(5.75rem+env(safe-area-inset-bottom))] z-50 mx-auto flex max-w-lg items-center justify-between gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-sm shadow-xl backdrop-blur-xl lg:inset-x-auto lg:right-6 lg:bottom-6">
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
    <div className="min-h-svh overflow-x-hidden bg-background">
      <main className="mx-auto flex min-h-svh w-full max-w-xl flex-col justify-center px-4 pt-[max(1rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))] sm:px-6">
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
    <div className="min-h-svh overflow-x-hidden bg-background">
      <DashboardNavigation applicationName={state?.application_name ?? "LumiStripe"} />
      <div className="min-h-svh lg:pl-64">
        <header className="sticky top-0 z-30 border-b border-border/70 bg-background/90 pt-[env(safe-area-inset-top)] backdrop-blur-xl">
          <div className="mx-auto flex h-14 max-w-[1500px] items-center gap-3 px-4 sm:px-6 lg:h-16 lg:px-10">
            <div className="flex min-w-0 items-center gap-2 lg:hidden">
              <LightbulbIcon className="size-5 text-primary" aria-hidden="true" />
              <span className="truncate text-sm font-semibold tracking-tight">{state?.application_name ?? "LumiStripe"}</span>
            </div>
            <div className="hidden text-sm text-muted-foreground lg:block">
              {state ? `${state.runtime} controller` : "Lighting controller"}
            </div>
            <div className="ml-auto"><ConnectionBadge status={connection} /></div>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1500px] px-4 pt-5 pb-[calc(6rem+env(safe-area-inset-bottom))] sm:px-6 lg:px-10 lg:pt-8 lg:pb-10">
          <SystemUpdateNotice />
          {loading && !state ? (
            <DashboardSkeleton />
          ) : !state ? (
            <Unavailable message={loadError ?? "The controller did not return a state."} retry={() => void controller.refresh()} />
          ) : (
            <UnsavedChangesProvider>
              <DashboardRoutes access={access} controller={controller} fallback={<DashboardSkeleton />} />
            </UnsavedChangesProvider>
          )}
        </main>
      </div>
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
      <div className="relative grid min-h-svh place-items-center overflow-hidden bg-background px-4 py-8 sm:px-6 lg:px-10">
        <div className="w-full max-w-[1500px]">
          <DashboardSkeleton />
        </div>
      </div>
    )
  }
  if (access.statusError) {
    return (
      <DashboardAccessFrame>
        <Unavailable message={access.statusError} retry={() => void access.refresh()} />
      </DashboardAccessFrame>
    )
  }
  if (access.required && !access.authenticated) {
    return (
      <div className="min-h-svh overflow-hidden bg-background">
        <PairingScreen access={access} />
        <Toaster position="top-center" richColors />
      </div>
    )
  }
  return <Dashboard access={access} />
}
