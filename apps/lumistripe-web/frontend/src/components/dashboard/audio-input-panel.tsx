import { useCallback, useEffect, useState } from "react"
import { CircleAlertIcon, MicIcon, RefreshCwIcon, SaveIcon } from "lucide-react"
import { toast } from "sonner"

import { SetupPage } from "@/components/dashboard/setup-nav"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { dashboardApi, type AudioSettingsResponse } from "@/lib/api"

export function AudioInputPanel() {
  const [response, setResponse] = useState<AudioSettingsResponse | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await dashboardApi.getAudioSettings()
      setResponse(next)
      setSelected(next.active_device ?? next.devices[0]?.selector ?? null)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load audio devices.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const save = async () => {
    if (!selected || saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.selectAudioDevice(selected)
      setResponse(next)
      setSelected(next.active_device)
      toast.success("Audio input applied and saved.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Audio input could not be changed.")
    } finally {
      setSaving(false)
    }
  }

  const selectedName = response?.devices.find((device) => device.selector === selected)?.name
    ?? response?.active_device_name
    ?? "Select microphone"
  const dirty = selected !== null && selected !== response?.active_device

  return (
    <SetupPage>
      {loading && !response ? <Skeleton className="h-56 rounded-xl" /> : (
        <Card className="border-white/5 bg-card/80">
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div>
                <CardTitle className="flex items-center gap-2"><MicIcon className="size-4 text-violet-300" />Input device</CardTitle>
                <CardDescription>Choose the microphone used by Music mode.</CardDescription>
              </div>
              <Badge variant={response?.monitoring ? "default" : "outline"}>{response?.monitoring ? "Active" : response?.source}</Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex gap-2">
              <Select value={selected} onValueChange={(value) => value && setSelected(value)} disabled={!response?.devices.length || saving}>
                <SelectTrigger aria-label="Input device" className="h-11 min-w-0 flex-1 rounded-xl"><SelectValue>{selectedName}</SelectValue></SelectTrigger>
                <SelectContent>{response?.devices.map((device) => <SelectItem key={device.selector} value={device.selector}>{device.name}</SelectItem>)}</SelectContent>
              </Select>
              <Button variant="outline" size="icon" className="size-11 rounded-xl" disabled={saving} onClick={() => void load()} aria-label="Refresh audio devices"><RefreshCwIcon /></Button>
            </div>
            {response?.error && <div className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{response.error}</span></div>}
            {!response?.devices.length && !response?.error && <p className="text-sm text-muted-foreground">No input devices were found.</p>}
            <Button className="h-12 w-full rounded-xl" disabled={!dirty || saving} onClick={() => void save()}><SaveIcon />{saving ? "Applying…" : "Save input device"}</Button>
            <p className="text-xs text-muted-foreground">Each microphone keeps its own tuning profile. Fine-tune the active device from the Audio page.</p>
          </CardContent>
        </Card>
      )}
    </SetupPage>
  )
}
