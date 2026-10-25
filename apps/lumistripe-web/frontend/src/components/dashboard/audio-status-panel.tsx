import { memo, useCallback, useEffect, useState } from "react"
import { Link } from "react-router"
import { AudioLinesIcon, ChevronDownIcon, Settings2Icon } from "lucide-react"
import { toast } from "sonner"

import {
  AudioControlVisualization,
  AudioHistoryChart,
  AudioMusicStructureSummary,
  AudioTelemetryCards,
  useAudioTelemetry,
} from "@/components/dashboard/audio-telemetry"
import { AudioOutputControls } from "@/components/dashboard/audio-output-controls"
import { AudioSourceStatus } from "@/components/dashboard/audio-source-status"
import { audioSourceLabel } from "@/components/dashboard/audio-source"
import { SpotifyControls } from "@/components/dashboard/spotify-controls"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useBluetooth } from "@/hooks/use-bluetooth"
import type { BluetoothAction } from "@/hooks/use-bluetooth"
import type { AudioSettingsResponse, BluetoothStatusResponse } from "@/lib/api"
import { dashboardApi } from "@/lib/api"
import type { WebSocketStreamStatus } from "@/hooks/use-websocket-stream"

function streamStateLabel(connection: WebSocketStreamStatus, fresh: boolean, sequence: number) {
  if (connection === "connecting") return "Connecting to audio stream"
  if (connection === "reconnecting") return sequence > 0 ? "Stream reconnecting · last sample stale" : "Audio stream reconnecting"
  if (fresh) return "Audio stream live"
  return sequence > 0 ? "Audio samples stale" : "Waiting for audio samples"
}

function sourceMessage(
  source: string | null | undefined,
  configuredSource: string | null | undefined,
  monitoring: boolean,
  settings: AudioSettingsResponse | null,
  audioStatus: string,
) {
  const genericStatus = audioStatus === "No audio source active." || audioStatus === "Audio input status unavailable."
  switch (source) {
    case "spotify":
      return monitoring ? "Spotify is providing the active audio signal." : genericStatus ? "Spotify is selected, but its audio stream is unavailable." : audioStatus
    case "bluetooth": {
      const deviceName = settings?.bluetooth?.connected_device?.name
      return monitoring
        ? deviceName ? `Receiving audio from ${deviceName}.` : "Bluetooth input is active."
        : genericStatus ? "Bluetooth input is disconnected or its audio stream is unavailable." : audioStatus
    }
    case "mic":
      return monitoring
        ? settings?.active_device_name ? `Listening to ${settings.active_device_name}.` : "Microphone input is active."
        : genericStatus ? "The selected microphone is unavailable or not monitoring audio." : audioStatus
    case "demo":
      return "Using the internal demo beat; no microphone capture is required."
    case "off":
      return configuredSource === "auto"
        ? genericStatus ? "Automatic routing is waiting for an available audio source." : audioStatus
        : genericStatus ? "No audio source is active." : audioStatus
    default:
      return configuredSource === "auto"
        ? source ? `Automatic source · ${audioSourceLabel(source)} is currently selected.` : "Automatic routing is waiting for an available audio source."
        : audioStatus
  }
}

function sourceHeading(source: string | null | undefined, configuredSource: string | null | undefined) {
  if (configuredSource === "auto") return "Automatic audio source"
  switch (source) {
    case "mic": return "Microphone input"
    case "bluetooth": return "Bluetooth input"
    case "demo": return "Demo beat"
    case "off": return "No active audio source"
    default: return `${audioSourceLabel(source)} source`
  }
}

function AudioStatusPanelView({
  audioStatus,
  configuredAudioSource,
  activeAudioSource,
  audioMonitoring,
}: {
  audioStatus: string
  configuredAudioSource?: string | null
  activeAudioSource?: string | null
  audioMonitoring?: boolean | null
}) {
  const { telemetry, history, connection } = useAudioTelemetry()
  const [settings, setSettings] = useState<AudioSettingsResponse | null>(null)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const bluetoothController = useBluetooth(settings?.bluetooth)
  const {
    status: bluetoothStatus,
    busy: bluetoothBusy,
    replaceStatus,
    run: runBluetooth,
  } = bluetoothController

  const applySettings = useCallback((next: AudioSettingsResponse) => {
    setSettings(next)
    setSettingsError(null)
    if (next.bluetooth) replaceStatus(next.bluetooth)
  }, [replaceStatus])

  const failSettingsLoad = useCallback((error: unknown) => {
    const message = error instanceof Error ? error.message : "Audio settings are unavailable."
    setSettingsError(message)
    setSettings(null)
  }, [])

  const loadSettings = useCallback(async () => {
    try {
      applySettings(await dashboardApi.getAudioSettings())
    } catch (error) {
      failSettingsLoad(error)
    } finally {
      setLoading(false)
    }
  }, [applySettings, failSettingsLoad])

  useEffect(() => {
    let mounted = true
    void dashboardApi.getAudioSettings()
      .then((next) => { if (mounted) applySettings(next) })
      .catch((error: unknown) => { if (mounted) failSettingsLoad(error) })
      .finally(() => { if (mounted) setLoading(false) })
    return () => { mounted = false }
  }, [applySettings, failSettingsLoad])

  const runOutputAction = useCallback(async (
    action: BluetoothAction,
    operation: () => Promise<BluetoothStatusResponse>,
  ) => {
    try {
      return await runBluetooth(action, operation)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Audio output operation failed.")
      return null
    }
  }, [runBluetooth])

  const configuredSource = configuredAudioSource ?? settings?.source
  const activeSource = activeAudioSource ?? settings?.active_source
  const monitoring = audioMonitoring ?? settings?.monitoring ?? false
  const streamFresh = connection === "connected" && telemetry.fresh
  const spotifySelected = activeSource === "spotify" || (activeSource == null && configuredSource === "spotify")
  const sourceStatus = sourceMessage(activeSource, configuredSource, monitoring, settings, audioStatus)
  const silent = streamFresh
    && monitoring
    && activeSource !== "demo"
    && activeSource !== "off"
    && telemetry.input_peak < 0.001
  const outputStatus = bluetoothStatus ?? settings?.bluetooth ?? null

  return (
    <div className="space-y-4 pb-4">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-cyan-200">Control center</p>
          <h2 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-tight"><AudioLinesIcon className="size-5 text-cyan-300" />Audio</h2>
          <p className="mt-1 text-sm text-muted-foreground">Playback, routing, and live signal health.</p>
        </div>
        <Link to="/setup/audio" aria-label="Open audio setup" className="inline-flex size-10 shrink-0 items-center justify-center rounded-xl border border-border bg-background text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <Settings2Icon className="size-4" />
        </Link>
      </header>

      <div className="flex flex-wrap items-center gap-2" role="status" aria-live="polite">
        <Badge variant={streamFresh ? "default" : "outline"}>{streamStateLabel(connection, telemetry.fresh, telemetry.sequence)}</Badge>
        {telemetry.health !== "inactive" && <Badge variant="outline">{telemetry.health}</Badge>}
      </div>

      {settingsError && (
        <Card className="border-amber-300/15 bg-amber-400/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 py-3">
            <p role="alert" className="text-sm text-amber-100">{settingsError}</p>
            <Button type="button" variant="outline" size="sm" disabled={loading} onClick={() => { setLoading(true); setSettingsError(null); void loadSettings() }}>Retry</Button>
          </CardContent>
        </Card>
      )}

      <section className="grid gap-3 md:grid-cols-2" aria-label="Audio routing status">
        <AudioSourceStatus
          configuredSource={configuredSource}
          activeSource={activeSource}
          monitoring={monitoring}
          connection={connection}
          fresh={streamFresh}
          statusMessage={sourceStatus}
          compact
        />
        <AudioOutputControls
          status={outputStatus}
          busy={bluetoothBusy}
          onAction={runOutputAction}
          variant="compact"
        />
      </section>

      {spotifySelected ? (
        settings
          ? <SpotifyControls key="spotify-player" initialStatus={settings.spotify} />
          : <Card className="border-white/5 bg-card/80"><CardContent className="py-4 text-sm text-muted-foreground">Spotify playback status is unavailable until audio settings load.</CardContent></Card>
      ) : (
        <Card className="border-white/5 bg-card/80">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <CardTitle>{sourceHeading(activeSource, configuredSource)}</CardTitle>
                <CardDescription className="mt-1">{sourceStatus}</CardDescription>
              </div>
              <Badge variant={monitoring || activeSource === "demo" ? "default" : "outline"}>{activeSource === "off" ? "Inactive" : activeSource === "demo" ? "Internal" : monitoring ? "Active" : "Unavailable"}</Badge>
            </div>
          </CardHeader>
          {loading && <CardContent className="pt-0"><Skeleton className="h-2 w-full" /></CardContent>}
        </Card>
      )}

      <AudioControlVisualization telemetry={telemetry} fresh={streamFresh} silent={silent} />
      <AudioMusicStructureSummary telemetry={telemetry} fresh={streamFresh} />

      <details
        className="group rounded-xl border border-white/10 bg-card/60"
        onToggle={(event) => setDiagnosticsOpen(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
          <span>Advanced audio diagnostics</span>
          <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
        </summary>
        {diagnosticsOpen && (
          <div className="space-y-4 border-t border-white/5 p-3 sm:p-4">
            <AudioTelemetryCards telemetry={telemetry} />
            <AudioHistoryChart history={history} />
          </div>
        )}
      </details>
    </div>
  )
}

export const AudioStatusPanel = memo(AudioStatusPanelView)
