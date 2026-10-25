import type { WebSocketStreamStatus } from "@/hooks/use-websocket-stream"
import type { AudioSettingsResponse, AudioTelemetry, BluetoothStatusResponse } from "@/lib/api"

export type AudioDiagnosticAction =
  | { type: "link"; label: string; to: string }
  | { type: "retry-settings"; label: string }
  | { type: "retry-output"; label: string }
  | { type: "output-controls"; label: string }
  | null

export type AudioDiagnostic = {
  id: string
  severity: "info" | "warning"
  title: string
  message: string
  action: AudioDiagnosticAction
}

export type AudioDiagnosticsInput = {
  settings: AudioSettingsResponse | null
  settingsLoading: boolean
  settingsError: string | null
  audioStatus: string
  audioHealth?: string
  configuredSource: string | null | undefined
  activeSource: string | null | undefined
  monitoring: boolean
  telemetry: AudioTelemetry
  connection: WebSocketStreamStatus
  bluetoothStatus: BluetoothStatusResponse | null
  outputLoading: boolean
  bluetoothError: string | null
}

function sourceError(audioStatus: string) {
  if (["No audio source active.", "Audio input status unavailable."].includes(audioStatus)) return null
  return /(unavailable|failed|error|disconnected)/i.test(audioStatus) ? audioStatus : null
}

export function getAudioDiagnostics(input: AudioDiagnosticsInput): AudioDiagnostic[] {
  const issues: AudioDiagnostic[] = []
  const { settings, settingsLoading, settingsError, audioStatus, audioHealth, configuredSource, activeSource, monitoring, telemetry, connection, bluetoothStatus, outputLoading, bluetoothError } = input
  const configuredKnown = configuredSource != null || activeSource != null || settings !== null
  const expectsInput = configuredKnown && configuredSource !== "off" && configuredSource !== "demo"
  const activeIsIntentionalSilence = activeSource === "off" || activeSource === "demo"
  const startup = audioHealth === "starting" || (connection === "connecting" && telemetry.sequence === 0)
  const streamFresh = connection === "connected" && telemetry.fresh
  const expectedSignal = expectsInput && !activeIsIntentionalSilence
  const activeSourceMissing = expectedSignal && (!monitoring || activeSource == null || activeSource === "off")
  const isSilent = streamFresh && monitoring && !activeIsIntentionalSilence && telemetry.input_peak < 0.001

  if (!configuredKnown && settingsLoading) {
    issues.push({
      id: "source-status-loading",
      severity: "info",
      title: "Audio source status is loading",
      message: "Checking the configured source and its current availability.",
      action: null,
    })
  }

  if (expectedSignal && activeSourceMissing) {
    issues.push({
      id: "input-unavailable",
      severity: startup ? "info" : "warning",
      title: startup ? "Audio input is starting" : "No active audio input",
      message: startup ? "Waiting for the selected source to become active." : sourceError(audioStatus) ?? "The configured source is not currently feeding audio analysis.",
      action: startup ? null : { type: "link", label: "Check sources", to: "/setup/audio" },
    })
  } else if (expectedSignal && (!streamFresh || !monitoring)) {
    issues.push({
      id: "input-stale",
      severity: startup ? "info" : "warning",
      title: startup ? "Waiting for audio signal" : connection === "reconnecting" ? "Audio stream reconnecting" : "Audio signal is stale",
      message: startup ? "Live samples will appear when the source starts." : sourceError(audioStatus) ?? "The audio stream is not delivering fresh input samples.",
      action: startup ? null : { type: "link", label: "Check sources", to: "/setup/audio" },
    })
  } else if (expectedSignal && (audioHealth === "degraded" || audioHealth === "unavailable") && !isSilent) {
    issues.push({
      id: "input-health",
      severity: "warning",
      title: "Audio input needs attention",
      message: sourceError(audioStatus) ?? "The runtime reports degraded or unavailable audio capture.",
      action: { type: "link", label: "Check sources", to: "/setup/audio" },
    })
  } else if (isSilent) {
    issues.push({
      id: "input-silent",
      severity: "warning",
      title: "No input signal detected",
      message: "The source is active, but its recent input level is near zero. Check the microphone, playback, and capture gain.",
      action: { type: "link", label: "Check tuning", to: "/setup/audio/tuning" },
    })
  }

  if (streamFresh && telemetry.clipping_fraction > 0) {
    issues.push({
      id: "input-clipping",
      severity: "warning",
      title: "Audio input is clipping",
      message: `Clipped samples were reported in the recent signal (${Math.round(telemetry.clipping_fraction * 100)}%). Lower capture gain before calibrating.`,
      action: { type: "link", label: "Adjust tuning", to: "/setup/audio/tuning" },
    })
  }

  if (settingsError) {
    issues.push({
      id: "settings-unavailable",
      severity: "warning",
      title: "Audio settings could not be refreshed",
      message: settingsError,
      action: { type: "retry-settings", label: "Retry settings" },
    })
  } else if (settings?.error) {
    issues.push({
      id: "source-configuration",
      severity: "warning",
      title: "Audio source configuration needs attention",
      message: settings.error,
      action: { type: "link", label: "Open source settings", to: "/setup/audio" },
    })
  }

  const spotifySelected = configuredSource === "spotify" || activeSource === "spotify"
  if (spotifySelected && settings?.spotify) {
    const spotify = settings.spotify
    if (!spotify.configured || !spotify.connected || !spotify.logged_in || spotify.error) {
      issues.push({
        id: "spotify-configuration",
        severity: "warning",
        title: "Spotify source is not ready",
        message: spotify.error
          ?? (!spotify.configured ? "Spotify playback is not configured." : !spotify.connected ? "The Spotify Connect service is disconnected." : "Select LumiStripe in Spotify Connect to sign in."),
        action: { type: "link", label: "Open source settings", to: "/setup/audio" },
      })
    }
  }

  if (bluetoothError || bluetoothStatus?.error) {
    issues.push({
      id: "output-status-error",
      severity: "warning",
      title: "Audio output status could not be confirmed",
      message: bluetoothError ?? bluetoothStatus?.error ?? "The output service returned an error.",
      action: { type: "retry-output", label: "Retry output status" },
    })
  } else if (!bluetoothStatus && outputLoading) {
    issues.push({
      id: "output-status-loading",
      severity: "info",
      title: "Audio output status is loading",
      message: "Checking the selected PipeWire output and connection.",
      action: null,
    })
  } else if (bluetoothStatus?.default_sink) {
    const selectedOutput = bluetoothStatus.output_devices.find((device) => device.selector === bluetoothStatus.default_sink)
    if (!selectedOutput) {
      issues.push({
        id: "output-selection-unavailable",
        severity: "warning",
        title: "Selected audio output is unavailable",
        message: "The configured PipeWire output is missing from the available device list.",
        action: { type: "link", label: "Choose another output", to: "/setup/audio/devices" },
      })
    } else if (!selectedOutput.connected) {
      issues.push({
        id: "output-disconnected",
        severity: "warning",
        title: "Selected audio output is disconnected",
        message: `${selectedOutput.name} is selected but is not connected.`,
        action: { type: "link", label: "Manage audio devices", to: "/setup/audio/devices" },
      })
    } else if (!bluetoothStatus.output_ready) {
      issues.push({
        id: "output-unavailable",
        severity: "warning",
        title: "Selected audio output is unavailable",
        message: `${selectedOutput.name} is connected, but PipeWire reports it is not ready.`,
        action: { type: "link", label: "Open audio devices", to: "/setup/audio/devices" },
      })
    } else if (bluetoothStatus.output_muted) {
      issues.push({
        id: "output-muted",
        severity: "warning",
        title: "Audio output is muted",
        message: "Unmute the selected output using the control below.",
        action: { type: "output-controls", label: "Open output controls" },
      })
    }
  } else if (bluetoothStatus && !bluetoothStatus.output_ready) {
    issues.push({
      id: "output-unavailable",
      severity: "warning",
      title: "No active audio output",
      message: "No active PipeWire output is selected. Choose an available speaker to hear audio.",
      action: { type: "link", label: "Open audio devices", to: "/setup/audio/devices" },
    })
  }

  return issues
}
