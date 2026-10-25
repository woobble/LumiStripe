import { useEffect, useState } from "react"
import { Controller, useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { CircleAlertIcon, MicIcon, RefreshCwIcon, SaveIcon } from "lucide-react"
import { z } from "zod"
import { toast } from "sonner"

import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { AudioSettingsResponse } from "@/lib/api"

const inputSelectionSchema = z.object({ selected: z.string().trim().min(1, "Choose an input device.") })
type InputFormValues = z.infer<typeof inputSelectionSchema>

function deviceSelection(settings: AudioSettingsResponse) {
  return settings.fallback_device ?? settings.active_device ?? settings.devices[0]?.selector ?? ""
}

export function MicrophoneInputControls({
  settings,
  loading,
  onRefresh,
  onSave,
}: {
  settings: AudioSettingsResponse
  loading: boolean
  onRefresh: () => void
  onSave: (device: string) => Promise<AudioSettingsResponse>
}) {
  const [saving, setSaving] = useState(false)
  const form = useForm<InputFormValues>({ resolver: zodResolver(inputSelectionSchema), defaultValues: { selected: deviceSelection(settings) } })
  const { control, handleSubmit, reset, formState: { isDirty, errors } } = form
  useUnsavedChangesGuard(isDirty)

  useEffect(() => {
    if (!isDirty) reset({ selected: deviceSelection(settings) })
  }, [isDirty, reset, settings])

  const microphoneDevices = settings.devices.filter((device) => device.selector !== "bluetooth" && device.selector !== "spotify")
  const selected = form.watch("selected")
  const selectedName = microphoneDevices.find((device) => device.selector === selected)?.name
    ?? settings.active_device_name
    ?? "Select microphone"
  const active = settings.monitoring && settings.active_source === "mic"

  const save = async ({ selected: device }: InputFormValues) => {
    if (saving) return
    setSaving(true)
    try {
      const next = await onSave(device)
      reset({ selected: deviceSelection(next) })
      toast.success("Microphone input applied and saved.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Microphone input could not be changed.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="border-white/5 bg-card/80">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><MicIcon className="size-4 text-violet-300" />Microphone input</CardTitle>
            <CardDescription>Choose the microphone used when Bluetooth is not connected or is disabled.</CardDescription>
          </div>
          <Badge variant={active ? "default" : "outline"}>{active ? "Active" : "Fallback"}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <Controller control={control} name="selected" render={({ field }) => (
            <Select value={field.value} onValueChange={(value) => value && field.onChange(value)} disabled={!microphoneDevices.length || saving}>
              <SelectTrigger aria-label="Microphone input device" aria-invalid={errors.selected ? true : undefined} className="h-11 min-w-0 flex-1 rounded-xl"><SelectValue>{selectedName}</SelectValue></SelectTrigger>
              <SelectContent>{microphoneDevices.map((device) => <SelectItem key={device.selector} value={device.selector}>{device.name}</SelectItem>)}</SelectContent>
            </Select>
          )} />
          <Button type="button" variant="outline" size="icon" className="size-11 rounded-xl" disabled={saving || loading} onClick={onRefresh} aria-label="Refresh microphone devices"><RefreshCwIcon /></Button>
        </div>
        {settings.error && <div role="alert" className="flex gap-2 rounded-xl bg-red-500/10 p-3 text-sm text-red-200"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" /><span>{settings.error}</span></div>}
        {!microphoneDevices.length && !settings.error && <p className="text-sm text-muted-foreground">No microphone input devices were found.</p>}
        {errors.selected && <p role="alert" className="text-sm text-red-300">{errors.selected.message}</p>}
        <Button className="h-12 w-full rounded-xl" disabled={!isDirty || saving} onClick={() => void handleSubmit(save)()}><SaveIcon />{saving ? "Applying…" : "Save microphone input"}</Button>
        <p className="text-xs text-muted-foreground">Each microphone keeps its own tuning profile. Fine-tune the active input from the Audio page.</p>
      </CardContent>
    </Card>
  )
}
