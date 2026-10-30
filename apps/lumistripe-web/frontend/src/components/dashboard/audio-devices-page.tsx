import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { AudioOutputControls } from "@/components/dashboard/audio-output-controls"
import { BluetoothDevicePanel } from "@/components/dashboard/bluetooth-device-panel"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useBluetooth, type BluetoothAction } from "@/hooks/use-bluetooth"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"
import { dashboardApi, type BluetoothStatusResponse } from "@/lib/api"
import { useAudioSetupCalibrationStop } from "@/hooks/use-audio-setup-calibration-stop"

export function AudioDevicesPage() {
  const { status, busy, loading, error, refresh, run } = useBluetooth()
  const { stopCalibration } = useAudioSetupCalibrationStop()
  const [alias, setAlias] = useState("")
  const [aliasDirty, setAliasDirty] = useState(false)
  useUnsavedChangesGuard(aliasDirty, "Your Bluetooth name changes will be discarded if you leave this page.")

  useEffect(() => {
    if (!aliasDirty && status?.adapter_alias !== undefined) setAlias(status.adapter_alias ?? "")
  }, [aliasDirty, status?.adapter_alias])

  const handleAliasChange = useCallback((next: string) => {
    setAlias(next)
    setAliasDirty(next.trim() !== (status?.adapter_alias ?? ""))
  }, [status?.adapter_alias])

  const runAction = useCallback(async (
    action: BluetoothAction,
    operation: () => Promise<BluetoothStatusResponse>,
    message?: string,
    onSuccess?: () => void,
  ) => {
    try {
      await stopCalibration()
      const next = await run(action, operation)
      if (!next) return null
      onSuccess?.()
      if (message) toast.success(message)
      return next
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Bluetooth operation failed.")
      return null
    }
  }, [run, stopCalibration])

  const saveAlias = () => {
    const nextAlias = alias.trim()
    if (!nextAlias) return
    void runAction("alias", () => dashboardApi.setBluetoothAlias(nextAlias), "Bluetooth device name saved.", () => {
      setAlias(nextAlias)
      setAliasDirty(false)
    })
  }

  const refreshStatus = () => { void refresh() }

  if (!status) {
    return (
      <div className="space-y-4">
        {loading ? <Skeleton className="h-56 rounded-xl" aria-label="Loading audio devices" /> : (
          <Card className="border-white/5 bg-card/80"><CardContent className="space-y-3 pt-4"><p role="alert" className="text-sm text-red-200">{error ?? "Audio device status is unavailable."}</p><Button type="button" variant="outline" onClick={refreshStatus}>Retry device status</Button></CardContent></Card>
        )}
      </div>
    )
  }

  return (
    <div className="grid items-start gap-4 xl:grid-cols-2">
      {error && <Card className="border-amber-300/15 bg-amber-500/5"><CardContent className="flex flex-wrap items-center justify-between gap-3 pt-4"><p role="status" className="text-sm text-amber-100">Could not refresh device status. Showing the last available result: {error}</p><Button type="button" variant="outline" disabled={loading || busy} onClick={refreshStatus}>Retry refresh</Button></CardContent></Card>}
      <section aria-labelledby="audio-outputs-heading" className="space-y-2">
        <div className="px-1"><h2 id="audio-outputs-heading" className="text-base font-semibold">Audio output</h2><p className="text-xs text-muted-foreground">Choose where sound plays from the Pi.</p></div>
        <AudioOutputControls status={status} busy={busy} onAction={runAction} />
      </section>
      <section aria-labelledby="bluetooth-devices-heading" className="space-y-2">
        <div className="px-1"><h2 id="bluetooth-devices-heading" className="text-base font-semibold">Bluetooth</h2><p className="text-xs text-muted-foreground">Pair speakers and audio inputs, or change the device name.</p></div>
        <BluetoothDevicePanel
          status={status}
          alias={alias}
          busy={busy}
          onAliasChange={handleAliasChange}
          onAliasSave={saveAlias}
          onAction={(action, operation, message) => { void runAction(action, operation, message) }}
          onRefresh={refreshStatus}
        />
      </section>
    </div>
  )
}
