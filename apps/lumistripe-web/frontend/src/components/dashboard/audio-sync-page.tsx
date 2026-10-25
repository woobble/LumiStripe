import { useEffect } from "react"

import { useAudioSettings } from "@/hooks/use-audio-settings"
import { useAudioSyncCalibration } from "@/hooks/use-audio-sync-calibration"
import { dashboardApi } from "@/lib/api"
import { AudioSyncControls } from "@/components/dashboard/audio-sync-controls"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAudioSetupCalibrationStop } from "@/hooks/use-audio-setup-calibration-stop"

export function AudioSyncPage() {
  const { settings, loading, loadError, refresh, applySettings } = useAudioSettings()
  const calibration = useAudioSyncCalibration()
  const { registerStop, stopCalibration } = useAudioSetupCalibrationStop()

  useEffect(() => {
    registerStop(calibration.stop)
    return () => {
      void stopCalibration()
      registerStop(null)
    }
  }, [calibration.stop, registerStop, stopCalibration])

  const refreshSettings = () => { void refresh().catch(() => undefined) }
  const saveOffset = async (offsetMs: number) => {
    const next = await dashboardApi.setAudioSyncOffset(offsetMs)
    applySettings(next)
    return next
  }

  if (loading && !settings) return <Skeleton className="h-72 rounded-xl" aria-label="Loading sync settings" />
  if (!settings) {
    return <Card className="border-white/5 bg-card/80"><CardContent className="space-y-3 pt-4"><p role="alert" className="text-sm text-red-200">{loadError ?? "Audio sync settings are unavailable."}</p><Button type="button" variant="outline" disabled={loading} onClick={refreshSettings}>Retry sync settings</Button></CardContent></Card>
  }

  return (
    <div className="space-y-4">
      {loadError && <Card className="border-red-300/15 bg-red-500/5"><CardContent className="flex flex-wrap items-center justify-between gap-3 pt-4"><p role="alert" className="text-sm text-red-200">{loadError}</p><Button type="button" variant="outline" disabled={loading} onClick={refreshSettings}>Retry settings refresh</Button></CardContent></Card>}
      <div className="px-1"><h2 className="text-base font-semibold">Audio-to-LED sync</h2><p className="text-xs text-muted-foreground">The offset below belongs to the active input and output route. Choose an output on Devices before calibrating its route.</p></div>
      <AudioSyncControls
        key={settings.sync_route}
        settings={settings}
        calibrationRunning={calibration.running}
        calibrationBusy={calibration.starting || calibration.stopping}
        calibrationStarting={calibration.starting}
        calibrationStopping={calibration.stopping}
        onStartCalibration={() => { void calibration.start() }}
        onStopCalibration={() => { void calibration.stop() }}
        onSave={saveOffset}
      />
    </div>
  )
}
