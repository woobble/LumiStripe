import { useEffect, useState } from "react"
import { CheckCircle2Icon, CircleAlertIcon, Mic2Icon, RotateCcwIcon, SlidersHorizontalIcon } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { dashboardApi, type AudioCalibrationSessionResponse } from "@/lib/api"

export function AudioCalibrationWizard({ device, onApplied }: { device: string | null; onApplied: () => void }) {
  const [session, setSession] = useState<AudioCalibrationSessionResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [duration, setDuration] = useState(8)

  useEffect(() => {
    if (!session || session.status !== "capturing") return
    const timer = window.setInterval(async () => {
      try { setSession(await dashboardApi.getAudioCalibration(session.session_id)) } catch (error) { toast.error(error instanceof Error ? error.message : "Calibration status unavailable."); setSession(null) }
    }, 250)
    return () => window.clearInterval(timer)
  }, [session])

  const start = async () => {
    if (!device || busy) return
    setBusy(true)
    try { setSession(await dashboardApi.startAudioCalibration(device, duration)) } catch (error) { toast.error(error instanceof Error ? error.message : "Could not start calibration.") } finally { setBusy(false) }
  }

  const finish = async (apply: boolean) => {
    if (!session || busy) return
    setBusy(true)
    try {
      await dashboardApi.finishAudioCalibration(session.session_id, apply, apply && session.result ? { target_level: session.result.recommended_target_level, noise_floor: session.result.recommended_noise_floor } : undefined)
      setSession(null)
      if (apply) { onApplied(); toast.success("Audio calibration applied.") } else toast.success("Calibration discarded.")
    } catch (error) { toast.error(error instanceof Error ? error.message : "Could not finish calibration.") } finally { setBusy(false) }
  }

  const result = session?.result
  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader><CardTitle className="flex items-center gap-2"><Mic2Icon className="size-4 text-emerald-300" />Microphone calibration</CardTitle><CardDescription>Measure the room floor and recommend a calmer software gain for quiet passages.</CardDescription></CardHeader>
      <CardContent className="space-y-4">
        {!session && <>
          <p className="text-sm text-muted-foreground">Stay quiet while the microphone listens. You can review the recommendation before anything is changed.</p>
          <div className="flex items-center gap-2"><label htmlFor="calibration-duration" className="text-sm">Capture time</label><select id="calibration-duration" value={duration} onChange={(event) => setDuration(Number(event.target.value))} className="h-10 rounded-lg border border-input bg-background px-3 text-sm"><option value={5}>5 seconds</option><option value={8}>8 seconds</option><option value={12}>12 seconds</option></select></div>
          <Button className="h-11 w-full rounded-xl" disabled={!device || busy} onClick={() => void start()}><SlidersHorizontalIcon />Start measurement</Button>
        </>}
        {session?.status === "capturing" && <div className="space-y-3"><div className="flex justify-between text-sm"><span>Listening…</span><span>{Math.ceil(session.remaining_seconds)}s remaining</span></div><Progress value={(session.elapsed_seconds / (session.elapsed_seconds + session.remaining_seconds || 1)) * 100} /><p className="text-xs text-muted-foreground">Keep the room quiet so the noise floor is representative.</p></div>}
        {session?.status === "complete" && result && <>
          <div className="grid grid-cols-2 gap-2"><Metric label="Measured floor" value={result.measured_floor.toFixed(3)} /><Metric label="Measured peak" value={result.measured_peak.toFixed(3)} /><Metric label="Recommended floor" value={result.recommended_noise_floor.toFixed(3)} /><Metric label="Recommended target" value={result.recommended_target_level.toFixed(3)} /></div>
          <p className="rounded-xl bg-emerald-400/10 p-3 text-sm text-emerald-100">Review complete. Applying will update this microphone profile only.</p>
          <div className="grid grid-cols-2 gap-2"><Button variant="outline" disabled={busy} onClick={() => void finish(false)}><RotateCcwIcon />Discard</Button><Button disabled={busy} onClick={() => void finish(true)}><CheckCircle2Icon />Apply</Button></div>
        </>}
        {session?.error && <p className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="size-4" />{session.error}</p>}
      </CardContent>
    </Card>
  )
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-xl bg-white/[0.035] p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-mono text-lg">{value}</p></div> }
