import { memo, useCallback, useEffect, useRef, useState } from "react"
import { AudioSetupPage } from "@/components/dashboard/setup-nav"
import { AudioOutputControls } from "@/components/dashboard/audio-output-controls"
import { AudioSourceControls } from "@/components/dashboard/audio-source-controls"
import { LiveAudioSourceStatus } from "@/components/dashboard/audio-source-status"
import { AudioSyncControls } from "@/components/dashboard/audio-sync-controls"
import { BluetoothDevicePanel } from "@/components/dashboard/bluetooth-device-panel"
import { MicrophoneInputControls } from "@/components/dashboard/microphone-input-controls"
import { SpotifySetupStatus } from "@/components/dashboard/spotify-controls"
import { useBluetooth, type BluetoothAction } from "@/hooks/use-bluetooth"
import { dashboardApi, type AudioSettingsResponse, type BluetoothStatusResponse } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"

function AudioInputPanelView({
  configuredAudioSource,
  activeAudioSource,
  audioMonitoring,
}: {
  configuredAudioSource?: string | null
  activeAudioSource?: string | null
  audioMonitoring?: boolean | null
}) {
  const [settings, setSettings] = useState<AudioSettingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [bluetoothAlias, setBluetoothAlias] = useState("")
  const [calibrationRunning, setCalibrationRunning] = useState(false)
  const calibrationActive = useRef(false)
  const calibrationTimer = useRef<number | null>(null)
  const initialLoadComplete = useRef(false)
  const bluetoothController = useBluetooth(settings?.bluetooth)
  const {
    status: bluetoothStatus,
    busy: bluetoothBusy,
    replaceStatus,
    refresh: refreshBluetooth,
    run: runBluetooth,
  } = bluetoothController
  const bluetooth = bluetoothStatus ?? settings?.bluetooth ?? null

  const applySettings = useCallback((next: AudioSettingsResponse) => {
    setSettings(next)
    if (next.bluetooth) replaceStatus(next.bluetooth)
  }, [replaceStatus])

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const next = await dashboardApi.getAudioSettings()
      applySettings(next)
      if (!initialLoadComplete.current && next.bluetooth?.adapter_alias) {
        setBluetoothAlias(next.bluetooth.adapter_alias)
      }
      initialLoadComplete.current = true
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Could not load audio devices.")
      toast.error(error instanceof Error ? error.message : "Could not load audio devices.")
    } finally {
      setLoading(false)
    }
  }, [applySettings])

  useEffect(() => {
    void load()
  }, [load])

  const startSyncCalibration = async () => {
    if (calibrationRunning) return
    try {
      await dashboardApi.setAudioSyncCalibration(true)
      calibrationActive.current = true
      setCalibrationRunning(true)
      calibrationTimer.current = window.setTimeout(() => {
        void stopSyncCalibration()
      }, 45_000)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start sync calibration.")
    }
  }

  const stopSyncCalibration = async () => {
    if (calibrationTimer.current !== null) window.clearTimeout(calibrationTimer.current)
    calibrationTimer.current = null
    calibrationActive.current = false
    setCalibrationRunning(false)
    try {
      await dashboardApi.setAudioSyncCalibration(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not stop sync calibration.")
    }
  }

  useEffect(() => () => {
    if (calibrationTimer.current !== null) window.clearTimeout(calibrationTimer.current)
    calibrationTimer.current = null
    if (calibrationActive.current) {
      calibrationActive.current = false
      void dashboardApi.setAudioSyncCalibration(false).catch(() => undefined)
    }
  }, [])

  const saveSource = async (source: Parameters<typeof dashboardApi.setAudioSource>[0]) => {
    if (calibrationRunning) await stopSyncCalibration()
    const next = await dashboardApi.setAudioSource(source)
    applySettings(next)
    return next
  }

  const saveMicrophone = async (device: string) => {
    if (calibrationRunning) await stopSyncCalibration()
    const next = await dashboardApi.selectAudioDevice(device)
    applySettings(next)
    return next
  }

  const saveSyncOffset = async (offsetMs: number) => {
    const next = await dashboardApi.setAudioSyncOffset(offsetMs)
    applySettings(next)
    return next
  }

  const runBluetoothAction = async (
    action: BluetoothAction,
    operation: () => Promise<BluetoothStatusResponse>,
    message?: string,
    onSuccess?: () => void,
  ) => {
    try {
      const status = await runBluetooth(action, operation)
      if (!status) return null
      onSuccess?.()
      if (message) toast.success(message)
      return status
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Bluetooth operation failed.")
      return null
    }
  }

  const runOutputAction = async (
    action: BluetoothAction,
    operation: () => Promise<BluetoothStatusResponse>,
  ) => {
    if (calibrationRunning) await stopSyncCalibration()
    const status = await runBluetoothAction(action, operation)
    if (status) void load()
    return status
  }

  const saveBluetoothAlias = () => {
    const alias = bluetoothAlias.trim()
    if (!alias) return
    void runBluetoothAction("alias", () => dashboardApi.setBluetoothAlias(alias), "Bluetooth device name saved.", () => setBluetoothAlias(alias))
  }

  return (
    <AudioSetupPage>
      {loading && !settings ? <Skeleton className="h-56 rounded-xl" /> : settings ? (
        <div className="space-y-4">
          <LiveAudioSourceStatus
            settings={settings}
            configuredAudioSource={configuredAudioSource}
            activeAudioSource={activeAudioSource}
            audioMonitoring={audioMonitoring}
          />
          <AudioSourceControls settings={settings} onSave={saveSource} />
          <SpotifySetupStatus status={settings.spotify} />
          <AudioOutputControls status={bluetooth} busy={bluetoothBusy} onAction={runOutputAction} />
          <AudioSyncControls
            key={settings.sync_route}
            settings={settings}
            calibrationRunning={calibrationRunning}
            onStartCalibration={() => void startSyncCalibration()}
            onStopCalibration={() => void stopSyncCalibration()}
            onSave={saveSyncOffset}
          />
          <BluetoothDevicePanel
            status={bluetooth ?? undefined}
            alias={bluetoothAlias}
            busy={bluetoothBusy}
            onAliasChange={setBluetoothAlias}
            onAliasSave={saveBluetoothAlias}
            onAction={(action, operation, message) => void runBluetoothAction(action, operation, message)}
            onRefresh={() => void refreshBluetooth()}
          />
          <MicrophoneInputControls
            settings={settings}
            loading={loading}
            onRefresh={() => void load()}
            onSave={saveMicrophone}
          />
        </div>
      ) : (
        <Card className="border-white/5 bg-card/80">
          <CardContent className="space-y-3 pt-4">
            <p role="alert" className="text-sm text-red-200">{loadError ?? "Audio settings are unavailable."}</p>
            <Button type="button" variant="outline" disabled={loading} onClick={() => void load()}>Retry audio settings</Button>
          </CardContent>
        </Card>
      )}
    </AudioSetupPage>
  )
}

export const AudioInputPanel = memo(AudioInputPanelView)
