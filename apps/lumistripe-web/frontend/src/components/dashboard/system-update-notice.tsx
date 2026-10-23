import { useCallback, useEffect, useState } from "react"
import { ArrowDownToLineIcon, CircleAlertIcon, CircleCheckIcon, Loader2Icon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { dashboardApi, type UpdateStatus } from "@/lib/api"

function shortCommit(commit: string | null) {
  return commit ? commit.slice(0, 7) : ""
}

export function SystemUpdateNotice() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [installRequested, setInstallRequested] = useState(false)
  const [installError, setInstallError] = useState<string | null>(null)
  const [hiddenSuccessAt, setHiddenSuccessAt] = useState<string | null>(null)

  const refresh = useCallback(async (): Promise<UpdateStatus | null> => {
    try {
      const next = await dashboardApi.getUpdateStatus()
      setStatus(next)
      if (next.install_state === "success") setInstallRequested(false)
      if (next.install_state === "failed") setInstallRequested(false)
      return next
    } catch {
      // The updater restarts the dashboard during installation; keep the last
      // known state visible while the browser reconnects.
      return null
    }
  }, [])

  const check = useCallback(async () => {
    try {
      await dashboardApi.checkForUpdates()
      window.setTimeout(() => void refresh(), 2_000)
    } catch {
      // Offline checks are expected while the Pi is used without a hotspot.
    }
  }, [refresh])

  useEffect(() => {
    void refresh().then((current) => {
      if (current?.supported && (navigator.onLine || current.install_state === "running")) {
        void check()
      }
    })
    const onOnline = () => {
      void refresh().then((current) => {
        if (current?.supported) void check()
      })
    }
    const pollingInterval =
      installRequested || status?.install_state === "running" ? 3_000 : 30_000
    const poll = window.setInterval(() => void refresh(), pollingInterval)
    window.addEventListener("online", onOnline)
    return () => {
      window.clearInterval(poll)
      window.removeEventListener("online", onOnline)
    }
  }, [check, installRequested, refresh, status?.install_state])

  useEffect(() => {
    const finishedAt = status?.install_finished_at
    if (status?.install_state !== "success" || !finishedAt) return
    const timeout = window.setTimeout(() => setHiddenSuccessAt(finishedAt), 30_000)
    return () => window.clearTimeout(timeout)
  }, [status?.install_finished_at, status?.install_state])

  const install = async () => {
    setInstallRequested(true)
    setInstallError(null)
    try {
      setStatus(await dashboardApi.installUpdate())
    } catch (error) {
      setInstallRequested(false)
      setInstallError(error instanceof Error ? error.message : "Could not start the update.")
    }
  }

  const installing = installRequested || status?.install_state === "running"
  if (!status?.supported) return null

  if (installing) {
    return (
      <section className="mb-4 flex items-center gap-3 rounded-2xl border border-violet-300/20 bg-violet-400/10 px-4 py-3" aria-live="polite">
        <Loader2Icon className="size-5 shrink-0 animate-spin text-violet-200" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium">Installing LumiStripe update</p>
          <p className="text-xs text-muted-foreground">The dashboard will reconnect when installation finishes.</p>
        </div>
      </section>
    )
  }

  if (
    status.install_state === "success" &&
    !status.update_available &&
    status.install_finished_at !== null &&
    status.install_finished_at !== hiddenSuccessAt
  ) {
    return (
      <section className="mb-4 flex items-center gap-3 rounded-2xl border border-emerald-300/20 bg-emerald-400/10 px-4 py-3" aria-live="polite">
        <CircleCheckIcon className="size-5 shrink-0 text-emerald-200" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium">LumiStripe updated successfully</p>
          <p className="text-xs text-muted-foreground">Now running {shortCommit(status.installed_commit)}.</p>
        </div>
      </section>
    )
  }

  if (!status.update_available) return null

  if (status.working_tree_dirty) {
    return (
      <section className="mb-4 flex items-start gap-3 rounded-2xl border border-amber-300/20 bg-amber-400/10 px-4 py-3" role="status">
        <CircleAlertIcon className="mt-0.5 size-5 shrink-0 text-amber-200" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium">An update is available, but the Pi checkout has local changes.</p>
          <p className="text-xs text-muted-foreground">Those changes must be saved or removed on the Pi before updating.</p>
        </div>
      </section>
    )
  }

  return (
    <section className="mb-4 flex items-center gap-3 rounded-2xl border border-violet-300/20 bg-violet-400/10 px-4 py-3" aria-live="polite">
      {status.install_state === "failed" ? (
        <CircleAlertIcon className="size-5 shrink-0 text-amber-200" aria-hidden="true" />
      ) : (
        <ArrowDownToLineIcon className="size-5 shrink-0 text-violet-200" aria-hidden="true" />
      )}
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{status.install_state === "failed" ? "Update could not be installed" : "LumiStripe update available"}</p>
        <p className="text-xs text-muted-foreground">
          {status.install_state === "failed"
            ? status.install_error ?? "Check the Pi logs before retrying."
            : `Version ${shortCommit(status.available_commit)} is ready to install.`}
        </p>
        {installError && <p className="mt-1 text-xs text-destructive">{installError}</p>}
      </div>
      <Button className="h-9 shrink-0 rounded-xl px-3 text-xs" onClick={() => void install()}>
        {status.install_state === "failed" ? "Retry" : "Install update"}
      </Button>
    </section>
  )
}
