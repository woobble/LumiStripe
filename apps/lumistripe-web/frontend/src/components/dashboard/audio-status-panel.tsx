import { memo, useCallback, useMemo, useState } from "react"
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
import { AudioDiagnostics } from "@/components/dashboard/audio-diagnostics"
import { getAudioDiagnostics } from "@/components/dashboard/audio-diagnostics-model"
import { AudioOutputControls } from "@/components/dashboard/audio-output-controls"
import { AudioSourceStatus } from "@/components/dashboard/audio-source-status"
import { audioSourceLabel } from "@/components/dashboard/audio-source"
import { SpotifyControls } from "@/components/dashboard/spotify-controls"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { useBluetooth } from "@/hooks/use-bluetooth"
import type { BluetoothAction } from "@/hooks/use-bluetooth"
import { useAudioSettings } from "@/hooks/use-audio-settings"
import type { AudioSettingsResponse, BluetoothStatusResponse } from "@/lib/api"
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

function AudioStatusPanelView({
  audioStatus,
  audioHealth,
  configuredAudioSource,
  activeAudioSource,
  audioMonitoring,
}: {
  audioStatus: string
  audioHealth?: string
  configuredAudioSource?: string | null
  activeAudioSource?: string | null
  audioMonitoring?: boolean | null
}) {
  const { telemetry, history, connection } = useAudioTelemetry()
  const { settings, loadError: settingsError, loading, refresh: refreshSettings } = useAudioSettings()
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const bluetoothController = useBluetooth(settings?.bluetooth)
  const {
    status: bluetoothStatus,
    busy: bluetoothBusy,
    error: bluetoothError,
    loading: bluetoothLoading,
    run: runBluetooth,
    refresh: refreshBluetooth,
  } = bluetoothController

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
  const audioDiagnostics = useMemo(() => getAudioDiagnostics({
    settings,
    settingsLoading: loading,
    settingsError,
    audioStatus,
    audioHealth,
    configuredSource,
    activeSource,
    monitoring,
    telemetry,
    connection,
    bluetoothStatus: outputStatus,
    outputLoading: outputStatus === null && bluetoothLoading,
    bluetoothError,
  }), [activeSource, audioHealth, audioStatus, bluetoothError, bluetoothLoading, configuredSource, connection, loading, monitoring, outputStatus, settings, settingsError, telemetry])

  return (
    <div className="space-y-4 pb-4">
      <header className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">Audio</p>
          <h2 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-tight"><AudioLinesIcon className="size-5 text-cyan-300" aria-hidden="true" />Audio</h2>
        </div>
        <Link to="/setup/audio" aria-label="Open audio setup" className="inline-flex size-10 shrink-0 items-center justify-center rounded-xl border border-border bg-background text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <Settings2Icon className="size-4" />
        </Link>
      </header>

      <div className="flex flex-wrap items-center gap-2" role="status" aria-live="polite">
        <Badge variant={streamFresh ? "default" : "outline"}>{streamStateLabel(connection, telemetry.fresh, telemetry.sequence)}</Badge>
        {telemetry.health !== "inactive" && <Badge variant="outline">{telemetry.health}</Badge>}
      </div>

      <AudioDiagnostics
        issues={audioDiagnostics}
        onRetrySettings={() => { void refreshSettings().catch(() => undefined) }}
        onRetryOutput={() => { void refreshBluetooth().catch(() => undefined) }}
      />

      <section className="grid items-start gap-3 xl:grid-cols-2" aria-label="Audio routing status">
        <AudioSourceStatus
          configuredSource={configuredSource}
          activeSource={activeSource}
          monitoring={monitoring}
          connection={connection}
          fresh={streamFresh}
          statusMessage={sourceStatus}
          compact
        />
        <section id="audio-output-controls" aria-label="Audio output controls">
          <AudioOutputControls
            status={outputStatus}
            busy={bluetoothBusy}
            onAction={runOutputAction}
            variant="compact"
          />
        </section>
      </section>

      {spotifySelected && (
        settings
          ? <SpotifyControls key="spotify-player" initialStatus={settings.spotify} />
          : <Card className="border-white/5 bg-card/80"><CardContent className="py-4 text-sm text-muted-foreground">Spotify playback status is unavailable until audio settings load.</CardContent></Card>
      )}

      <section className="grid items-start gap-3 xl:grid-cols-2" aria-label="Live audio analysis">
        <AudioControlVisualization telemetry={telemetry} fresh={streamFresh} silent={silent} />
        <AudioMusicStructureSummary telemetry={telemetry} fresh={streamFresh} />
      </section>

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
