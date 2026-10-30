import { useEffect, useState } from "react"
import { AudioLinesIcon, SaveIcon } from "lucide-react"
import { toast } from "sonner"

import { audioSourceLabels, audioSources, isAudioSource, type AudioSourceValue } from "@/components/dashboard/audio-source"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { AudioSettingsResponse } from "@/lib/api"

export function AudioSourceControls({
  settings,
  disabled = false,
  onDirtyChange,
  onSave,
}: {
  settings: AudioSettingsResponse
  disabled?: boolean
  onDirtyChange?: (dirty: boolean) => void
  onSave: (source: AudioSourceValue) => Promise<AudioSettingsResponse>
}) {
  const [source, setSource] = useState<AudioSourceValue>(isAudioSource(settings.source) ? settings.source : "auto")
  const [saving, setSaving] = useState(false)
  const dirty = source !== settings.source

  useEffect(() => {
    if (!dirty) setSource(isAudioSource(settings.source) ? settings.source : "auto")
  }, [dirty, settings.source])

  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])

  const changeSource = (next: AudioSourceValue) => {
    setSource(next)
    onDirtyChange?.(next !== settings.source)
  }

  const save = async () => {
    if (saving || disabled || source === settings.source) return
    setSaving(true)
    try {
      const next = await onSave(source)
      if (isAudioSource(next.source)) setSource(next.source)
      onDirtyChange?.(false)
      toast.success("Audio input applied.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Audio input could not be changed.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><AudioLinesIcon className="size-4 text-cyan-300" />Audio input</CardTitle>
        <CardDescription>Choose what drives the Stripe animation. Automatic mode prefers active Spotify Connect, then live Bluetooth input, then the selected microphone on hardware.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Select value={source} onValueChange={(value) => { if (value && isAudioSource(value)) changeSource(value) }} disabled={saving || disabled}>
          <SelectTrigger aria-label="Audio input source" className="h-11 rounded-xl"><SelectValue>{(value) => isAudioSource(value) ? audioSourceLabels[value] : "Select audio source"}</SelectValue></SelectTrigger>
          <SelectContent>{audioSources.map((value) => <SelectItem key={value} value={value}>{audioSourceLabels[value]}</SelectItem>)}</SelectContent>
        </Select>
        <Button className="h-12 w-full rounded-xl" disabled={saving || disabled || source === settings.source} onClick={() => void save()}><SaveIcon />{saving ? "Applying…" : "Save audio input"}</Button>
      </CardContent>
    </Card>
  )
}
