import { useCallback, useState } from "react"

import { AudioSourceControls } from "@/components/dashboard/audio-source-controls"
import { LiveAudioSourceStatus } from "@/components/dashboard/audio-source-status"
import { MicrophoneInputControls } from "@/components/dashboard/microphone-input-controls"
import { SpotifySetupStatus } from "@/components/dashboard/spotify-controls"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAudioSettings } from "@/hooks/use-audio-settings"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"
import { dashboardApi } from "@/lib/api"
import type { AudioSourceValue } from "@/components/dashboard/audio-source"
import { useAudioSetupCalibrationStop } from "@/hooks/use-audio-setup-calibration-stop"

export function AudioSourcesPage({
  configuredAudioSource,
  activeAudioSource,
  audioMonitoring,
}: {
  configuredAudioSource?: string | null
  activeAudioSource?: string | null
  audioMonitoring?: boolean | null
}) {
  const { settings, loading, loadError, refresh, applySettings } = useAudioSettings()
  const { stopCalibration } = useAudioSetupCalibrationStop()
  const [sourceDirty, setSourceDirty] = useState(false)
  const [microphoneDirty, setMicrophoneDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  useUnsavedChangesGuard(sourceDirty || microphoneDirty)

  const changeAudioSetting = useCallback(async (operation: () => Promise<Awaited<ReturnType<typeof dashboardApi.setAudioSource>>>) => {
    if (saving) throw new Error("Another audio setting is still being applied.")
    setSaving(true)
    try {
      await stopCalibration()
      const next = await operation()
      applySettings(next)
      return next
    } finally {
      setSaving(false)
    }
  }, [applySettings, saving, stopCalibration])

  const saveSource = (source: AudioSourceValue) => changeAudioSetting(() => dashboardApi.setAudioSource(source))
  const saveMicrophone = (device: string) => changeAudioSetting(() => dashboardApi.selectAudioDevice(device))
  const refreshSettings = () => { void refresh().catch(() => undefined) }

  if (loading && !settings) {
    return <div className="space-y-4" aria-label="Loading audio sources"><Skeleton className="h-44 rounded-xl" /><Skeleton className="h-48 rounded-xl" /><Skeleton className="h-36 rounded-xl" /></div>
  }

  if (!settings) {
    return <Card className="border-white/5 bg-card/80"><CardContent className="space-y-3 pt-4"><p role="alert" className="text-sm text-red-200">{loadError ?? "Audio settings are unavailable."}</p><Button type="button" variant="outline" disabled={loading} onClick={refreshSettings}>Retry audio settings</Button></CardContent></Card>
  }

  return (
    <div className="space-y-4">
      {loadError && <Card className="border-red-300/15 bg-red-500/5"><CardContent className="flex flex-wrap items-center justify-between gap-3 pt-4"><p role="alert" className="text-sm text-red-200">{loadError}</p><Button type="button" variant="outline" disabled={loading || saving} onClick={refreshSettings}>Retry device refresh</Button></CardContent></Card>}
      <LiveAudioSourceStatus settings={settings} configuredAudioSource={configuredAudioSource} activeAudioSource={activeAudioSource} audioMonitoring={audioMonitoring} />
      <p className="-mt-2 px-1 text-xs text-muted-foreground">Configured source is your selection. Active source shows what is currently feeding audio analysis; automatic mode may fall back to the selected microphone.</p>
      {settings.error && <p role="alert" className="rounded-xl bg-red-500/10 p-3 text-sm text-red-200">{settings.error}</p>}
      <div className="grid items-start gap-4 xl:grid-cols-2">
        <AudioSourceControls settings={settings} disabled={loading || saving} onDirtyChange={setSourceDirty} onSave={saveSource} />
        <MicrophoneInputControls settings={settings} loading={loading || saving} onDirtyChange={setMicrophoneDirty} onRefresh={refreshSettings} onSave={saveMicrophone} />
      </div>
      <div className="space-y-2">
        <h3 className="px-1 text-sm font-semibold">Spotify Connect</h3>
        <SpotifySetupStatus status={settings.spotify} />
        {settings.spotify.error && <p role="alert" className="px-1 text-xs text-amber-200">{settings.spotify.error}</p>}
      </div>
    </div>
  )
}
