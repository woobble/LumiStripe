import { memo, useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { MoonIcon, PowerIcon, RotateCcwIcon, SparklesIcon, SunMediumIcon } from "lucide-react"
import { toast } from "sonner"

import { SetupPage } from "@/components/dashboard/setup-nav"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Toggle } from "@/components/ui/toggle"
import { dashboardApi, type StartupSettingsResponse } from "@/lib/api"
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes"

const startupSchema = z.object({ restore_last_state: z.boolean() })
type StartupFormValues = z.infer<typeof startupSchema>

function title(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ")
}

function StartupPanelView() {
  const [settings, setSettings] = useState<StartupSettingsResponse | null>(null)
  const [saving, setSaving] = useState(false)
  const form = useForm<StartupFormValues>({ resolver: zodResolver(startupSchema), defaultValues: { restore_last_state: false } })
  const { watch, reset, handleSubmit, formState: { isDirty, errors } } = form
  useUnsavedChangesGuard(isDirty)

  const load = useCallback(async () => {
    try {
      const next = await dashboardApi.getStartupSettings()
      setSettings(next)
      reset({ restore_last_state: next.restore_last_state })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load startup behavior.")
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const update = async ({ restore_last_state }: StartupFormValues) => {
    if (saving) return
    setSaving(true)
    try {
      const next = await dashboardApi.updateStartupSettings(restore_last_state)
      setSettings(next)
      reset({ restore_last_state: next.restore_last_state })
      toast.success(restore_last_state ? "Current shared state will be restored after restart." : "Startup restoration disabled.")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Startup behavior could not be saved.")
    } finally {
      setSaving(false)
    }
  }

  const restore = watch("restore_last_state")
  return (
    <SetupPage>
      {!settings ? <Skeleton className="h-64 rounded-xl" /> : (
        <Card className="border-white/5 bg-card/80">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><RotateCcwIcon className="size-4 text-violet-300" />Restore last state</CardTitle>
            <CardDescription>Resume the last controls applied to All stripes after LumiStripe restarts.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Toggle
              pressed={restore}
              onPressedChange={(pressed) => { form.setValue("restore_last_state", pressed, { shouldDirty: true }); queueMicrotask(() => void handleSubmit(update)()) }}
              disabled={saving}
              aria-label={`Restore last state ${restore ? "on" : "off"}`}
              variant="outline"
              className="h-12 w-full justify-between px-4 data-pressed:border-violet-300/30 data-pressed:bg-violet-400/15 data-pressed:text-violet-100"
            >
              <span>Restore last state</span><span className="text-xs">{restore ? "On" : "Off"}</span>
            </Toggle>
            {errors.restore_last_state && <p role="alert" className="text-sm text-red-300">{errors.restore_last_state.message}</p>}

            <div className="grid grid-cols-2 gap-2">
              <Summary icon={SparklesIcon} label="Mode" value={title(settings.remembered.mode)} />
              <Summary icon={SunMediumIcon} label="Brightness" value={`${Math.round(settings.remembered.brightness * 100)}%`} />
              <Summary icon={settings.remembered.blackout ? MoonIcon : PowerIcon} label="Power" value={settings.remembered.blackout ? "Off" : "On"} />
              <Summary
                icon={SparklesIcon}
                label={settings.remembered.mode === "solid" ? "Color" : "Animation"}
                value={settings.remembered.mode === "solid" ? settings.remembered.solid_color : title(settings.remembered.animation || "Default")}
              />
            </div>
            <p className="text-xs text-muted-foreground">In Independent mode, only changes made with All selected update this remembered state.</p>
          </CardContent>
        </Card>
      )}
    </SetupPage>
  )
}

export const StartupPanel = memo(StartupPanelView)
function Summary({ icon: Icon, label, value }: { icon: typeof PowerIcon; label: string; value: string }) {
  return <div className="min-w-0 rounded-xl bg-white/[0.035] p-3"><Icon className="mb-2 size-4 text-violet-200" /><p className="text-xs text-muted-foreground">{label}</p><p className="truncate text-sm font-medium capitalize">{value}</p></div>
}
