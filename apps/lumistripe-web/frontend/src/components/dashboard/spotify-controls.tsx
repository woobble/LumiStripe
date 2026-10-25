import { useCallback, useEffect, useRef, useState } from "react"
import { CircleAlertIcon, Music2Icon, PauseIcon, PlayIcon, Repeat2Icon, ShuffleIcon, SkipBackIcon, SkipForwardIcon, Volume2Icon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import { dashboardApi, type SpotifyControlAction, type SpotifyStatusResponse } from "@/lib/api"

type SpotifySliderKey = "position" | "volume"
type SpotifySliderCommand = { queuedValue: number | null; sequence: number; running: boolean }
type SpotifySliderOptimisticValue = { value: number; expiresAt: number }

function firstSliderValue(value: number | readonly number[]) {
  return Array.isArray(value) ? value[0] ?? 0 : value
}

function formatMilliseconds(value: number | null | undefined) {
  const totalSeconds = Math.max(0, Math.floor((value ?? 0) / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = String(totalSeconds % 60).padStart(2, "0")
  return `${minutes}:${seconds}`
}

export function SpotifyControls({ initialStatus }: { initialStatus: SpotifyStatusResponse }) {
  const [spotify, setSpotify] = useState(initialStatus)
  const [commandBusy, setCommandBusy] = useState(false)
  const [positionDraft, setPositionDraft] = useState<number | null>(null)
  const [volumeDraft, setVolumeDraft] = useState<number | null>(null)
  const positionDragging = useRef(false)
  const volumeDragging = useRef(false)
  const sliderCommands = useRef<Record<SpotifySliderKey, SpotifySliderCommand>>({
    position: { queuedValue: null, sequence: 0, running: false },
    volume: { queuedValue: null, sequence: 0, running: false },
  })
  const sliderPending = useRef<Record<SpotifySliderKey, number | null>>({ position: null, volume: null })
  const sliderOptimistic = useRef<Record<SpotifySliderKey, SpotifySliderOptimisticValue | null>>({ position: null, volume: null })

  const updateStatus = useCallback((next: SpotifyStatusResponse) => {
    setSpotify(next)
    const now = Date.now()
    for (const key of ["position", "volume"] as const) {
      const optimistic = sliderOptimistic.current[key]
      if (optimistic && optimistic.expiresAt <= now) sliderOptimistic.current[key] = null
    }
    if (!positionDragging.current && sliderPending.current.position === null) {
      setPositionDraft(sliderOptimistic.current.position?.value ?? next.position_ms)
    }
    if (!volumeDragging.current && sliderPending.current.volume === null) {
      setVolumeDraft(sliderOptimistic.current.volume?.value ?? next.volume)
    }
  }, [])

  useEffect(() => {
    updateStatus(initialStatus)
  }, [initialStatus, updateStatus])

  useEffect(() => {
    let mounted = true
    const refresh = async () => {
      try {
        const next = await dashboardApi.getSpotifyStatus()
        if (mounted) updateStatus(next)
      } catch {
        // Keep the last useful status while the optional service is unavailable.
      }
    }
    const interval = window.setInterval(() => void refresh(), 1000)
    return () => {
      mounted = false
      window.clearInterval(interval)
    }
  }, [updateStatus])

  const runControl = async (action: SpotifyControlAction, value?: number | boolean | string) => {
    if (commandBusy) return
    setCommandBusy(true)
    try {
      updateStatus(await dashboardApi.controlSpotify(action, value))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Spotify control failed.")
    } finally {
      setCommandBusy(false)
    }
  }

  const sendSliderCommand = useCallback((key: SpotifySliderKey, action: "seek" | "set_volume", value: number) => {
    const command = sliderCommands.current[key]
    command.queuedValue = value
    command.sequence += 1
    sliderPending.current[key] = value
    if (command.running) return
    command.running = true

    void (async () => {
      try {
        while (command.queuedValue !== null) {
          const commandValue = command.queuedValue
          command.queuedValue = null
          const sequence = command.sequence
          try {
            updateStatus(await dashboardApi.controlSpotify(action, commandValue))
            // Soloist can acknowledge a command before publishing its next status event.
            if (command.sequence === sequence) {
              sliderPending.current[key] = null
              sliderOptimistic.current[key] = { value: commandValue, expiresAt: Date.now() + 2000 }
              if (key === "position") setPositionDraft(commandValue)
              else setVolumeDraft(commandValue)
            }
          } catch (error) {
            if (command.sequence === sequence) {
              sliderPending.current[key] = null
              sliderOptimistic.current[key] = null
              if (key === "position") setPositionDraft(null)
              else setVolumeDraft(null)
              toast.error(error instanceof Error ? error.message : "Spotify control failed.")
            }
          }
        }
      } finally {
        command.running = false
      }
    })()
  }, [updateStatus])

  const ready = Boolean(spotify.connected && spotify.logged_in)
  const duration = Math.max(1, spotify.duration_ms ?? spotify.track?.duration_ms ?? 1)
  const position = Math.min(Math.max(0, positionDraft ?? spotify.position_ms), duration)
  const volume = Math.min(100, Math.max(0, volumeDraft ?? spotify.volume))
  const playing = spotify.status === "playing" || spotify.status === "buffering"

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><Music2Icon className="size-4 text-emerald-300" />Spotify Connect</CardTitle>
            <CardDescription>Play Spotify through Soloist. Its dedicated PipeWire sink is analysed for the Stripe animation and routed to the selected output.</CardDescription>
          </div>
          <Badge variant={ready ? "default" : "outline"}>{ready ? "Ready" : "Unavailable"}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {!spotify.configured && <p className="rounded-xl bg-amber-500/10 p-3 text-sm text-amber-100">Spotify is not configured. Set <span className="font-mono">LUMI_SPOTIFY_API_KEY</span> during deployment and restart the Spotify service.</p>}
        {spotify.configured && !spotify.connected && <p className="rounded-xl bg-white/[0.03] p-3 text-sm text-muted-foreground">Waiting for the local Soloist WebSocket service.</p>}
        {spotify.configured && spotify.connected && !spotify.logged_in && <p className="rounded-xl bg-amber-500/10 p-3 text-sm text-amber-100">Open Spotify on a phone or desktop and choose this device, <span className="font-medium">{spotify.device_name ?? "LumiStripe"}</span>, to sign in and start playback.</p>}
        {spotify.error && <div role="alert" className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{spotify.error}</span></div>}
        {spotify.track && (
          <div className="flex items-center gap-3 rounded-xl border border-white/5 bg-white/[0.03] p-3">
            <div className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-emerald-400/15 text-emerald-200"><Music2Icon className="size-5" /></div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{spotify.track.name || "Unknown track"}</p>
              <p className="truncate text-xs text-muted-foreground">{spotify.track.artists.join(", ") || "Unknown artist"}{spotify.track.album ? ` · ${spotify.track.album}` : ""}</p>
            </div>
          </div>
        )}
        <div className="flex items-center justify-center gap-2">
          <Button variant="outline" size="icon" className="size-10 rounded-full" disabled={!ready || commandBusy} onClick={() => void runControl("skip_prev")} aria-label="Previous Spotify track"><SkipBackIcon /></Button>
          <Button size="icon-lg" className="rounded-full" disabled={!ready || commandBusy} onClick={() => void runControl(playing ? "pause" : "play")} aria-label={playing ? "Pause Spotify" : "Play Spotify"}>{playing ? <PauseIcon /> : <PlayIcon />}</Button>
          <Button variant="outline" size="icon" className="size-10 rounded-full" disabled={!ready || commandBusy} onClick={() => void runControl("skip_next")} aria-label="Next Spotify track"><SkipForwardIcon /></Button>
        </div>
        <div className="space-y-1">
          <Slider aria-label="Spotify track position" min={0} max={duration} step={1000} value={[position]} disabled={!ready || !spotify.track} onValueChange={(value) => { positionDragging.current = true; sliderOptimistic.current.position = null; setPositionDraft(firstSliderValue(value)) }} onValueCommitted={(value) => { const next = firstSliderValue(value); positionDragging.current = false; setPositionDraft(next); sendSliderCommand("position", "seek", next) }} />
          <div className="flex justify-between text-xs text-muted-foreground"><span>{formatMilliseconds(position)}</span><span>{formatMilliseconds(duration)}</span></div>
        </div>
        <div className="grid gap-4 rounded-xl border border-white/5 bg-white/[0.03] p-3 sm:grid-cols-2">
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3 text-sm"><span className="flex items-center gap-2"><Volume2Icon className="size-4" />Volume</span><span className="font-mono text-muted-foreground">{volume}%</span></div>
            <Slider aria-label="Spotify volume" min={0} max={100} step={1} value={[volume]} disabled={!ready} onValueChange={(value) => { volumeDragging.current = true; sliderOptimistic.current.volume = null; setVolumeDraft(firstSliderValue(value)) }} onValueCommitted={(value) => { const next = firstSliderValue(value); volumeDragging.current = false; setVolumeDraft(next); sendSliderCommand("volume", "set_volume", next) }} />
          </div>
          <div className="flex items-center justify-between gap-3">
            <Button variant={spotify.shuffle ? "default" : "outline"} size="sm" disabled={!ready || commandBusy} onClick={() => void runControl("set_shuffle", !spotify.shuffle)}><ShuffleIcon />Shuffle</Button>
            <Button variant="outline" size="sm" disabled={!ready || commandBusy} onClick={() => void runControl("set_repeat", spotify.repeat === "off" ? "context" : spotify.repeat === "context" ? "track" : "off")}><Repeat2Icon />{spotify.repeat === "track" ? "Track" : spotify.repeat === "context" ? "Context" : "Repeat"}</Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
